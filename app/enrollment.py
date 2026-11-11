"""Dang ky khuon mat tu video ngan hoac phien co huong dan (ho tro toi goc nghieng 90 do + backfill du lieu cu)."""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import cv2
import numpy as np

from .pipeline import crop_with_margin
from .pose3d import classify_pose_group
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


def _group_from_bin_name(bin_name: str) -> tuple[str, float]:
    if "sau-trai" in bin_name or "nghieng-trai" in bin_name:
        return "left_profile", -75.0
    if "sau-phai" in bin_name or "nghieng-phai" in bin_name:
        return "right_profile", 75.0
    return "frontal", 0.0


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
    """ctx cung cap: cfg, db, engine, engine_lock, gallery, threshold, (tuy chon) pose."""
    cfg, db, engine = ctx.cfg, ctx.db, ctx.engine
    pose_est = getattr(ctx, "pose", None)
    frontal_max = float(cfg["recognition"].get("frontal_max_yaw", 40.0))
    ecfg, qcfg = cfg["enrollment"], dict(cfg["quality"])
    qcfg["max_yaw"] = max(float(qcfg.get("max_yaw", 90)), 90.0)

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
            stats["multi_face"] += 1
            continue
        f = faces[0]
        with ctx.engine_lock:
            pose = pose_est.estimate(fr, f) if pose_est is not None else None
        q = assess(fr, f, qcfg, pose=pose)
        if not q.ok:
            stats["rejected"][q.reason] = stats["rejected"].get(q.reason, 0) + 1
            continue
        grp = pose.pose_group if pose is not None else classify_pose_group(q.yaw, frontal_max=frontal_max)
        with ctx.engine_lock:
            emb = engine.embed_aligned(engine.align(fr, f, pose=pose, pose_group=grp))
        cands.append({
            "idx": idx,
            "emb": emb,
            "q": q,
            "pose_group": grp,
            "bin": _pose_bin(q.yaw, q.pitch),
            "crop": crop_with_margin(fr, f.bbox),
        })

    min_n, max_n = int(ecfg["min_frames"]), int(ecfg["max_frames"])
    if len(cands) < min_n:
        raise EnrollError(f"Chỉ có {len(cands)} frame đạt chất lượng (cần ≥ {min_n}). "
                          f"Quay lại nơi đủ sáng, mặt to rõ, giữ máy ổn định. Chi tiết: {stats}")

    # Kiem tra nhat quan TACH RIENG TRONG TUNG POSE_GROUP (khong loai nham mau 80° vi khac vector 0°)
    out_cos = float(ecfg.get("outlier_min_cosine", 0.42))
    by_grp: dict[str, list] = {}
    for c in cands:
        by_grp.setdefault(c["pose_group"], []).append(c)
    kept, outliers = [], []
    for grp, items in by_grp.items():
        if len(items) <= 1:
            items[0]["sim_center"] = 1.0
            kept.extend(items)
            continue
        mean = np.mean([c["emb"] for c in items], axis=0)
        mean /= np.linalg.norm(mean) + 1e-9
        for c in items:
            c["sim_center"] = float(c["emb"] @ mean)
            (kept if c["sim_center"] >= out_cos else outliers).append(c)

    if len(outliers) > 0.35 * (len(kept) + len(outliers)) or len(kept) < min_n:
        raise EnrollError("Các frame không nhất quán (có thể nhiều người trong video). Hãy quay lại chỉ một người.")
    cands = kept

    # Chon theo o goc nhin, loai trung lap TRONG CUNG POSE_GROUP
    bins: dict[str, list] = {}
    for c in cands:
        bins.setdefault(c["bin"], []).append(c)
    for b in bins.values():
        b.sort(key=lambda c: c["q"].score, reverse=True)
    selected: list = []
    per_bin, dd = int(ecfg["per_bin"]), float(ecfg["dedup_cosine"])
    for rnd in range(per_bin):
        for b in sorted(bins, key=lambda k: (k != "thang-giua", k)):
            if len(selected) >= max_n:
                break
            if rnd < len(bins[b]):
                c = bins[b][rnd]
                same_grp_sel = [s for s in selected if s["pose_group"] == c["pose_group"]]
                if all(float(c["emb"] @ s["emb"]) < dd for s in same_grp_sel):
                    selected.append(c)
    if len(selected) < min_n:
        rest = sorted((c for c in cands if c not in selected), key=lambda c: c["q"].score, reverse=True)
        selected += rest[: min_n - len(selected)]

    return store_enrollment(selected, code, name, ctx, stats, n_good=len(cands) + len(outliers),
                            n_outliers=len(outliers), bin_names=bins)


