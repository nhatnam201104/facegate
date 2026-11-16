"""API bang cong + lich su xuat hien (kem clip video)."""
from __future__ import annotations

import csv
import io
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from .attendance import MAX_DAYS, day_range, timesheet, today_stats
from .ledger import list_audit_queue, record_manual_checkin, resolve_audit_item, revoke_checkin
from .media_clips import render_burnin_mp4
from .security import require_role
from .state import bc, ctx

router = APIRouter()

KINDS = {"employee", "unknown", "spoof"}
MAX_ZIP_CLIPS = 200
STATUS_VI = {"ok": "Đúng giờ", "late": "Đi muộn", "absent": "Vắng", "not_arrived": "Chưa đến"}


def _range(date_from: str | None, date_to: str | None) -> tuple[float, float]:
    try:
        d0 = date.fromisoformat(date_from) if date_from else date.today()
        d1 = date.fromisoformat(date_to) if date_to else d0
    except ValueError:
        raise HTTPException(400, "Ngày phải có dạng YYYY-MM-DD")
    if d1 < d0:
        d0, d1 = d1, d0
    if (d1 - d0).days >= MAX_DAYS:
        raise HTTPException(400, f"Khoảng ngày tối đa {MAX_DAYS} ngày")
    return day_range(d0)[0], day_range(d1)[1]


def _fmt(ts: float | None, f: str = "%H:%M:%S") -> str:
    return datetime.fromtimestamp(ts).strftime(f) if ts else ""


def _csv_response(rows: list[dict], cols: list[tuple[str, str]], name: str) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([title for _, title in cols])
    for r in rows:
        w.writerow([r.get(k, "") for k, _ in cols])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",  # BOM cho Excel
                    headers={"Content-Disposition": f"attachment; filename={name}"})


# ---------------------------------------------------------------- cham cong
def _timesheet(date_from, date_to, code):
    try:
        return timesheet(ctx.db, date_from, date_to, ctx.shift, code or None)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/attendance")
def attendance(date_from: str | None = None, date_to: str | None = None, code: str | None = None):
    return _timesheet(date_from, date_to, code)


@router.get("/api/attendance.csv")
def attendance_csv(date_from: str | None = None, date_to: str | None = None, code: str | None = None):
    ts = _timesheet(date_from, date_to, code)
    rows = [{**r, "in": _fmt(r["check_in"]), "status_vi": STATUS_VI.get(r["status"], r["status"])}
            for r in ts["rows"]]
    cols = [("date", "Ngày"), ("code", "Mã NV"), ("name", "Họ tên"), ("in", "Giờ check-in"),
            ("late_min", "Phút đi muộn"), ("status_vi", "Trạng thái")]
    return _csv_response(rows, cols, f"cham_cong_{ts['from']}_{ts['to']}.csv")


@router.get("/api/attendance/today")
def attendance_today():
    return today_stats(ctx.db, ctx.shift)


@router.get("/api/attendance/audit-queue")
def attendance_audit_queue(request: Request, date_from: str | None = None, date_to: str | None = None):
    require_role(request, ctx.cfg, min_role="hr")
    return list_audit_queue(ctx.db, date_from, date_to)


@router.post("/api/attendance/revoke")
def attendance_revoke(request: Request, payload: dict = Body(...)):
    actor = require_role(request, ctx.cfg, min_role="hr")
    try:
        lid = revoke_checkin(
            ctx.db,
            target_ledger_id=int(payload["target_ledger_id"]),
            idempotency_key=str(payload.get("idempotency_key") or f"rev-{payload['target_ledger_id']}-{int(datetime.now().timestamp())}"),
            reason=str(payload.get("reason") or ""),
            actor=actor,
            ip_addr=request.client.host if request.client else None,
        )
    except LookupError as e:
        raise HTTPException(404, str(e))
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    ctx.hrm.notify()
    bc({"type": "event", "event": {"kind": "revoked", "ts": datetime.now().timestamp()}})
    return {"ok": True, "revoke_ledger_id": lid}


@router.post("/api/attendance/manual")
def attendance_manual(request: Request, payload: dict = Body(...)):
    actor = require_role(request, ctx.cfg, min_role="hr")
    try:
        lid = record_manual_checkin(
            ctx.db,
            employee_code=str(payload["employee_code"]),
            work_date=str(payload["work_date"]),
            effective_time=str(payload["effective_time"]),
            idempotency_key=str(payload.get("idempotency_key") or f"manual-{payload['employee_code']}-{payload['work_date']}"),
            reason=str(payload.get("reason") or ""),
            actor=actor,
            ip_addr=request.client.host if request.client else None,
        )
    except LookupError as e:
        raise HTTPException(404, str(e))
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    ctx.hrm.notify()
    return {"ok": True, "ledger_id": lid}


@router.post("/api/attendance/audit-resolve/{ledger_id}")
def attendance_audit_resolve(ledger_id: int, request: Request, payload: dict = Body(...)):
    actor = require_role(request, ctx.cfg, min_role="hr")
    try:
        res = resolve_audit_item(
            ctx.db, ledger_id,
            confirm=bool(payload.get("confirm", True)),
            reason=str(payload.get("reason") or ""),
            actor=actor,
            ip_addr=request.client.host if request.client else None,
        )
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    ctx.hrm.notify()
    return res


# ---------------------------------------------------------- lich su xuat hien
@router.get("/api/people")
def people():
    """Danh sach de loc: nhan vien hien tai + nguoi da tung xuat hien (ke ca da xoa)."""
    cur = {e["code"]: e["name"] for e in ctx.db.q("SELECT code, name FROM employees")}
    out = [{"code": c, "name": n, "deleted": False} for c, n in cur.items()]
    out += [{"code": p["code"], "name": p["name"], "deleted": True}
            for p in ctx.db.people_seen() if p["code"] not in cur]
    return sorted(out, key=lambda p: (p["deleted"], p["name"] or ""))


