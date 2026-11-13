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
    if yaw <= -60.0:
        return "nghieng-sau-trai"
    if yaw >= 60.0:
        return "nghieng-sau-phai"
    if yaw <= -40.0:
        return "ban-nghieng-trai"
    if yaw >= 40.0:
        return "ban-nghieng-phai"
    h = "trai" if yaw < -12 else ("phai" if yaw > 12 else "thang")
    v = "ngang" if pitch < -12 else ("cui" if pitch > 12 else "giua")
    return f"{h}-{v}"


def _pose_group_from_bin_or_yaw(bin_name: str, yaw: float = 0.0) -> str:
    if "sau-trai" in bin_name or "nghieng-trai" in bin_name or yaw < -40.0:
        return "left_profile"
    if "sau-phai" in bin_name or "nghieng-phai" in bin_name or yaw > 40.0:
        return "right_profile"
    return "frontal"


def try_auto_enroll_from_gate(ctx, *_args, **_kwargs) -> None:
    """H17: Cam tuyet doi tu dong them mau tu luong cong de chong dau doc mau (Template Poisoning)."""
    if not bool((ctx.cfg.get("decision") or {}).get("auto_enroll_online", False)):
        raise EnrollError("Tu dong them mau online (auto_enroll_online) da bi cam theo chinh sach an toan H17")


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


def _enroll_engine_and_lock(ctx):
    """Uu tien dung session suy luan rieng (enroll_engine) de khong khoa cong cham cong (P6, T028)."""
    eng = getattr(ctx, "enroll_engine", None) or ctx.engine
    lck = getattr(ctx, "enroll_lock", None) or getattr(ctx, "engine_lock", None)
    return eng, lck


def process_enrollment(frames: list[np.ndarray], code: str, name: str, ctx) -> dict:
    """ctx cung cap: cfg, db, engine/enroll_engine, gallery, threshold."""
    cfg, db = ctx.cfg, ctx.db
    engine, lock = _enroll_engine_and_lock(ctx)
    pose_est = getattr(ctx, "pose", None)
    ecfg, qcfg = cfg["enrollment"], dict(cfg["quality"])
    qcfg["max_yaw"] = max(float(qcfg.get("max_yaw", 90)), 90.0)

    stats = {"frames_total": len(frames), "no_face": 0, "multi_face": 0, "rejected": {}}
    cands = []
    for idx, fr in enumerate(frames):
        small, s = _resize_for_detect(fr)
        if lock is not None:
            with lock:
                faces = engine.detect(small)
        else:
            faces = engine.detect(small)
        if not faces:
            stats["no_face"] += 1
            continue
        for f in faces:
            f.bbox, f.kps = f.bbox / s, f.kps / s
        faces.sort(key=lambda f: f.w * f.h, reverse=True)
        if len(faces) > 1 and faces[1].w * faces[1].h > 0.4 * faces[0].w * faces[0].h:
            stats["multi_face"] += 1
            continue
        f = faces[0]
        pose = pose_est.estimate(fr, f) if pose_est is not None else None
        q = assess(fr, f, qcfg, pose=pose)
        if not q.ok:
            stats["rejected"][q.reason] = stats["rejected"].get(q.reason, 0) + 1
            continue
        aligned = engine.align(fr, f, pose=pose) if "pose" in engine.align.__code__.co_varnames else engine.align(fr, f)
        if lock is not None:
            with lock:
                emb = engine.embed_aligned(aligned)
        else:
            emb = engine.embed_aligned(aligned)
        bname = _pose_bin(q.yaw, q.pitch)
        cands.append({
            "idx": idx, "emb": emb, "q": q, "bin": bname,
            "pose_group": _pose_group_from_bin_or_yaw(bname, q.yaw),
            "crop": crop_with_margin(fr, f.bbox),
        })

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

    return store_enrollment(selected, code, name, ctx, stats, n_good=len(cands) + len(outliers),
                            n_outliers=len(outliers), bin_names=bins)


