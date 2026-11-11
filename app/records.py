"""API bang cong + lich su xuat hien (kem clip video)."""
from __future__ import annotations

import csv
import io
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from .attendance import MAX_DAYS, day_range, timesheet, today_stats
from .state import ctx

router = APIRouter()

KINDS = {"employee", "unknown", "spoof"}
TIERS = {"verified", "profile_verified", "rejected_spoof", "unverified"}
MAX_ZIP_CLIPS = 200
STATUS_VI = {"ok": "Đúng giờ", "late": "Đi muộn", "absent": "Vắng", "not_arrived": "Chưa đến"}
TIER_VI = {
    "verified": "Chính diện",
    "profile_verified": "Góc nghiêng (có clip)",
    "rejected_spoof": "Chặn giả mạo",
    "unverified": "Chưa xác thực",
}


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
    rows = [
        {
            **r,
            "in": _fmt(r["check_in"]),
            "status_vi": STATUS_VI.get(r["status"], r["status"]),
            "tier_vi": TIER_VI.get(r.get("verification_tier") or "", "—") if r["check_in"] else "",
        }
        for r in ts["rows"]
    ]
    cols = [
        ("date", "Ngày"), ("code", "Mã NV"), ("name", "Họ tên"), ("in", "Giờ check-in"),
        ("late_min", "Phút đi muộn"), ("status_vi", "Trạng thái"), ("tier_vi", "Mức xác thực"),
    ]
    return _csv_response(rows, cols, f"cham_cong_{ts['from']}_{ts['to']}.csv")


@router.get("/api/attendance/today")
def attendance_today():
    return today_stats(ctx.db, ctx.shift)


# ---------------------------------------------------------- lich su xuat hien
@router.get("/api/people")
def people():
    """Danh sach de loc: nhan vien hien tai + nguoi da tung xuat hien (ke ca da xoa)."""
    cur = {e["code"]: e["name"] for e in ctx.db.q("SELECT code, name FROM employees")}
    out = [{"code": c, "name": n, "deleted": False} for c, n in cur.items()]
    out += [{"code": p["code"], "name": p["name"], "deleted": True}
            for p in ctx.db.people_seen() if p["code"] not in cur]
    return sorted(out, key=lambda p: (p["deleted"], p["name"] or ""))


def _appearances(date_from, date_to, code, kind, limit, tier: str | None = None) -> list[dict]:
    if kind and kind not in KINDS:
        raise HTTPException(400, "kind phải là employee | unknown | spoof")
    if tier and tier not in TIERS:
        raise HTTPException(400, "tier không hợp lệ")
    t0, t1 = _range(date_from, date_to)
    rows = ctx.db.list_appearances(t0, t1, code or None, kind or None, min(max(limit, 1), 2000), tier=tier or None)
    data = ctx.cfg["_paths"]["data"]
    for r in rows:
        r["duration"] = round(r["end_ts"] - r["start_ts"], 1)
        r["clip_ok"] = bool(r["clip"]) and (data / r["clip"]).is_file()
    return rows


@router.get("/api/appearances")
def appearances(date_from: str | None = None, date_to: str | None = None, code: str | None = None,
                kind: str | None = None, tier: str | None = None, limit: int = 300):
    return _appearances(date_from, date_to, code, kind, limit, tier=tier)


@router.get("/api/appearances.zip")
def appearances_zip(date_from: str | None = None, date_to: str | None = None, code: str | None = None,
                    kind: str | None = None, tier: str | None = None):
    rows = [r for r in _appearances(date_from, date_to, code, kind, MAX_ZIP_CLIPS, tier=tier) if r["clip_ok"]]
    if not rows:
        raise HTTPException(404, "Không có clip nào trong khoảng đã chọn")
    data: Path = ctx.cfg["_paths"]["data"]
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir=ctx.cfg["_paths"]["uploads"])
    tmp.close()
    index = io.StringIO()
    w = csv.writer(index)
    w.writerow(["file", "bat_dau", "ket_thuc", "giay", "loai", "muc_xac_thuc", "nhom_goc", "ma", "ho_ten", "score", "nguoi_that"])
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED) as z:   # video da nen san
        for r in rows:
            src = data / r["clip"]
            who = r["code"] or r["kind"]
            arc = f"{_fmt(r['start_ts'], '%Y%m%d_%H%M%S')}_{who}_{r['id']}{src.suffix}"
            z.write(src, arc)
            w.writerow([arc, _fmt(r["start_ts"], "%Y-%m-%d %H:%M:%S"), _fmt(r["end_ts"], "%Y-%m-%d %H:%M:%S"),
                        r["duration"], r["kind"], r.get("verification_tier") or "", r.get("pose_group") or "",
                        r["code"] or "", r["name"] or "", r["score"], r["liveness"]])
        z.writestr("danh_sach.csv", "﻿" + index.getvalue())
    name = f"lich_su_{tier or code or kind or 'tat_ca'}_{date_from or date.today()}.zip"
    return FileResponse(tmp.name, media_type="application/zip", filename=name,
                        background=BackgroundTask(lambda: Path(tmp.name).unlink(missing_ok=True)))
