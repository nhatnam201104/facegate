"""API quan ly Khu vuc xuong (Polygon Zones), theo doi hien dien Time-Based & xuat CSV (GAP-04)."""
from __future__ import annotations

import csv
import io
import math
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response

from ..store.db import validate_hex_color
from ..vision.pipeline import validate_zone
from .security import require_role
from .sku import _csv_cell, _parse_range
from .state import bc, ctx

router = APIRouter()


def _finite_float(val: float, name: str, lo: float, hi: float) -> float:
    v = float(val)
    if not math.isfinite(v):
        raise ValueError(f"{name} phải là số hữu hạn")
    return max(lo, min(hi, v))


@router.get("/api/zones")
def list_zones():
    rows = ctx.db.list_zones(active_only=False)
    live_map = {
        int(z["id"]): z
        for z in (ctx.zone_engine.live_status() if getattr(ctx, "zone_engine", None) else [])
    }
    for r in rows:
        lv = live_map.get(int(r["id"]))
        r["present_count"] = int(lv["present_count"]) if lv else 0
        r["max_dwell_sec"] = float(lv["max_dwell_sec"]) if lv else 0.0
        r["empty_sec"] = float(lv["empty_sec"]) if lv else 0.0
        r["alert"] = bool(lv["alert"]) if lv else False
    return rows


@router.post("/api/zones")
def create_or_update_zone(
    request: Request,
    code: str = Body(...),
    name: str = Body(...),
    polygon: list[list[float]] = Body(...),
    enter_confirm_sec: float = Body(1.0),
    min_dwell_sec: float = Body(3.0),
    exit_grace_sec: float = Body(2.0),
    alert_empty_sec: float = Body(0.0),
    min_persons: int = Body(1),
    color: str = Body("#3cc85a"),
):
    actor = require_role(request, ctx.cfg, min_role="hr")
    try:
        poly_clean = validate_zone(polygon)
        color_clean = validate_hex_color(color)
        zid = ctx.db.upsert_zone(
            code=code,
            name=name,
            polygon=poly_clean,
            camera_id=str(ctx.cfg.get("camera", {}).get("id", "gate-1")),
            enter_confirm_sec=_finite_float(enter_confirm_sec, "enter_confirm_sec", 0.0, 300.0),
            min_dwell_sec=_finite_float(min_dwell_sec, "min_dwell_sec", 0.5, 3600.0),
            exit_grace_sec=_finite_float(exit_grace_sec, "exit_grace_sec", 0.2, 300.0),
            alert_empty_sec=_finite_float(alert_empty_sec, "alert_empty_sec", 0.0, 86400.0),
            min_persons=max(1, min(100, int(min_persons))),
            color=color_clean,
            status="active",
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    if getattr(ctx, "zone_engine", None) is not None:
        ctx.zone_engine.reload_zones()
    ctx.db.add_audit_log(
        actor, "upsert_zone", "zones", str(zid),
        {"code": code, "vertices": len(poly_clean), "min_persons": int(min_persons)},
        ip_addr=request.client.host if request.client else None,
    )
    bc({"type": "zones"})
    bc({"type": "status", "status": ctx.pipeline.status()})
    return {"ok": True, "zone_id": zid}


@router.post("/api/zones/{zone_id}/{action}")
def zone_action(zone_id: int, action: str, request: Request):
    actor = require_role(request, ctx.cfg, min_role="hr")
    status = {"activate": "active", "disable": "disabled"}.get(action)
    if not status:
        raise HTTPException(400, "action không hợp lệ")
    rows = ctx.db.q("SELECT id, code FROM zones WHERE id=?", (zone_id,))
    if not rows:
        raise HTTPException(404, "Không tìm thấy khu vực")
    ctx.db.set_zone_status(zone_id, status)
    if getattr(ctx, "zone_engine", None) is not None:
        ctx.zone_engine.reload_zones()
    ctx.db.add_audit_log(actor, f"zone_{action}", "zones", str(zone_id), {"code": rows[0]["code"]})
    bc({"type": "zones"})
    bc({"type": "status", "status": ctx.pipeline.status()})
    return {"ok": True}


@router.delete("/api/zones/{zone_id}")
def delete_zone(zone_id: int, request: Request):
    actor = require_role(request, ctx.cfg, min_role="hr")
    rows = ctx.db.q("SELECT code FROM zones WHERE id=?", (zone_id,))
    if not rows:
        raise HTTPException(404, "Không tìm thấy khu vực")
    code = rows[0]["code"]
    ctx.db.delete_zone(zone_id)
    if getattr(ctx, "zone_engine", None) is not None:
        ctx.zone_engine.reload_zones()
    ctx.db.add_audit_log(actor, "delete_zone", "zones", str(zone_id), {"code": code})
    bc({"type": "zones"})
    bc({"type": "status", "status": ctx.pipeline.status()})
    return {"ok": True}


@router.get("/api/zones/sessions")
def list_zone_sessions(
    date_from: str | None = None,
    date_to: str | None = None,
    zone_code: str | None = None,
    limit: int = 200,
):
    t0, t1, _, _ = _parse_range(date_from, date_to)
    return ctx.db.list_zone_sessions(
        t0=t0, t1=t1, zone_code=zone_code or None, limit=min(max(limit, 1), 2000),
    )


@router.get("/api/zones/summary")
def zone_summary(
    date_from: str | None = None,
    date_to: str | None = None,
    zone_code: str | None = None,
):
    t0, t1, d0, d1 = _parse_range(date_from, date_to)
    agg = ctx.db.summarize_zone_sessions(t0=t0, t1=t1, zone_code=zone_code or None)
    live = ctx.zone_engine.live_status() if getattr(ctx, "zone_engine", None) is not None else []
    return {
        "from": d0,
        "to": d1,
        "active_zones": len(live),
        "live_present_total": sum(int(z.get("present_count", 0)) for z in live),
        "live_alerts": sum(1 for z in live if z.get("alert")),
        **agg,
    }


@router.get("/api/zones/sessions.csv")
def zone_sessions_csv(
    date_from: str | None = None,
    date_to: str | None = None,
    zone_code: str | None = None,
):
    t0, t1, d0, d1 = _parse_range(date_from, date_to)
    rows = ctx.db.list_zone_sessions(t0=t0, t1=t1, zone_code=zone_code or None, limit=None)
    buf = io.StringIO()
    cols = [
        ("id", "ID"),
        ("zone_code", "Mã khu vực"),
        ("zone_name", "Tên khu vực"),
        ("camera", "Camera"),
        ("person_track_id", "Track ID"),
        ("enter_time", "Giờ vào"),
        ("exit_time", "Giờ ra"),
        ("dwell_sec", "Thời gian hiện diện (giây)"),
        ("status", "Trạng thái"),
    ]
    w = csv.writer(buf)
    w.writerow([title for _, title in cols])
    for r in rows:
        r["enter_time"] = datetime.fromtimestamp(r["enter_ts"]).strftime("%Y-%m-%d %H:%M:%S")
        r["exit_time"] = datetime.fromtimestamp(r["exit_ts"]).strftime("%Y-%m-%d %H:%M:%S") if r.get("exit_ts") else ""
        w.writerow([
            r[k] if k in ("enter_time", "exit_time") else _csv_cell(r.get(k))
            for k, _ in cols
        ])
    return Response(
        "\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=zones_{d0}_{d1}.csv"},
    )