def store_enrollment(
    selected: list,
    code: str,
    name: str,
    ctx,
    stats: dict,
    *,
    n_good: int,
    n_outliers: int,
    bin_names,
    guided: bool = False,
    supplement_only: bool = False,
) -> dict:
    """Kiem tra trung nhan vien khac theo tung pose_group, luu embedding + crop va ghi bao cao."""
    cfg, db, engine = ctx.cfg, ctx.db, ctx.engine
    frontal_max = float(cfg["recognition"].get("frontal_max_yaw", 40.0))
    for c in selected:
        c.setdefault("pose_group", classify_pose_group(c["q"].yaw, frontal_max=frontal_max))

    warnings = []
    others = db.load_gallery(engine.model_version, statuses=("active", "pending"))
    best_other: dict[str, float] = {}
    for grp in ("frontal", "left_profile", "right_profile"):
        grp_vecs = [c["emb"] for c in selected if c["pose_group"] == grp]
        if not grp_vecs:
            continue
        center = np.mean(grp_vecs, axis=0)
        center /= np.linalg.norm(center) + 1e-9
        for r in others:
            if r["code"] == code or (r.get("pose_group") or "frontal") != grp:
                continue
            s = float(r["vec"] @ center)
            if s > best_other.get(r["code"], -1.0):
                best_other[r["code"]] = s
    dup = [(k, v) for k, v in best_other.items() if v >= ctx.threshold]
    if dup:
        k, v = max(dup, key=lambda kv: kv[1])
        warnings.append(f"Khuôn mặt giống nhân viên {k} (cosine {v:.2f}). HR cần kiểm tra trước khi duyệt.")

    emp = db.get_employee_by_code(code)
    folder = cfg["_paths"]["enroll"] / code
    if emp:
        emp_id = emp["id"]
        if supplement_only:
            # Giữ nguyên trạng thái duyệt nếu đang active, chỉ cập nhật nhóm góc vừa quay bổ sung
            updated_groups = tuple(sorted({c["pose_group"] for c in selected}))
            db.clear_embeddings(emp_id, engine.model_version, pose_groups=updated_groups)
            db.x("UPDATE employees SET name=? WHERE id=?", (name, emp_id))
        else:
            db.x("UPDATE employees SET name=?, status='pending' WHERE id=?", (name, emp_id))
            db.clear_embeddings(emp_id, engine.model_version)
            shutil.rmtree(folder, ignore_errors=True)
    else:
        emp_id = db.create_employee(code, name)

    folder.mkdir(parents=True, exist_ok=True)
    existing_idx = len(list(folder.glob("*.jpg"))) if supplement_only else 0
    saved_crops = []
    src_tag = "enroll_supplement" if supplement_only else "enroll"
    for i, c in enumerate(selected):
        fn = f"{existing_idx + i:02d}_{c['bin']}.jpg"
        cv2.imwrite(str(folder / fn), c["crop"])
        rel_crop = f"enroll/{code}/{fn}"
        saved_crops.append(rel_crop)
        db.add_embedding(
            emp_id, c["emb"], engine.model_version, c["q"].score, c["bin"], rel_crop,
            source=src_tag, pose_group=c["pose_group"], yaw=float(c["q"].yaw), pitch=float(c["q"].pitch),
        )

    same_grp_pairs = [
        float(a["emb"] @ b["emb"])
        for i, a in enumerate(selected)
        for b in selected[i + 1:]
        if a["pose_group"] == b["pose_group"]
    ]
    cov_groups = sorted({r["pose_group"] for r in db.q(
        "SELECT DISTINCT pose_group FROM embeddings WHERE employee_id=? AND model_version=?",
        (emp_id, engine.model_version),
    )})
    report = {
        "frames_total": stats["frames_total"], "frames_good": n_good,
        "frames_selected": len(selected), "no_face": stats["no_face"], "multi_face": stats["multi_face"],
        "rejected": stats["rejected"], "outliers": n_outliers,
        "bins": {b: sum(1 for c in selected if c["bin"] == b) for b in bin_names},
        "pose_groups": cov_groups,
        "ready_90": set(("frontal", "left_profile", "right_profile")).issubset(cov_groups),
        "min_pair_cosine": round(min(same_grp_pairs, default=1.0), 3),
        "warnings": warnings, "model_version": engine.model_version,
        "guided": guided, "supplement_only": supplement_only,
        "crops": saved_crops,
    }
    if not supplement_only and len(set(bin_names)) < 3:
        report["warnings"].append("Ít góc nhìn (<3). Nên quay lại: nhìn thẳng, xoay trái/phải, ngẩng/cúi nhẹ.")
    if not report["ready_90"]:
        report["warnings"].append("Chưa có đủ 2 góc nghiêng sâu (±80°) — chỉ nhận diện tối ưu ở góc ≤ 40°.")
    db.add_enrollment(emp_id, report)
    report["employee_id"] = emp_id
    log.info("Dang ky %s (%s): chon %d/%d frame (groups=%s)", name, code, len(selected), stats["frames_total"], cov_groups)
    return report


