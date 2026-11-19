"""API quan ly Hang hoa / SKU, chuyen che do camera (attendance | sku) va thong ke xuat CSV."""
from __future__ import annotations

import csv
import io
import math
import shutil
import time
from datetime import date, datetime

import cv2
import numpy as np
from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from ..attendance.shift import MAX_DAYS, day_range
from ..store.db import validate_sku_code
from .security import require_role
from .state import bc, ctx

router = APIRouter()


def _parse_range(date_from: str | None, date_to: str | None) -> tuple[float, float, str, str]:
    try:
        d0 = date.fromisoformat(date_from) if date_from else date.today()
        d1 = date.fromisoformat(date_to) if date_to else d0
    except ValueError:
        raise HTTPException(400, "Ngày phải có dạng YYYY-MM-DD")
    if d1 < d0:
        d0, d1 = d1, d0
    if (d1 - d0).days >= MAX_DAYS:
        raise HTTPException(400, f"Khoảng ngày tối đa {MAX_DAYS} ngày")
    return day_range(d0)[0], day_range(d1)[1], d0.isoformat(), d1.isoformat()


def _parse_roi_norm(roi: str) -> list[float] | None:
    s = (roi or "").strip()
    if not s:
        return None
    raw_parts = s.split(",")
    if len(raw_parts) != 4:
        raise HTTPException(400, "ROI phải có dạng x1,y1,x2,y2")
    try:
        parts = [float(x) for x in raw_parts]
    except ValueError:
        raise HTTPException(400, "ROI phải có dạng x1,y1,x2,y2")
    if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in parts):
        raise HTTPException(400, "Tọa độ ROI phải là số hữu hạn trong khoảng 0..1")
    x1, x2 = min(parts[0], parts[2]), max(parts[0], parts[2])
    y1, y2 = min(parts[1], parts[3]), max(parts[1], parts[3])
    if (x2 - x1) < 0.01 or (y2 - y1) < 0.01:
        raise HTTPException(400, "Vùng ROI quá nhỏ")
    return [x1, y1, x2, y2]


def _crop_frame_norm(frame: np.ndarray, roi_norm: list[float] | None) -> np.ndarray:
    if not roi_norm or len(roi_norm) != 4:
        return frame
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in roi_norm)
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
        raise ValueError("ROI chứa giá trị không hữu hạn")
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    px1 = int(np.clip(round(x1 * w), 0, max(0, w - 2)))
    py1 = int(np.clip(round(y1 * h), 0, max(0, h - 2)))
    px2 = int(np.clip(round(x2 * w), px1 + 1, w))
    py2 = int(np.clip(round(y2 * h), py1 + 1, h))
    crop = frame[py1:py2, px1:px2]
    return crop if crop.size > 0 else frame


def _decode_uploaded_image(raw: bytes) -> np.ndarray:
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        raise HTTPException(400, "Không đọc được ảnh mẫu (dùng JPG/PNG/WebP)")
    return img