def store_enrollment(selected: list, code: str, name: str, ctx, stats: dict, *, n_good: int,
                     n_outliers: int, bin_names, guided: bool = False, supplement_only: bool = False) -> dict:
    """Kiem tra trung nhan vien khac, luu embedding + crop kem pose_group va ghi bao cao."""
    cfg, db = ctx.cfg, ctx.db
    engine, _ = _enroll_engine_and_lock(ctx)
    front_items = [c for c in selected if c.get("pose_group", "frontal") == "frontal"] or selected
    center = np.mean([c["emb"] for c in front_items], axis=0)
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

    emp = db.get_employee_by_code(code)
    folder = cfg["_paths"]["enroll"] / code
    if emp:
        emp_id = emp["id"]
        db.x("UPDATE employees SET name=?, status='pending' WHERE id=?", (name, emp_id))
        if not supplement_only:
            db.clear_embeddings(emp_id, engine.model_version)
            shutil.rmtree(folder, ignore_errors=True)
    else:
        emp_id = db.create_employee(code, name)
    folder.mkdir(parents=True, exist_ok=True)
    offset = len(list(folder.glob("*.jpg"))) if supplement_only else 0
    saved_crops: list[str] = []
    for i, c in enumerate(selected):
        fn = f"{offset + i:02d}_{c['bin']}.jpg"
        cv2.imwrite(str(folder / fn), c["crop"])
        rel = f"enroll/{code}/{fn}"
        saved_crops.append(rel)
        grp = c.get("pose_group") or _pose_group_from_bin_or_yaw(c["bin"], getattr(c["q"], "yaw", 0.0))
        db.add_embedding(
            emp_id, c["emb"], engine.model_version, c["q"].score, c["bin"], rel,
            source="supplement" if supplement_only else "enroll",
            pose_group=grp, yaw=float(getattr(c["q"], "yaw", 0.0)), pitch=float(getattr(c["q"], "pitch", 0.0)),
        )

    report = {
        "frames_total": stats["frames_total"], "frames_good": n_good,
        "frames_selected": len(selected), "no_face": stats["no_face"], "multi_face": stats["multi_face"],
        "rejected": stats["rejected"], "outliers": n_outliers,
        "bins": {b: sum(1 for c in selected if c["bin"] == b) for b in bin_names},
        "min_pair_cosine": round(min((float(a["emb"] @ b["emb"]) for i, a in enumerate(selected)
                                      for b in selected[i + 1:]), default=1.0), 3),
        "warnings": warnings, "model_version": engine.model_version,
        "guided": guided, "supplement_only": supplement_only,
        "crops": saved_crops,
    }
    if not supplement_only and len(set(bin_names)) < 3:
        report["warnings"].append("Ít góc nhìn (<3). Nên quay lại: nhìn thẳng, xoay trái/phải, ngẩng/cúi nhẹ.")
    db.add_enrollment(emp_id, report)
    report["employee_id"] = emp_id
    log.info("Dang ky %s (%s): chon %d/%d frame (supplement=%s)", name, code, len(selected), stats["frames_total"], supplement_only)
    return report


def backfill_legacy_embeddings(ctx, stop_event=None) -> int:
    """Tu dong trich xuat lai dac trung tu anh mau trong data/enroll/ khi doi sang model_version moi (T028)."""
    db = ctx.db
    engine, lock = _enroll_engine_and_lock(ctx)
    pose_est = getattr(ctx, "pose", None)
    enroll_root: Path = ctx.cfg["_paths"]["enroll"]
    migrated = 0
    for emp in db.q("SELECT id, code FROM employees"):
        if stop_event is not None and stop_event.is_set():
            break
        has_cur = db.q("SELECT COUNT(*) AS n FROM embeddings WHERE employee_id=? AND model_version=?",
                       (emp["id"], engine.model_version))[0]["n"]
        if has_cur > 0:
            continue
        emp_dir = enroll_root / emp["code"]
        if not emp_dir.is_dir():
            continue
        added = 0
        for img_p in sorted(emp_dir.glob("*.jpg")):
            img = cv2.imread(str(img_p))
            if img is None:
                continue
            faces = engine.detect(img)
            if not faces:
                continue
            f = max(faces, key=lambda x: x.w * x.h)
            pose = pose_est.estimate(img, f) if pose_est is not None else None
            aligned = engine.align(img, f, pose=pose) if "pose" in engine.align.__code__.co_varnames else engine.align(img, f)
            if lock is None:
                emb = engine.embed_aligned(aligned)
            else:
                with lock:
                    emb = engine.embed_aligned(aligned)
            bname = "_".join(img_p.stem.split("_")[1:]) or "thang-giua"
            grp = _pose_group_from_bin_or_yaw(bname, pose.yaw if pose is not None else 0.0)
            db.add_embedding(emp["id"], emb, engine.model_version, 0.85, bname,
                             f"enroll/{emp['code']}/{img_p.name}", source="backfill", pose_group=grp)
            added += 1
        if added > 0:
            migrated += 1
    return migrated