def _appearances(date_from, date_to, code, kind, limit) -> list[dict]:
    if kind and kind not in KINDS:
        raise HTTPException(400, "kind phải là employee | unknown | spoof")
    t0, t1 = _range(date_from, date_to)
    rows = ctx.db.list_appearances(t0, t1, code or None, kind or None, min(max(limit, 1), 2000))
    data = ctx.cfg["_paths"]["data"]
    for r in rows:
        r["duration"] = round(r["end_ts"] - r["start_ts"], 1)
        r["clip_ok"] = bool(r["clip"]) and (data / r["clip"]).is_file()
        sc_rel = r.get("sidecar_path") or (Path(r["clip"]).with_suffix(".meta.json").as_posix() if r.get("clip") else None)
        r["sidecar_ok"] = bool(sc_rel) and (data / sc_rel).is_file()
        r["sidecar"] = sc_rel if r["sidecar_ok"] else None
    return rows


@router.post("/api/appearances/{app_id}/legal-hold")
def toggle_appearance_legal_hold(app_id: int, request: Request, payload: dict = Body(...)):
    actor = require_role(request, ctx.cfg, min_role="hr")
    rows = ctx.db.q("SELECT id FROM appearances WHERE id=?", (app_id,))
    if not rows:
        raise HTTPException(404, "Không tìm thấy bản ghi lịch sử")
    hold = bool(payload.get("hold", True))
    reason = str(payload.get("reason") or "").strip() or None
    if hold and not reason:
        raise HTTPException(400, "Phải nhập lý do khi bật khóa bảo vệ clip (legal_hold)")
    ctx.db.set_legal_hold(app_id, hold, reason)
    ctx.db.add_audit_log(actor, "set_legal_hold", "appearances", str(app_id),
                         {"hold": hold, "reason": reason}, ip_addr=request.client.host if request.client else None)
    return {"ok": True, "id": app_id, "legal_hold": int(hold), "legal_hold_reason": reason}


@router.post("/api/appearances/{app_id}/export-burnin")
def export_appearance_burnin(app_id: int, request: Request):
    actor = require_role(request, ctx.cfg, min_role="hr")
    rows = ctx.db.q("SELECT * FROM appearances WHERE id=?", (app_id,))
    if not rows or not rows[0].get("clip"):
        raise HTTPException(404, "Không tìm thấy clip gốc cho lượt xuất hiện này")
    r = rows[0]
    data_root: Path = ctx.cfg["_paths"]["data"]
    mp4_p = data_root / r["clip"]
    sc_rel = r.get("sidecar_path") or Path(r["clip"]).with_suffix(".meta.json").as_posix()
    sc_p = data_root / sc_rel
    tmp_out = ctx.cfg["_paths"]["uploads"] / f"burnin_{app_id}_{int(datetime.now().timestamp())}.mp4"
    try:
        render_burnin_mp4(mp4_p, sc_p, tmp_out, cfg=ctx.cfg)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Không thể kết xuất MP4 burn-in: {e}")
    ctx.db.add_audit_log(actor, "export_burnin_mp4", "appearances", str(app_id),
                         {"clip": r["clip"]}, ip_addr=request.client.host if request.client else None)
    return FileResponse(
        tmp_out, media_type="video/mp4", filename=f"bang_chung_{r.get('code') or r['kind']}_{app_id}.mp4",
        background=BackgroundTask(lambda: tmp_out.unlink(missing_ok=True)),
    )


@router.get("/api/appearances")
def appearances(date_from: str | None = None, date_to: str | None = None, code: str | None = None,
                kind: str | None = None, limit: int = 300):
    return _appearances(date_from, date_to, code, kind, limit)


@router.get("/api/appearances.zip")
def appearances_zip(date_from: str | None = None, date_to: str | None = None, code: str | None = None,
                    kind: str | None = None):
    rows = [r for r in _appearances(date_from, date_to, code, kind, MAX_ZIP_CLIPS) if r["clip_ok"]]
    if not rows:
        raise HTTPException(404, "Không có clip nào trong khoảng đã chọn")
    data: Path = ctx.cfg["_paths"]["data"]
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir=ctx.cfg["_paths"]["uploads"])
    tmp.close()
    index = io.StringIO()
    w = csv.writer(index)
    w.writerow(["file", "bat_dau", "ket_thuc", "giay", "loai", "ma", "ho_ten", "score", "nguoi_that"])
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED) as z:   # video da nen san
        for r in rows:
            src = data / r["clip"]
            who = r["code"] or r["kind"]
            arc = f"{_fmt(r['start_ts'], '%Y%m%d_%H%M%S')}_{who}_{r['id']}{src.suffix}"
            z.write(src, arc)
            w.writerow([arc, _fmt(r["start_ts"], "%Y-%m-%d %H:%M:%S"), _fmt(r["end_ts"], "%Y-%m-%d %H:%M:%S"),
                        r["duration"], r["kind"], r["code"] or "", r["name"] or "", r["score"], r["liveness"]])
        z.writestr("danh_sach.csv", "﻿" + index.getvalue())
    name = f"lich_su_{code or kind or 'tat_ca'}_{date_from or date.today()}.zip"
    return FileResponse(tmp.name, media_type="application/zip", filename=name,
                        background=BackgroundTask(lambda: Path(tmp.name).unlink(missing_ok=True)))
