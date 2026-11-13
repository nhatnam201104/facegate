"""Quan ly clip bang chung H.264 goc sach tu MediaMTX Playback API + Sidecar ky HMAC-SHA256 + Burn-in FFmpeg & Don dia an toan."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import cv2

from .draw import GREEN, GRAY, ORANGE, RED, draw_overlay

log = logging.getLogger("media_clips")

COLOR_BY_ROLE = {
    "ok": GREEN,
    "checking": ORANGE,
    "alert": RED,
    "low_quality": GRAY,
}


def _hmac_secret(cfg: dict | None = None) -> bytes:
    env_key = os.environ.get("FACEGATE_HMAC_SECRET", "").strip()
    if env_key:
        return env_key.encode("utf-8")
    cfg_key = ((cfg or {}).get("audit_and_safety") or {}).get("hmac_secret", "")
    return (str(cfg_key) if cfg_key else "facegate-default-dev-hmac-secret-change-in-prod").encode("utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sidecar_bytes(meta: dict) -> bytes:
    clean = {k: v for k, v in meta.items() if k != "hmac_sha256"}
    return json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_sidecar(meta: dict, secret: bytes) -> str:
    return hmac.new(secret, canonical_sidecar_bytes(meta), hashlib.sha256).hexdigest()


def write_signed_sidecar(mp4_path: Path, data_root: Path, appearance_id: int, camera_id: str,
                         clip_start_server_ns: int, event_offset_ms: int,
                         timeline: list[dict], cfg: dict | None = None) -> tuple[Path, str, str]:
    """Tao file .meta.json ky HMAC-SHA256 di kem file MP4 goc sach. Tra ve (sidecar_path, clip_sha256, hmac_hex)."""
    clip_hash = sha256_file(mp4_path)
    rel_mp4 = mp4_path.relative_to(data_root).as_posix()
    meta = {
        "appearance_id": int(appearance_id),
        "camera_id": str(camera_id),
        "clip_rel_path": rel_mp4,
        "clip_sha256": clip_hash,
        "clip_start_server_ns": int(clip_start_server_ns),
        "event_offset_ms": max(0, int(event_offset_ms)),
        "audio_stripped": True,
        "timeline": timeline,
    }
    sig = sign_sidecar(meta, _hmac_secret(cfg))
    meta["hmac_sha256"] = sig
    sidecar = mp4_path.with_suffix(".meta.json")
    sidecar.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return sidecar, clip_hash, sig


def verify_clip_sidecar(mp4_path: Path, sidecar_path: Path, cfg: dict | None = None) -> tuple[bool, str, dict]:
    if not mp4_path.is_file():
        return False, "Khong tim thay file clip MP4 goc", {}
    if not sidecar_path.is_file():
        return False, "Khong tim thay file sidecar .meta.json", {}
    try:
        meta = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, f"Sidecar JSON loi: {e}", {}
    expected_sig = str(meta.get("hmac_sha256", ""))
    calc_sig = sign_sidecar(meta, _hmac_secret(cfg))
    if not expected_sig or not hmac.compare_digest(expected_sig, calc_sig):
        return False, "Chu ky HMAC-SHA256 cua sidecar khong hop le (nghi ngo chinh sua)", meta
    actual_sha = sha256_file(mp4_path)
    if actual_sha != meta.get("clip_sha256"):
        return False, "Ma bam SHA-256 cua file MP4 khong khop voi sidecar", meta
    return True, "ok", meta


def fetch_playback_clip(playback_base_url: str, mtx_path: str, start_ts: float, end_ts: float,
                        pre_sec: float, post_sec: float, dst_mp4: Path, timeout: float = 10.0) -> bool:
    """Cat clip H.264 sach tu MediaMTX Playback Server API (/get?path=...&start=...&duration=...)."""
    t0 = max(0.0, start_ts - pre_sec)
    dur = max(1.0, (end_ts - start_ts) + pre_sec + post_sec)
    iso_start = datetime.fromtimestamp(t0, tz=timezone.utc).isoformat()
    qs = urllib.parse.urlencode({"path": mtx_path, "start": iso_start, "duration": f"{dur:.2f}s", "format": "mp4"})
    url = f"{playback_base_url.rstrip('/')}/get?{qs}"
    dst_mp4.parent.mkdir(parents=True, exist_ok=True)
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
        if len(payload) < 256:
            return False
        dst_mp4.write_bytes(payload)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("Khong trich xuat duoc clip tu MediaMTX Playback (%s): %s", url, e)
        return False


def render_burnin_mp4(mp4_path: Path, sidecar_path: Path, out_mp4: Path, cfg: dict | None = None) -> Path:
    """Xac thuc HMAC-SHA256 roi ket xuat ban MP4 da dong dau khung bao + nhan tieng Viet + timestamp server (P2, H9)."""
    ok, msg, meta = verify_clip_sidecar(mp4_path, sidecar_path, cfg=cfg)
    if not ok:
        raise ValueError(msg)

    cap = cv2.VideoCapture(str(mp4_path))
    if not cap.isOpened():
        raise RuntimeError(f"Khong doc duoc video goc: {mp4_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 640)
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 360)
    timeline = sorted(meta.get("timeline") or [], key=lambda x: float(x.get("offset_ms", 0)))
    start_ns = int(meta.get("clip_start_server_ns") or 0)

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    raw_tmp = out_mp4.with_suffix(".raw_burnin.mp4")
    writer = cv2.VideoWriter(str(raw_tmp), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        cap.release()
        raise RuntimeError("Khong khoi tao duoc bo ghi tam cho burn-in MP4")

    idx = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            cur_ms = (idx / max(fps, 1.0)) * 1000.0
            active_objs: list[dict] = []
            for item in timeline:
                dt = cur_ms - float(item.get("offset_ms", 0.0))
                if -120.0 <= dt <= 350.0:
                    active_objs = item.get("objects") or []
            boxes = []
            for o in active_objs:
                bn = o.get("bbox_norm") or [0, 0, 0, 0]
                boxes.append({
                    "bbox": (bn[0] * w, bn[1] * h, bn[2] * w, bn[3] * h),
                    "color": COLOR_BY_ROLE.get(o.get("color_role", "checking"), ORANGE),
                    "label": o.get("label", ""),
                })
            ts_sec = (start_ns / 1e9) + (cur_ms / 1000.0) if start_ns > 0 else 0.0
            ts_txt = datetime.fromtimestamp(ts_sec).strftime("%Y-%m-%d %H:%M:%S") if ts_sec > 0 else f"+{int(cur_ms)}ms"
            hud = f"FACEGATE VERIFIED EXPORT · {meta.get('camera_id', 'gate')} · {ts_sec and ts_txt or ''} · HMAC OK"
            burned = draw_overlay(frame.copy(), boxes, hud)
            writer.write(burned)
            idx += 1
    finally:
        cap.release()
        writer.release()

    # Neu he thong co ffmpeg thi remux sang H.264 chuan trinh duyet (-an dam bao 0 audio)
    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin:
        try:
            subprocess.run(
                [
                    ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(raw_tmp), "-an", "-c:v", "libx264", "-preset", "ultrafast",
                    "-movflags", "+faststart", str(out_mp4),
                ],
                check=True,
                timeout=60,
            )
            raw_tmp.unlink(missing_ok=True)
            return out_mp4
        except Exception:  # noqa: BLE001
            pass
    raw_tmp.replace(out_mp4)
    return out_mp4


def cleanup_clips_safe(db, data_root: Path, retention_days: float = 30.0,
                       min_free_disk_gb: float = 2.0, simulated_free_gb: float | None = None) -> dict:
    """Don dep clip qua han retention_days hoac khi dia trong < min_free_disk_gb, TUYET DOI bao ve legal_hold=1 (H15).

    Day la duong don dep DUY NHAT cho thu muc clips (ClipRecorder khong tu xoa nua, CR5).
    retention_days <= 0 nghia la khong xoa theo han (giu nhu ClipRecorder cu, CR8); van xoa khi dia day.
    """
    now = time.time()
    expiry_enabled = retention_days > 0
    cutoff = now - retention_days * 86400.0 if expiry_enabled else float("-inf")
    deleted_expired = 0
    deleted_low_disk = 0
    protected_hold = 0

    def _free_gb() -> float:
        if simulated_free_gb is not None:
            return simulated_free_gb
        usage = shutil.disk_usage(data_root)
        return usage.free / (1024 ** 3)

    rows = db.q("SELECT id, start_ts, clip, sidecar_path, legal_hold FROM appearances WHERE clip IS NOT NULL ORDER BY start_ts ASC")
    for r in rows:
        if int(r.get("legal_hold") or 0) == 1:
            protected_hold += 1
            continue
        expired = float(r["start_ts"]) < cutoff
        low_space = _free_gb() < min_free_disk_gb
        if not expired and not low_space:
            continue
        clip_p = data_root / r["clip"]
        clip_p.unlink(missing_ok=True)
        if r.get("sidecar_path"):
            (data_root / r["sidecar_path"]).unlink(missing_ok=True)
        else:
            clip_p.with_suffix(".meta.json").unlink(missing_ok=True)
        db.x("UPDATE appearances SET clip=NULL, clip_ready=0 WHERE id=?", (r["id"],))
        if expired:
            deleted_expired += 1
        else:
            deleted_low_disk += 1
            if simulated_free_gb is not None:
                simulated_free_gb += 0.25  # gia lap giai phong dung luong cho moi clip xoa trong unit test

    # File mo coi (khong dong appearances nao tro toi, vd. tien trinh chet giua chung) qua han -> xoa,
    # de khong ro ri dung luong. File dang ghi co mtime moi nen khong bi dong toi.
    deleted_orphan = 0
    clips_root = data_root / "clips"
    if expiry_enabled and clips_root.is_dir():
        referenced: set[str] = set()
        for r in db.q("SELECT clip, sidecar_path FROM appearances WHERE clip IS NOT NULL OR sidecar_path IS NOT NULL"):
            if r.get("clip"):
                referenced.add(r["clip"])
                referenced.add(Path(r["clip"]).with_suffix(".meta.json").as_posix())
            if r.get("sidecar_path"):
                referenced.add(r["sidecar_path"])
        for f in clips_root.glob("*/*"):
            if not f.is_file():
                continue
            rel = f.relative_to(data_root).as_posix()
            if rel in referenced:
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
                    deleted_orphan += 1
            except OSError:
                continue
        for d in clips_root.iterdir():
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()

    still_low = _free_gb() < min_free_disk_gb
    return {
        "deleted_expired": deleted_expired,
        "deleted_low_disk": deleted_low_disk,
        "deleted_orphan": deleted_orphan,
        "protected_legal_hold": protected_hold,
        "disk_warning": still_low,
        "free_disk_gb": round(_free_gb(), 2),
    }
