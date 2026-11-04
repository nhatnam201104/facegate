"""FaceGate demo - FastAPI app. Chay: python -m app.main"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .attendance import Shift
from .camera import Camera, can_read_video
from .clips import ClipRecorder
from .config import ROOT, load_config
from .db import DB
from .engines import create_engine
from .enrollment import EnrollError, process_enrollment, read_video_frames
from .gallery import Gallery
from .hrm import HRMWorker
from .liveness import create_liveness
from .pipeline import FULL_ZONE, Pipeline
from .records import router as records_router
from .state import bc, ctx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    bc.loop = asyncio.get_running_loop()
    cfg = load_config()
    ctx.cfg = cfg
    ctx.db = DB(cfg["_paths"]["db"])
    ctx.engine = create_engine(cfg)
    ctx.gallery = Gallery(ctx.db, ctx.engine.model_version)
    ctx.camera = Camera(cfg["camera"]["source"], cfg["camera"].get("loop_file", True)).start()
    ctx.hrm = HRMWorker(cfg, ctx.db, on_update=lambda i, s: bc({"type": "hrm", "id": i, "status": s}))
    ctx.shift = Shift.from_cfg(cfg)
    ctx.clips = ClipRecorder(cfg)
    ctx.pipeline = Pipeline(cfg, ctx.db, ctx.engine, ctx.gallery, ctx.camera, ctx.hrm, bc,
                            liveness=create_liveness(cfg), clips=ctx.clips).start()
    zone = _load_settings().get("motion_zone")
    if zone:
        try:
            ctx.pipeline.set_motion_zone(zone)
        except ValueError as e:
            log.warning("Bo qua vung phat hien trong settings.json: %s", e)
    ctx.engine_lock = ctx.pipeline.engine_lock
    ctx.threshold = ctx.pipeline.threshold
    log.info("FaceGate san sang: http://%s:%s", cfg["server"]["host"], cfg["server"]["port"])
    yield
    ctx.pipeline.stop()      # ket thuc cac track dang mo -> luu lich su
    ctx.clips.close()        # ghi xong cac clip con dang mo
    ctx.camera.stop()
    ctx.hrm.stop()


app = FastAPI(title="FaceGate demo", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
app.include_router(records_router)


@app.get("/")
def index():
    return FileResponse(ROOT / "app" / "static" / "index.html")


@app.get("/files/{path:path}")
def files(path: str):
    base = ctx.cfg["_paths"]["data"].resolve()
    p = (base / path).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


# ------------------------------------------------------------------ video
@app.get("/video.mjpg")
async def video():
    pl = ctx.pipeline

    def wait_frame(last: int):
        with pl.jpeg_cond:
            pl.jpeg_cond.wait_for(lambda: pl.jpeg_id != last, timeout=2.0)
            return pl.jpeg_id, pl.jpeg

    async def gen():
        last = -1
        while True:
            last, jpg = await run_in_threadpool(wait_frame, last)
            if jpg is None:
                await asyncio.sleep(0.2)
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n"

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue()
    bc.clients.add(q)
    try:
        await websocket.send_json({"type": "status", "status": ctx.pipeline.status()})
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=2.0)
            except asyncio.TimeoutError:
                msg = {"type": "status", "status": ctx.pipeline.status()}
            await websocket.send_json(msg)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bc.clients.discard(q)


@app.get("/api/status")
def status():
    return ctx.pipeline.status()


# ------------------------------------------------- vung phat hien chuyen dong
def _settings_path() -> Path:
    return ctx.cfg["_paths"]["data"] / "settings.json"


def _load_settings() -> dict:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        log.warning("Khong doc duoc settings.json: %s", e)
        return {}


def _save_setting(key: str, value) -> None:
    data = _load_settings()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    tmp = _settings_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_settings_path())


@app.get("/api/motion-zone")
def get_motion_zone():
    return {"zone": ctx.pipeline.motion_zone, "full": ctx.pipeline.motion_zone == FULL_ZONE}


@app.post("/api/motion-zone")
def set_motion_zone(zone: list = Body(..., embed=True)):
    try:
        ctx.pipeline.set_motion_zone(zone)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    _save_setting("motion_zone", ctx.pipeline.motion_zone)
    bc({"type": "status", "status": ctx.pipeline.status()})
    return get_motion_zone()


@app.delete("/api/motion-zone")
def reset_motion_zone():
    """Ve lai vung trong config.yaml (mac dinh: toan khung hinh)."""
    ctx.pipeline.set_motion_zone(ctx.cfg.get("power", {}).get("motion_zone") or FULL_ZONE)
    _save_setting("motion_zone", None)
    bc({"type": "status", "status": ctx.pipeline.status()})
    return get_motion_zone()


# ------------------------------------------------------------ video source
VIDEO_EXTS = {"mp4", "avi", "mov", "mkv", "webm", "m4v"}
_source_lock = threading.Lock()


def _switch_camera(source, label: str | None = None) -> None:
    """Dung nguon cu (nha webcam) roi moi chuyen pipeline sang nguon moi."""
    with _source_lock:
        old = ctx.camera
        old.stop(wait=5.0)
        cam = Camera(source, ctx.cfg["camera"].get("loop_file", True), label=label).start()
        ctx.camera = cam
        ctx.pipeline.set_camera(cam)
        # chi giu video nguon dang dung
        if old.kind == "file" and ctx.cfg["_paths"]["sources"] in Path(old.source).parents:
            Path(old.source).unlink(missing_ok=True)
    log.info("Doi nguon video -> %s", cam.label)
    bc({"type": "status", "status": ctx.pipeline.status()})


@app.get("/api/source")
def get_source():
    return ctx.camera.describe()


@app.post("/api/source")
async def set_source(kind: str = Form(...), value: str = Form(""), file: UploadFile | None = File(None)):
    label = None
    if kind == "webcam":
        if not value.strip().isdigit() or int(value) > 9:
            raise HTTPException(400, "Chỉ số webcam phải là số 0–9")
        source = int(value)
    elif kind == "rtsp":
        source = value.strip()
        if not source.lower().startswith(("rtsp://", "http://", "https://")):
            raise HTTPException(400, "URL phải bắt đầu bằng rtsp://, http:// hoặc https://")
    elif kind == "file":
        if file is None or not file.filename:
            raise HTTPException(400, "Chọn file video")
        ext = file.filename.rsplit(".", 1)[-1].lower()
        if ext not in VIDEO_EXTS:
            raise HTTPException(400, f"Định dạng không hỗ trợ (.{ext}). Dùng: {', '.join(sorted(VIDEO_EXTS))}")
        path = ctx.cfg["_paths"]["sources"] / f"{uuid.uuid4().hex}.{ext}"
        with open(path, "wb") as f:
            shutil.copyfileobj(file.file, f)
        if not await run_in_threadpool(can_read_video, str(path)):
            path.unlink(missing_ok=True)
            raise HTTPException(422, "Không đọc được video này. Thử file MP4 (H.264).")
        source, label = str(path), file.filename
    else:
        raise HTTPException(400, "kind phải là webcam | file | rtsp")
    await run_in_threadpool(_switch_camera, source, label)
    return ctx.camera.describe()


# -------------------------------------------------------------- enrollment
CODE_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")


def _check(code: str, name: str):
    if not CODE_RE.match(code):
        raise HTTPException(400, "Mã nhân viên chỉ gồm chữ, số, - _ . (tối đa 32 ký tự)")
    if not name.strip():
        raise HTTPException(400, "Thiếu họ tên")


def _finish(report_fn):
    try:
        report = report_fn()
    except EnrollError as e:
        raise HTTPException(422, str(e))
    ctx.gallery.reload()
    bc({"type": "employees"})
    return report


@app.post("/api/enroll")
async def enroll_video(code: str = Form(...), name: str = Form(...), file: UploadFile = File(...)):
    _check(code, name)
    ext = (file.filename or "v.mp4").rsplit(".", 1)[-1].lower()[:5]
    path = ctx.cfg["_paths"]["uploads"] / f"{uuid.uuid4().hex}.{ext}"
    with open(path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        frames = await run_in_threadpool(read_video_frames, path, float(ctx.cfg["enrollment"]["sample_fps"]))
        return await run_in_threadpool(_finish, lambda: process_enrollment(frames, code, name.strip(), ctx))
    except EnrollError as e:
        raise HTTPException(422, str(e))
    finally:
        if not ctx.cfg["enrollment"].get("keep_video", False):
            path.unlink(missing_ok=True)


@app.post("/api/enroll/camera")
async def enroll_camera(code: str = Form(...), name: str = Form(...), seconds: float = Form(0)):
    _check(code, name)
    secs = seconds or float(ctx.cfg["enrollment"]["camera_seconds"])
    if ctx.camera.kind == "file":
        raise HTTPException(409, "Nguồn hiện tại là file video. Chuyển về webcam/RTSP ở tab Trực tiếp để quay đăng ký.")
    if not ctx.camera.connected:
        raise HTTPException(503, "Camera chưa kết nối")
    frames = await run_in_threadpool(ctx.camera.record, min(secs, 20.0))
    fps = len(frames) / max(secs, 0.1)
    step = max(1, int(round(fps / float(ctx.cfg["enrollment"]["sample_fps"]))))
    frames = frames[::step]
    return await run_in_threadpool(_finish, lambda: process_enrollment(frames, code, name.strip(), ctx))


# -------------------------------------------------------------- employees
@app.get("/api/employees")
def employees():
    import json
    rows = ctx.db.list_employees(ctx.engine.model_version)
    for r in rows:
        r["report"] = json.loads(r["report"]) if r.get("report") else None
    return rows


@app.post("/api/employees/{emp_id}/{action}")
def employee_action(emp_id: int, action: str):
    status = {"approve": "active", "disable": "disabled", "pending": "pending"}.get(action)
    if not status:
        raise HTTPException(400, "action không hợp lệ")
    ctx.db.set_status(emp_id, status)
    ctx.gallery.reload()
    bc({"type": "employees"})
    return {"ok": True}


@app.delete("/api/employees/{emp_id}")
def employee_delete(emp_id: int):
    emp = ctx.db.q("SELECT code FROM employees WHERE id=?", (emp_id,))
    if not emp:
        raise HTTPException(404)
    # Xoa du lieu khuon mat (sinh trac hoc). Lich su cham cong/xuat hien giu lai theo ma + ten.
    ctx.db.delete_employee(emp_id)
    shutil.rmtree(ctx.cfg["_paths"]["enroll"] / emp[0]["code"], ignore_errors=True)
    ctx.gallery.reload()     # pipeline thay version doi -> bo danh tinh cua track dang dung truoc camera
    bc({"type": "employees"})
    return {"ok": True}


# ----------------------------------------------------------------- events
@app.get("/api/events")
def events(limit: int = 200):
    return ctx.db.list_events(min(limit, 2000))


LABELS = {"correct", "wrong", "unknown_employee", "unknown_stranger", "spoof_attack", "spoof_real", None}


@app.post("/api/events/{ev_id}/label")
def label(ev_id: int, value: str = Form("")):
    v = value or None
    if v not in LABELS:
        raise HTTPException(400)
    ctx.db.label_event(ev_id, v)
    return {"ok": True}


@app.get("/api/metrics")
def metrics():
    evs = ctx.db.list_events(100000)
    known = [e for e in evs if e["kind"] == "checkin"]
    unknown = [e for e in evs if e["kind"] == "unknown"]
    spoof = [e for e in evs if e["kind"] == "spoof"]
    spoof_lab = [e for e in spoof if e["label"] in ("spoof_attack", "spoof_real")]
    lab_known = [e for e in known if e["label"] in ("correct", "wrong")]
    wrong = sum(e["label"] == "wrong" for e in known)
    unk_emp = sum(e["label"] == "unknown_employee" for e in unknown)
    lab_unknown = [e for e in unknown if e["label"] in ("unknown_employee", "unknown_stranger")]
    lat = sorted(e["latency_ms"] for e in known if e["latency_ms"] is not None)
    attempts_emp = len([e for e in known if e["label"] == "correct"]) + unk_emp
    return {
        "events_total": len(evs), "recognized": len(known), "unknown": len(unknown), "spoof": len(spoof),
        "labeled": len(lab_known) + len(lab_unknown) + len(spoof_lab),
        # Nguoi that bi chan nham la gia mao (trong cac canh bao gia mao da gan nhan)
        "spoof_false_rate": round(sum(e["label"] == "spoof_real" for e in spoof_lab) / len(spoof_lab), 4)
        if spoof_lab else None,
        # Ty le nhan nham trong cac su kien da gan nhan (false accept)
        "false_accept_rate": round(wrong / len(lab_known), 4) if lab_known else None,
        # Nhan vien that nhung he thong tra Unknown (false reject)
        "false_reject_rate": round(unk_emp / attempts_emp, 4) if attempts_emp else None,
        "unknown_rate": round(len(unknown) / (len(known) + len(unknown)), 4) if known or unknown else None,
        "latency_ms_p50": lat[len(lat) // 2] if lat else None,
        "latency_ms_p90": lat[int(len(lat) * 0.9)] if lat else None,
        "hrm": {s: sum(e["hrm_status"] == s for e in evs) for s in ("pending", "sent", "failed", "skipped")},
    }


@app.get("/api/events.csv")
def events_csv():
    evs = ctx.db.list_events(100000)
    buf = io.StringIO()
    cols = ["id", "ts", "time", "camera", "kind", "att_type", "code", "name", "score", "margin", "liveness",
            "direction", "latency_ms", "frames", "model_version", "hrm_status", "label", "snapshot"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for e in evs:
        e["time"] = datetime.fromtimestamp(e["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        w.writerow(e)
    data = "﻿" + buf.getvalue()  # BOM de Excel doc dung tieng Viet
    return Response(data, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=events_{int(time.time())}.csv"})


def main():
    cfg = load_config()
    uvicorn.run("app.main:app", host=cfg["server"]["host"], port=int(cfg["server"]["port"]), log_level="info")


if __name__ == "__main__":
    main()
