"""FaceGate demo - FastAPI app. Chay: python -m app.main"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import cv2
import uvicorn
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .attendance import Shift, greeting
from .camera import Camera, can_read_video
from .clips import ClipRecorder
from .config import ROOT, load_config
from .db import DB
from .engines import create_engine
from .enrollment import EnrollError, backfill_legacy_embeddings, process_enrollment, read_video_frames
from .gallery import Gallery
from .guided import NO_FRONTAL_MSG, GuidedSession, known_frontal_embeddings
from .hrm import HRMWorker
from .ipc import IPCServer
from .ledger import hold_clip_if_pending_audit, record_auto_checkin
from .liveness import create_liveness
from .media_clips import cleanup_clips_safe, fetch_playback_clip, write_signed_sidecar
from .pipeline import FULL_ZONE, Pipeline
from .pose3d import PoseEstimator3D
from .records import router as records_router
from .security import BiometricCipher, issue_rbac_token, require_role
from .state import bc, ctx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


TRACK_EVENT_TTL_SEC = 3600.0


def _track_event_store() -> dict:
    store = getattr(ctx, "track_event_ids", None)
    if store is None:
        store = ctx.track_event_ids = {}
    return store


def _remember_track_event(camera_id: str, track_id: int, event_id: int, employee_id: int, ts: float) -> None:
    """Nho event check-in cua 1 track de gan vao appearance khi track dong (CR10)."""
    store = _track_event_store()
    # Worker khoi dong lai thi track_id quay ve 0 -> bo cac muc cu de khong gan nham
    for k in [k for k, v in store.items() if ts - v[2] > TRACK_EVENT_TTL_SEC]:
        del store[k]
    store[(camera_id, track_id)] = (int(event_id), int(employee_id), float(ts))


def _pop_track_event(camera_id: str, track_id: int, employee_id) -> int | None:
    item = _track_event_store().pop((camera_id, track_id), None)
    if item is None or employee_id is None or int(employee_id) != item[1]:
        return None
    return item[0]


def _handle_worker_ipc(msg: dict) -> None:
    mtype = msg.get("type")
    now = time.time()
    if mtype == "heartbeat":
        ctx.last_worker_hb = now
        ctx.worker_hb = msg
        return
    if mtype == "frame_overlay":
        ctx.last_worker_hb = now
        tid_objs = msg.get("objects") or []
        for o in tid_objs:
            tid = int(o.get("track_id", 0))
            buf = ctx.track_timelines.setdefault(tid, [])
            buf.append({"ts_ns": msg.get("ts_server_ns", time.time_ns()), "obj": o})
            if len(buf) > 300:
                del buf[:-300]
        bc({**msg, "ws_send_mono_ms": round(time.monotonic() * 1000.0, 2)})
        return
    if mtype == "track_event":
        ek = msg.get("event_kind")
        ts_sec = float(msg.get("ts_server_ns", time.time_ns())) / 1e9
        if ek == "checkin":
            ev = dict(
                ts=ts_sec, camera=msg.get("camera_id", "gate-1"),
                employee_id=msg["employee_id"], code=msg["code"], name=msg["name"],
                score=msg.get("score", 0.0), margin=msg.get("margin", 0.0),
                direction="IN", latency_ms=0.0, frames=2, snapshot=None,
                model_version=ctx.engine.model_version, hrm_status="skipped",
                kind="checkin", att_type="in", liveness=1.0,
                verification_tier=msg.get("verification_tier", "verified"),
                yaw=msg.get("yaw", 0.0),
            )
            ev["id"] = ctx.db.add_event(**ev)
            record_auto_checkin(
                ctx.db, employee_id=int(msg["employee_id"]), code=str(msg["code"]),
                ts=ts_sec, verification_tier=str(msg.get("verification_tier", "verified")),
                score=float(msg.get("score", 1.0)),
                tau_threshold=float(ctx.cfg["decision"]["tau_threshold"]),
                camera_id=str(msg.get("camera_id", "gate-1")),
                event_id=ev["id"], audit_cfg=ctx.cfg.get("audit_and_safety"),
            )
            # Appearance chi duoc tao khi track dong -> nho event_id de lien ket va giu clip neu pending_audit (CR10)
            _remember_track_event(str(msg.get("camera_id", "gate-1")), int(msg.get("track_id", 0)),
                                  ev["id"], int(msg["employee_id"]), ts_sec)
            ctx.hrm.notify()
            bc({"type": "event", "event": ev, "greet": greeting(ts_sec, ctx.shift)})
        elif ek == "spoof":
            ev = dict(
                ts=ts_sec, camera=msg.get("camera_id", "gate-1"),
                employee_id=None, code=None, name="Không xác định",
                score=0.0, margin=0.0, direction=None, latency_ms=0.0, frames=1,
                snapshot=None, model_version=ctx.engine.model_version,
                hrm_status="skipped", kind="spoof", att_type=None, liveness=0.0,
                verification_tier="rejected_spoof",
            )
            ev["id"] = ctx.db.add_event(**ev)
            bc({"type": "event", "event": ev})
        elif ek == "appearance_closed":
            tid = int(msg.get("track_id", 0))
            hist = ctx.track_timelines.pop(tid, [])
            linked_event_id = _pop_track_event(str(msg.get("camera_id", "gate-1")), tid, msg.get("employee_id"))
            kind = "spoof" if msg.get("spoof") else ("employee" if msg.get("employee_id") else "unknown")
            app_id = ctx.db.add_appearance(
                kind=kind, employee_id=msg.get("employee_id"), code=msg.get("code"), name=msg.get("name"),
                start_ts=float(msg.get("start_ts", now - 2.0)), end_ts=float(msg.get("end_ts", now)),
                score=0.0, liveness=None, snapshot=None, event_id=linked_event_id,
                camera=msg.get("camera_id", "gate-1"),
                verification_tier=msg.get("verification_tier"),
                min_abs_yaw=msg.get("min_abs_yaw", 0.0),
                legal_hold=1 if kind == "spoof" else 0,
                legal_hold_reason="unreviewed_spoof" if kind == "spoof" else None,
            )
            hold_clip_if_pending_audit(ctx.db, app_id, linked_event_id)
            threading.Thread(
                target=_archive_playback_clip_bg,
                args=(app_id, msg, hist),
                daemon=True,
            ).start()


def _archive_playback_clip_bg(app_id: int, msg: dict, hist: list[dict]) -> None:
    c_cfg = ctx.cfg.get("clips", {})
    if not c_cfg.get("enabled", True):
        return
    pre = float(c_cfg.get("pre_seconds", 2.0))
    post = float(c_cfg.get("post_seconds", 1.5))
    start_ts = float(msg.get("start_ts", time.time() - 2.0))
    end_ts = float(msg.get("end_ts", time.time()))
    day = datetime.fromtimestamp(start_ts).strftime("%Y%m%d")
    dst_mp4 = ctx.cfg["_paths"]["clips"] / day / f"{datetime.fromtimestamp(start_ts):%H%M%S}_{app_id}.mp4"
    pb_url = ctx.cfg["camera"].get("playback_url", "http://127.0.0.1:9996")
    if fetch_playback_clip(pb_url, "gate1_main", start_ts, end_ts, pre, post, dst_mp4):
        clip_start_ns = int((start_ts - pre) * 1e9)
        timeline = [
            {"offset_ms": max(0, int((h["ts_ns"] - clip_start_ns) / 1e6)), "objects": [h["obj"]]}
            for h in hist
        ]
        sc_path, sha, _ = write_signed_sidecar(
            dst_mp4, ctx.cfg["_paths"]["data"], app_id,
            camera_id=msg.get("camera_id", "gate-1"),
            clip_start_server_ns=clip_start_ns,
            event_offset_ms=int(pre * 1000),
            timeline=timeline, cfg=ctx.cfg,
        )
        data_root = ctx.cfg["_paths"]["data"]
        ctx.db.set_clip(
            app_id,
            dst_mp4.relative_to(data_root).as_posix(),
            clip_sha256=sha,
            sidecar_path=sc_path.relative_to(data_root).as_posix(),
        )
        bc({"type": "appearance", "id": app_id})


@asynccontextmanager
async def lifespan(app: FastAPI):
    bc.loop = asyncio.get_running_loop()
    cfg = load_config()
    ctx.cfg = cfg
    ctx.cipher = BiometricCipher.from_env_or_cfg(cfg)
    ctx.db = DB(cfg["_paths"]["db"], database_url=cfg["storage"].get("database_url", ""), cipher=ctx.cipher)
    ctx.engine = create_engine(cfg)
    # Session suy luan rieng cho Enrollment de khong bao gio khoa cong cham cong (P6, T028)
    ctx.enroll_engine = create_engine(cfg)
    ctx.enroll_lock = threading.Lock()
    ctx.pose = PoseEstimator3D(cfg["_paths"]["models"])
    ctx.gallery = Gallery(ctx.db, ctx.engine.model_version)
    ctx.camera = Camera(cfg["camera"]["source"], cfg["camera"].get("loop_file", True)).start()
    ctx.hrm = HRMWorker(cfg, ctx.db, on_update=lambda i, s: bc({"type": "hrm", "id": i, "status": s}))
    ctx.shift = Shift.from_cfg(cfg)
    ctx.clips = ClipRecorder(cfg)
    isolated = bool(cfg["pipeline"].get("isolated_worker") or os.environ.get("FACEGATE_ISOLATED_WORKER") == "1")
    # Che do isolated: worker_perception la bo nhan dien duy nhat; Pipeline o day chi cap khung xem truoc (CR2).
    ctx.pipeline = Pipeline(cfg, ctx.db, ctx.engine, ctx.gallery, ctx.camera, ctx.hrm, bc,
                            liveness=None if isolated else create_liveness(cfg),
                            clips=None if isolated else ctx.clips,
                            recognize=not isolated).start()
    zone = _load_settings().get("motion_zone")
    if zone:
        try:
            ctx.pipeline.set_motion_zone(zone)
        except ValueError as e:
            log.warning("Bo qua vung phat hien trong settings.json: %s", e)
    ctx.engine_lock = ctx.pipeline.engine_lock
    ctx.threshold = ctx.pipeline.threshold
    ctx.track_timelines = {}
    ctx.track_event_ids = {}
    ctx.last_worker_hb = time.time()
    ctx.worker_hb = {}
    ctx.disk_status = {"disk_warning": False, "free_disk_gb": 99.0}

    sock_path = cfg["_paths"]["run"] / "perception.sock"
    ctx.ipc = IPCServer(sock_path=sock_path, port=0, on_message=_handle_worker_ipc)
    await ctx.ipc.start()

    stop_sup = asyncio.Event()

    async def _supervise_worker_and_disk() -> None:
        proc: subprocess.Popen | None = None
        last_disk_chk = 0.0
        try:
            while not stop_sup.is_set():
                if isolated and (proc is None or proc.poll() is not None):
                    if proc is not None:
                        log.warning("worker_perception thoat voi ma %s -> tu khoi dong lai sau 1s", proc.returncode)
                        await asyncio.sleep(1.0)
                    proc = subprocess.Popen([
                        sys.executable, "-m", "app.worker_perception",
                        "--sock", str(sock_path), "--port", str(ctx.ipc.bound_port),
                    ])
                    ctx.worker_proc = proc
                if time.time() - last_disk_chk >= 60.0:
                    last_disk_chk = time.time()
                    ctx.disk_status = await asyncio.to_thread(
                        cleanup_clips_safe,
                        ctx.db, cfg["_paths"]["data"],
                        float(cfg["clips"].get("retention_days", 30)),
                        float(cfg["audit_and_safety"].get("min_free_disk_gb", 2.0)),
                    )
                await asyncio.sleep(2.0)
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()

    sup_task = asyncio.create_task(_supervise_worker_and_disk())
    backfill_stop = threading.Event()
    bf_task = asyncio.create_task(asyncio.to_thread(backfill_legacy_embeddings, ctx, backfill_stop))

    log.info("FaceGate v4 san sang: http://%s:%s", cfg["server"]["host"], cfg["server"]["port"])
    yield
    stop_sup.set()
    backfill_stop.set()
    sup_task.cancel()
    bf_task.cancel()
    await ctx.ipc.close()
    ctx.pipeline.stop()
    ctx.clips.close()
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


@app.get("/video_raw.mjpg")
async def video_raw():
    """Luong camera sach (khong khung tracker/nhan/HUD): dung cho man hinh dang ky co huong dan."""
    pcfg = ctx.cfg["pipeline"]
    interval = 1.0 / max(1.0, float(pcfg["stream_fps"]))
    width = float(pcfg["stream_width"])

    def next_jpeg(last: int):
        deadline = time.time() + 2.0
        while time.time() < deadline:
            fid, frame = ctx.camera.read()      # doc moi lan: nguon camera co the bi doi o tab Truc tiep
            if frame is not None and fid != last:
                h, w = frame.shape[:2]
                s = min(1.0, width / w)
                view = cv2.resize(frame, (int(w * s), int(h * s))) if s < 1 else frame
                ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if ok:
                    return fid, buf.tobytes()
            time.sleep(0.02)
        return last, None

    async def gen():
        last = -1
        while True:
            t0 = time.time()
            last, jpg = await run_in_threadpool(next_jpeg, last)
            if jpg is None:
                await asyncio.sleep(0.2)
                continue
            yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n"
            await asyncio.sleep(max(0.0, interval - (time.time() - t0)))

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
    st = ctx.pipeline.status()
    st["whep_url"] = ctx.cfg["camera"].get("whep_url", "/media/gate1_main/whep")
    st["overlay_offset_ms"] = ctx.cfg["camera"].get("overlay_offset_ms", 0.0)
    st["model_profile"] = ctx.cfg["camera"].get("model_profile", "tapo_c310")
    return st


@app.get("/api/health")
def health():
    """Kiem tra suc khoe van hanh Fail-safe (H15, H30): bao dong trong <= 5 giay khi mat camera / worker / ffmpeg / day dia."""
    cfg = ctx.cfg
    hb_timeout = float(cfg.get("audit_and_safety", {}).get("heartbeat_timeout_sec", 5.0))
    isolated = bool(cfg["pipeline"].get("isolated_worker") or os.environ.get("FACEGATE_ISOLATED_WORKER") == "1")
    worker_alive = (time.time() - getattr(ctx, "last_worker_hb", 0.0) <= hb_timeout) if isolated else ctx.pipeline._thread.is_alive()
    whb = getattr(ctx, "worker_hb", {}) or {}
    cam_ok = bool(whb.get("stream1_connected", True)) if (isolated and whb) else bool(ctx.camera.connected and not ctx.camera.black)
    ffmpeg_ok = bool(whb.get("ffmpeg_ingest_alive", True)) if (isolated and whb) else True
    dstat = getattr(ctx, "disk_status", {}) or {}
    free_gb = round(shutil.disk_usage(cfg["_paths"]["data"]).free / (1024 ** 3), 2)
    min_gb = float(cfg.get("audit_and_safety", {}).get("min_free_disk_gb", 2.0))
    hold_cnt = ctx.db.q("SELECT COUNT(*) AS n FROM appearances WHERE legal_hold=1")[0]["n"]
    ok = bool(cam_ok and ffmpeg_ok and worker_alive and free_gb >= min_gb)
    return {
        "ok": ok,
        "camera_stream1": cam_ok,
        "ffmpeg_ingest_alive": ffmpeg_ok,
        "perception_worker_alive": worker_alive,
        "free_disk_gb": free_gb,
        "disk_low": free_gb < min_gb or bool(dstat.get("disk_warning")),
        "legal_hold_clips_count": hold_cnt,
        "fallback_mode_active": not ok,
    }


@app.post("/api/auth/login")
def auth_login(username: str = Form(...), password: str = Form(...)):
    sec = ctx.cfg.get("security") or {}
    users = sec.get("users") or {}
    u = users.get(username)
    if not u or str(u.get("password", "")) != password:
        raise HTTPException(401, "Sai tài khoản hoặc mật khẩu")
    secret = os.environ.get("FACEGATE_AUTH_SECRET") or sec.get("jwt_secret") or "dev-secret"
    role = str(u.get("role", "viewer"))
    tok = issue_rbac_token(username, role, secret)
    ctx.db.add_audit_log(f"{role}:{username}", "login", "auth", username, {})
    return {"ok": True, "token": tok, "role": role, "username": username}


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
        v = value.strip()
        if v.isdigit() and int(v) <= 9:
            source = int(v)
        elif re.match(r"^/dev/video\d{1,2}$", v):
            source = v
        else:
            raise HTTPException(400, "Chỉ số webcam phải là số 0–9 hoặc /dev/videoN")
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


@app.post("/api/enroll/guided/start")
async def enroll_guided_start(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    profile_90: bool = Form(True),
    supplement_only: bool = Form(False),
):
    """Bat dau phien dang ky co huong dan 7 goc (hoac bo sung goc nghieng supplement_only) tren session rieng (T028)."""
    require_role(request, ctx.cfg, min_role="viewer")
    _check(code, name)
    if ctx.camera.kind == "file":
        raise HTTPException(409, "Nguồn hiện tại là file video. Chuyển về webcam/RTSP ở tab Trực tiếp để đăng ký.")
    if not ctx.camera.connected:
        raise HTTPException(503, "Camera chưa kết nối")
    old = getattr(ctx, "guided", None)
    if old is not None and old.state == "running":
        old.cancel()
        old._thread.join(timeout=2.0)
    if supplement_only:
        eng = getattr(ctx, "enroll_engine", None) or ctx.engine
        if not known_frontal_embeddings(ctx.db, code, eng.model_version):
            raise HTTPException(409, NO_FRONTAL_MSG)
    ctx.guided = GuidedSession(ctx, code, name.strip(), profile_90=profile_90, supplement_only=supplement_only).start()
    return ctx.guided.snapshot()


@app.get("/api/enroll/guided/status")
def enroll_guided_status():
    g = getattr(ctx, "guided", None)
    if g is None:
        raise HTTPException(404, "Chưa có phiên đăng ký nào")
    return g.snapshot()


@app.post("/api/enroll/guided/cancel")
def enroll_guided_cancel():
    g = getattr(ctx, "guided", None)
    if g is not None:
        g.cancel()
    return {"ok": True}


# -------------------------------------------------------------- employees
@app.get("/api/employees")
def employees():
    import json
    rows = ctx.db.list_employees(ctx.engine.model_version)
    for r in rows:
        r["report"] = json.loads(r["report"]) if r.get("report") else None
    return rows


@app.post("/api/employees/{emp_id}/{action}")
async def employee_action(emp_id: int, action: str, request: Request):
    actor = require_role(request, ctx.cfg, min_role="hr")
    status = {"approve": "active", "disable": "disabled", "pending": "pending"}.get(action)
    if not status:
        raise HTTPException(400, "action không hợp lệ")
    ctx.db.set_status(emp_id, status)
    ctx.db.add_audit_log(actor, f"employee_{action}", "employees", str(emp_id), {"status": status})
    ctx.gallery.reload()
    if getattr(ctx, "ipc", None) is not None:
        await ctx.ipc.send_command({"cmd": "reload_gallery", "version": ctx.gallery.version})
    bc({"type": "employees"})
    return {"ok": True}


@app.delete("/api/employees/{emp_id}")
async def employee_delete(emp_id: int, request: Request):
    actor = require_role(request, ctx.cfg, min_role="hr")
    emp = ctx.db.q("SELECT code FROM employees WHERE id=?", (emp_id,))
    if not emp:
        raise HTTPException(404)
    # Xoa du lieu khuon mat (sinh trac hoc). Lich su cham cong/xuat hien giu lai theo ma + ten.
    ctx.db.delete_employee(emp_id)
    ctx.db.add_audit_log(actor, "delete_biometrics", "employees", str(emp_id), {"code": emp[0]["code"]})
    shutil.rmtree(ctx.cfg["_paths"]["enroll"] / emp[0]["code"], ignore_errors=True)
    ctx.gallery.reload()
    if getattr(ctx, "ipc", None) is not None:
        await ctx.ipc.send_command({"cmd": "reload_gallery", "version": ctx.gallery.version})
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