def backfill_legacy_embeddings(ctx) -> int:
    """Tự động tái trích xuất embedding từ data/enroll/<code>/*.jpg cho nhân viên cũ khi đổi model_version."""
    cfg, db, engine = ctx.cfg, ctx.db, ctx.engine
    pose_est = getattr(ctx, "pose", None)
    enroll_root: Path = cfg["_paths"]["enroll"]
    if not enroll_root.exists():
        return 0

    rows = db.list_employees(engine.model_version)
    missing_emps = [r for r in rows if int(r.get("n_emb") or 0) == 0]
    if not missing_emps:
        return 0

    migrated = 0
    for emp in missing_emps:
        code, emp_id = emp["code"], int(emp["id"])
        emp_dir = enroll_root / code
        if not emp_dir.is_dir():
            continue
        jpgs = sorted(emp_dir.glob("*.jpg"))
        if not jpgs:
            continue
        added_for_emp = 0
        for img_path in jpgs:
            img = cv2.imread(str(img_path))
            if img is None or img.size == 0:
                continue
            m = re.match(r"^\d+_(.+)\.jpg$", img_path.name)
            bin_name = m.group(1) if m else "thang-giua"
            hint_grp, hint_yaw = _group_from_bin_name(bin_name)

            with ctx.engine_lock:
                faces = engine.detect(img)
                if faces:
                    faces.sort(key=lambda f: f.w * f.h, reverse=True)
                    face = faces[0]
                    pose = pose_est.estimate(img, face) if pose_est is not None else None
                    grp = pose.pose_group if pose is not None else hint_grp
                    yaw_val = float(pose.yaw) if pose is not None else hint_yaw
                    pitch_val = float(pose.pitch) if pose is not None else 0.0
                    aligned = engine.align(img, face, pose=pose, pose_group=grp)
                else:
                    # Ảnh crop sát mặt từ phiên bản cũ nếu detector không bắt lại khung -> resize trực tiếp
                    grp, yaw_val, pitch_val = hint_grp, hint_yaw, 0.0
                    aligned = cv2.resize(img, (112, 112))
                emb = engine.embed_aligned(aligned)

            rel_crop = f"enroll/{code}/{img_path.name}"
            db.add_embedding(
                emp_id, emb, engine.model_version, 0.75, bin_name, rel_crop,
                source="backfill", pose_group=grp, yaw=yaw_val, pitch=pitch_val,
            )
            added_for_emp += 1
        if added_for_emp > 0:
            migrated += 1
            log.info("Backfill %d embedding (%s) cho nhan vien %s", added_for_emp, engine.model_version, code)
    return migrated