_CSV_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value) -> str | int | float:
    """Chan CSV injection tren chuoi van ban ma khong bien so am (vd qty < 0) thanh text."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value if math.isfinite(value) else ""
    s = str(value)
    return "'" + s if s.startswith(_CSV_FORMULA_PREFIX) else s


# ----------------------------------------------------------- mode switching
@router.get("/api/mode")
def get_mode():
    return {"mode": ctx.pipeline.mode}


@router.post("/api/mode")
async def set_mode(request: Request, mode: str = Body(..., embed=True)):
    actor = require_role(request, ctx.cfg, min_role="hr")
    try:
        m = ctx.pipeline.set_mode(mode)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # Worker nhan dien chay tien trinh rieng: phai tat cham cong/overlay khuon mat o phia no nua
    if getattr(ctx, "ipc", None) is not None:
        await ctx.ipc.send_command({"cmd": "set_mode", "mode": m})
    ctx.db.add_audit_log(actor, "switch_mode", "pipeline", m, {"mode": m},
                         ip_addr=request.client.host if request.client else None)
    return {"mode": m}


# ------------------------------------------------------------------ SKU CRUD
@router.get("/api/sku/items")
def list_sku_items():
    return ctx.db.list_sku_items()


@router.post("/api/sku/items")
async def create_or_update_sku_item(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    barcode: str = Form(""),
    uom: str = Form("pcs"),
    source: str = Form("upload"),          # upload | camera | none
    replace_templates: bool = Form(False),
    roi: str = Form(""),                   # "x1,y1,x2,y2" chuan hoa 0..1 khi chup tu camera
    file: UploadFile | None = File(None),
):
    actor = require_role(request, ctx.cfg, min_role="hr")
    try:
        code_c = validate_sku_code(code)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not name.strip():
        raise HTTPException(400, "Thiếu tên sản phẩm / SKU")

    img: np.ndarray | None = None
    src_mode = (source or "upload").strip().lower()
    if src_mode not in ("camera", "upload", "none"):
        raise HTTPException(400, "source phải là 'upload', 'camera' hoặc 'none'")
    if src_mode == "camera":
        roi_vals = _parse_roi_norm(roi)
        if not ctx.camera.connected:
            raise HTTPException(503, "Camera chưa kết nối")
        _, frame = ctx.camera.read()
        if frame is None:
            raise HTTPException(503, "Chưa đọc được khung hình từ camera")
        img = _crop_frame_norm(frame, roi_vals).copy()
    elif src_mode == "upload":
        if file is None or not file.filename:
            raise HTTPException(400, "Vui lòng chọn file ảnh mẫu bao bì (hoặc đổi source='none')")
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "File ảnh mẫu rỗng")
        img = _decode_uploaded_image(raw)

    try:
        report = await run_in_threadpool(
            ctx.sku_scanner.register_template,
            ctx.db,
            code_c,
            name.strip(),
            img=img,
            barcode=barcode.strip() or None,
            uom=uom.strip() or "pcs",
            label="camera" if src_mode == "camera" else "upload",
            save_dir=ctx.cfg["_paths"]["data"],
            replace_templates=bool(replace_templates),
        )
    except ValueError as e:
        raise HTTPException(409 if "đã thuộc về" in str(e) else 400, str(e))

    ctx.db.add_audit_log(
        actor, "upsert_sku", "sku_items", str(report["sku_id"]),
        {"code": code_c, "source": src_mode, "has_image": img is not None},
        ip_addr=request.client.host if request.client else None,
    )
    bc({"type": "sku_items"})
    bc({"type": "status", "status": ctx.pipeline.status()})
    return report


@router.post("/api/sku/items/{sku_id}/{action}")
def sku_item_action(sku_id: int, action: str, request: Request):
    actor = require_role(request, ctx.cfg, min_role="hr")
    status = {"activate": "active", "disable": "disabled"}.get(action)
    if not status:
        raise HTTPException(400, "action không hợp lệ")
    rows = ctx.db.q("SELECT id, code FROM sku_items WHERE id=?", (sku_id,))
    if not rows:
        raise HTTPException(404, "Không tìm thấy SKU")
    ctx.db.set_sku_status(sku_id, status)
    ctx.sku_gallery.reload()
    ctx.db.add_audit_log(actor, f"sku_{action}", "sku_items", str(sku_id), {"code": rows[0]["code"]})
    bc({"type": "sku_items"})
    bc({"type": "status", "status": ctx.pipeline.status()})
    return {"ok": True}


@router.delete("/api/sku/items/{sku_id}")
def delete_sku_item(sku_id: int, request: Request):
    actor = require_role(request, ctx.cfg, min_role="hr")
    rows = ctx.db.q("SELECT code FROM sku_items WHERE id=?", (sku_id,))
    if not rows:
        raise HTTPException(404, "Không tìm thấy SKU")
    code = rows[0]["code"]
    ctx.db.delete_sku_item(sku_id)
    sku_dir = (ctx.cfg["_paths"]["data"] / "sku" / code).resolve()
    sku_root = (ctx.cfg["_paths"]["data"] / "sku").resolve()
    if sku_root in sku_dir.parents:
        shutil.rmtree(sku_dir, ignore_errors=True)
    ctx.sku_gallery.reload()
    ctx.db.add_audit_log(actor, "delete_sku", "sku_items", str(sku_id), {"code": code})
    bc({"type": "sku_items"})
    bc({"type": "status", "status": ctx.pipeline.status()})
    return {"ok": True}


# ------------------------------------------------------- SKU events & summary
@router.get("/api/sku/events")
def list_sku_events(
    date_from: str | None = None,
    date_to: str | None = None,
    code: str | None = None,
    method: str | None = None,
    limit: int = 200,
):
    t0, t1, _, _ = _parse_range(date_from, date_to)
    return ctx.db.list_sku_events(t0=t0, t1=t1, code=code or None, method=method or None, limit=min(max(limit, 1), 2000))


@router.get("/api/sku/summary")
def sku_summary(date_from: str | None = None, date_to: str | None = None, code: str | None = None):
    t0, t1, d0, d1 = _parse_range(date_from, date_to)
    agg = ctx.db.summarize_sku_events(t0=t0, t1=t1, code=code or None)
    return {"from": d0, "to": d1, **agg}


@router.get("/api/sku/events.csv")
def sku_events_csv(date_from: str | None = None, date_to: str | None = None, code: str | None = None):
    t0, t1, d0, d1 = _parse_range(date_from, date_to)
    evs = ctx.db.list_sku_events(t0=t0, t1=t1, code=code or None, limit=None)
    buf = io.StringIO()
    cols = [
        ("id", "ID"), ("time", "Thời gian"), ("camera", "Camera"), ("code", "Mã SKU"),
        ("name", "Tên sản phẩm"), ("barcode", "Mã vạch/QR"), ("method", "Phương thức"),
        ("score", "Độ tin cậy"), ("inliers", "Inliers"), ("qty", "Số lượng"),
        ("uom", "ĐVT"), ("snapshot", "Ảnh"),
    ]
    w = csv.writer(buf)
    w.writerow([title for _, title in cols])
    for e in evs:
        e["time"] = datetime.fromtimestamp(e["ts"]).strftime("%Y-%m-%d %H:%M:%S")
        w.writerow([_csv_cell(e.get(k)) if k != "time" else e["time"] for k, _ in cols])
    return Response(
        "\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=sku_{d0}_{d1}.csv"},
    )
