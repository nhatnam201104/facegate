"""Dang ky khuon mat tu video ngan: tu chon 8-15 frame tot theo goc nhin."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import cv2
import numpy as np

from .pipeline import crop_with_margin
from .quality import assess

log = logging.getLogger("enroll")


class EnrollError(Exception):
    pass


def _pose_bin(yaw: float, pitch: float) -> str:
    h = "trai" if yaw < -12 else ("phai" if yaw > 12 else "thang")
    v = "ngang" if pitch < -12 else ("cui" if pitch > 12 else "giua")
    return f"{h}-{v}"


def read_video_frames(path: Path, sample_fps: float, max_frames: int = 300) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise EnrollError("Không đọc được video. Hãy dùng MP4 (H.264) hoặc WebM.")
    fps = cap.get(cv2.CAP_PROP_FPS)
    fps = fps if fps and fps < 240 else 30.0
    step = max(1, int(round(fps / sample_fps)))
    frames, i = [], 0
    while len(frames) < max_frames:
        ok, f = cap.read()
        if not ok:
            break
        if i % step == 0:
            frames.append(f)
        i += 1
    cap.release()
    if not frames:
        raise EnrollError("Video không có frame nào đọc được.")
    return frames


def _resize_for_detect(frame: np.ndarray, width: int = 960):
    h, w = frame.shape[:2]
    s = min(1.0, width / w)
    return (cv2.resize(frame, (int(w * s), int(h * s))) if s < 1 else frame), s


def process_enrollment(frames: list[np.ndarray], code: str, name: str, ctx) -> dict:
    """ctx cung cap: cfg, db, engine, engine_lock, gallery, threshold."""
    cfg, db, engine = ctx.cfg, ctx.db, ctx.engine
    ecfg, qcfg = cfg["enrollment"], dict(cfg["quality"])
    qcfg["max_yaw"] = 45  # khi dang ky cho phep quay nhieu hon de lay du goc

    stats = {"frames_total": len(frames), "no_face": 0, "multi_face": 0, "rejected": {}}
    cands = []
    for idx, fr in enumerate(frames):
        small, s = _resize_for_detect(fr)
        with ctx.engine_lock:
            faces = engine.detect(small)
        if not faces:
            stats["no_face"] += 1
            continue
        for f in faces:
            f.bbox, f.kps = f.bbox / s, f.kps / s
        faces.sort(key=lambda f: f.w * f.h, reverse=True)
        if len(faces) > 1 and faces[1].w * faces[1].h > 0.4 * faces[0].w * faces[0].h:
            stats["multi_face"] += 1          # co nguoi khac lot vao khung -> bo frame
            continue
        f = faces[0]
        q = assess(fr, f, qcfg)
        if not q.ok:
            stats["rejected"][q.reason] = stats["rejected"].get(q.reason, 0) + 1
            continue
        with ctx.engine_lock:
            emb = engine.embed_aligned(engine.align(fr, f))
        cands.append({"idx": idx, "emb": emb, "q": q, "bin": _pose_bin(q.yaw, q.pitch),
                      "crop": crop_with_margin(fr, f.bbox)})

    min_n, max_n = int(ecfg["min_frames"]), int(ecfg["max_frames"])
    if len(cands) < min_n:
        raise EnrollError(f"Chỉ có {len(cands)} frame đạt chất lượng (cần ≥ {min_n}). "
                          f"Quay lại nơi đủ sáng, mặt to rõ, giữ máy ổn định. Chi tiết: {stats}")

    # Nhat quan: loai frame lech khoi trung tam (co the la nguoi khac)
    mean = np.mean([c["emb"] for c in cands], axis=0)
    mean /= np.linalg.norm(mean) + 1e-9
    for c in cands:
        c["sim_center"] = float(c["emb"] @ mean)
    outliers = [c for c in cands if c["sim_center"] < 0.45]
    cands = [c for c in cands if c["sim_center"] >= 0.45]
    if len(outliers) > 0.3 * (len(cands) + len(outliers)):
        raise EnrollError("Các frame không nhất quán (có thể nhiều người trong video). Hãy quay lại chỉ một người.")

    # Chon theo o goc nhin, loai trung lap
    bins: dict[str, list] = {}
    for c in cands:
        bins.setdefault(c["bin"], []).append(c)
    for b in bins.values():
        b.sort(key=lambda c: c["q"].score, reverse=True)
    selected: list = []
    per_bin, dd = int(ecfg["per_bin"]), float(ecfg["dedup_cosine"])
    # vong lap round-robin de cac goc deu co mat
    for rnd in range(per_bin):
        for b in sorted(bins, key=lambda k: (k != "thang-giua", k)):
            if len(selected) >= max_n:
                break
            if rnd < len(bins[b]):
                c = bins[b][rnd]
                if all(float(c["emb"] @ s["emb"]) < dd for s in selected):
                    selected.append(c)
    if len(selected) < min_n:  # bu them frame tot nhat neu dedup qua chat
        rest = sorted((c for c in cands if c not in selected), key=lambda c: c["q"].score, reverse=True)
        selected += rest[: min_n - len(selected)]

    # Kiem tra trung voi nhan vien khac
    center = np.mean([c["emb"] for c in selected], axis=0)
    center /= np.linalg.norm(center) + 1e-9
    warnings = []
    others = db.load_gallery(engine.model_version, statuses=("active", "pending"))
    best_other: dict[str, float] = {}
    for r in others:
        if r["code"] == code:
            continue
        s = float(r["vec"] @ center)
        if s > best_other.get(r["code"], -1):
            best_other[r["code"]] = s
    dup = [(k, v) for k, v in best_other.items() if v >= ctx.threshold]
    if dup:
        k, v = max(dup, key=lambda kv: kv[1])
        warnings.append(f"Khuôn mặt giống nhân viên {k} (cosine {v:.2f}). HR cần kiểm tra trước khi duyệt.")

    # Luu
    emp = db.get_employee_by_code(code)
    if emp:
        emp_id = emp["id"]
        db.x("UPDATE employees SET name=?, status='pending' WHERE id=?", (name, emp_id))
        db.clear_embeddings(emp_id, engine.model_version)
        shutil.rmtree(cfg["_paths"]["enroll"] / code, ignore_errors=True)
    else:
        emp_id = db.create_employee(code, name)
    folder = cfg["_paths"]["enroll"] / code
    folder.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(selected):
        fn = f"{i:02d}_{c['bin']}.jpg"
        cv2.imwrite(str(folder / fn), c["crop"])
        db.add_embedding(emp_id, c["emb"], engine.model_version, c["q"].score, c["bin"], f"enroll/{code}/{fn}")

    report = {
        "frames_total": stats["frames_total"], "frames_good": len(cands) + len(outliers),
        "frames_selected": len(selected), "no_face": stats["no_face"], "multi_face": stats["multi_face"],
        "rejected": stats["rejected"], "outliers": len(outliers),
        "bins": {b: sum(1 for c in selected if c["bin"] == b) for b in bins},
        "min_pair_cosine": round(min((float(a["emb"] @ b["emb"]) for i, a in enumerate(selected)
                                      for b in selected[i + 1:]), default=1.0), 3),
        "warnings": warnings, "model_version": engine.model_version,
        "crops": [f"enroll/{code}/{i:02d}_{c['bin']}.jpg" for i, c in enumerate(selected)],
    }
    if len(bins) < 3:
        report["warnings"].append("Ít góc nhìn (<3). Nên quay lại: nhìn thẳng, xoay trái/phải, ngẩng/cúi nhẹ.")
    db.add_enrollment(emp_id, report)
    report["employee_id"] = emp_id
    log.info("Dang ky %s (%s): chon %d/%d frame", name, code, len(selected), len(frames))
    return report
