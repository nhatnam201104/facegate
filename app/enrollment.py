"""Dang ky khuon mat tu video ngan hoac phien co huong dan (ho tro toi goc nghieng 90 do + backfill du lieu cu)."""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from pathlib import Path

import cv2
import numpy as np

from .pipeline import crop_with_margin
from .pose3d import classify_pose_group
from .quality import assess

log = logging.getLogger("enroll")

DEFAULT_CROSS_POSE_COS = 0.20
MAX_OUTLIER_RATIO = 0.35


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


def filter_consistent_candidates(
    cands: list[dict],
    out_cos: float,
    cross_cos: float,
) -> tuple[list[dict], list[dict]]:
    """Lọc outlier trong từng pose_group và kiểm tra chéo với tâm frontal (không miễn trừ nhóm 1 frame)."""
    by_grp: dict[str, list[dict]] = {}
    for c in cands:
        by_grp.setdefault(c["pose_group"], []).append(c)

    has_profile = any(g != "frontal" for g in by_grp)
    frontal_items = by_grp.get("frontal", [])
    if has_profile and not frontal_items:
        raise EnrollError("Video có góc nghiêng nhưng thiếu góc nhìn thẳng để đối chiếu danh tính. Hãy quay từ góc nhìn thẳng trước.")

    kept_frontal: list[dict] = []
    outliers: list[dict] = []
    if frontal_items:
        f_mean = np.mean([c["emb"] for c in frontal_items], axis=0)
        f_mean /= np.linalg.norm(f_mean) + 1e-9
        for c in frontal_items:
            c["sim_center"] = float(c["emb"] @ f_mean)
            (kept_frontal if (len(frontal_items) == 1 or c["sim_center"] >= out_cos) else outliers).append(c)

    if has_profile and not kept_frontal:
        raise EnrollError("Các frame góc nhìn thẳng không nhất quán. Hãy quay lại chỉ một người.")

    front_center = None
    if kept_frontal:
        front_center = np.mean([c["emb"] for c in kept_frontal], axis=0)
        front_center /= np.linalg.norm(front_center) + 1e-9

    kept: list[dict] = list(kept_frontal)
    for grp, items in by_grp.items():
        if grp == "frontal":
            continue
        g_mean = np.mean([c["emb"] for c in items], axis=0)
        g_mean /= np.linalg.norm(g_mean) + 1e-9
        for c in items:
            sim_grp = float(c["emb"] @ g_mean)
            cross_vec = c.get("cross_emb", c["emb"])
            sim_front = (
                max(
                    float(cross_vec @ front_center),
                    max((float(cross_vec @ fc["emb"]) for fc in kept_frontal), default=-1.0),
                    float(c["emb"] @ front_center),
                )
                if front_center is not None
                else 0.0
            )
            c["sim_center"] = round(min(sim_grp, sim_front) if len(items) > 1 else sim_front, 4)
            intra_ok = (len(items) == 1) or (sim_grp >= out_cos)
            cross_ok = (front_center is not None) and (sim_front >= cross_cos)
            (kept if (intra_ok and cross_ok) else outliers).append(c)
    return kept, outliers


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
            cross_emb = (
                engine.embed_aligned(engine.align(fr, f, pose=pose, pose_group="frontal"))
                if grp != "frontal"
                else emb
            )
        cands.append({
            "idx": idx,
            "emb": emb,
            "cross_emb": cross_emb,
            "q": q,
            "pose_group": grp,
            "bin": _pose_bin(q.yaw, q.pitch),
            "crop": crop_with_margin(fr, f.bbox),
        })

    min_n, max_n = int(ecfg["min_frames"]), int(ecfg["max_frames"])
    if len(cands) < min_n:
        raise EnrollError(f"Chỉ có {len(cands)} frame đạt chất lượng (cần ≥ {min_n}). "
                          f"Quay lại nơi đủ sáng, mặt to rõ, giữ máy ổn định. Chi tiết: {stats}")

    # Kiem tra nhat quan trong tung pose_group kem doi chieu cheo voi tam frontal
    out_cos = float(ecfg.get("outlier_min_cosine", 0.42))
    cross_cos = float(ecfg.get("cross_pose_min_cosine", DEFAULT_CROSS_POSE_COS))
    kept, outliers = filter_consistent_candidates(cands, out_cos, cross_cos)

    if len(outliers) > MAX_OUTLIER_RATIO * (len(kept) + len(outliers)) or len(kept) < min_n:
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
    data_root = cfg["_paths"]["data"]
    if emp:
        emp_id = emp["id"]
        if supplement_only:
            updated_groups = tuple(sorted({c["pose_group"] for c in selected}))
            if updated_groups:
                ph = ",".join("?" * len(updated_groups))
                old_crops = db.q(
                    f"SELECT crop_path FROM embeddings WHERE employee_id=? AND model_version=? AND pose_group IN ({ph})",
                    (emp_id, engine.model_version, *updated_groups),
                )
                db.clear_embeddings(emp_id, engine.model_version, pose_groups=updated_groups)
                for r in old_crops:
                    cp = r.get("crop_path")
                    if cp:
                        (folder / Path(cp).name).unlink(missing_ok=True)
            if warnings:
                db.x("UPDATE employees SET status='pending', approved_at=NULL WHERE id=?", (emp_id,))
            elif emp.get("status") != "active":
                db.x("UPDATE employees SET name=? WHERE id=?", (name, emp_id))
        else:
            db.x("UPDATE employees SET name=?, status='pending' WHERE id=?", (name, emp_id))
            db.clear_embeddings(emp_id, engine.model_version)
            shutil.rmtree(folder, ignore_errors=True)
    else:
        emp_id = db.create_employee(code, name)

    folder.mkdir(parents=True, exist_ok=True)
    if supplement_only:
        prefixes = [
            int(m.group(1))
            for p in folder.glob("*.jpg")
            if (m := re.match(r"^(\d+)_", p.name))
        ]
        existing_idx = (max(prefixes) + 1) if prefixes else 0
    else:
        existing_idx = 0
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


def _parse_report(raw) -> dict | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            obj = json.loads(raw)
            return obj if isinstance(obj, dict) else None
        except ValueError:
            return None
    return None


def _record_backfill_warning(
    db, emp_id: int, model_version: str, reason: str,
    skipped_no_face: int = 0, prev_report: dict | None = None,
) -> None:
    base = dict(prev_report) if isinstance(prev_report, dict) else {}
    prev_warnings = [w for w in (base.get("warnings") or []) if w != reason]
    db.add_enrollment(emp_id, {
        "frames_total": base.get("frames_total", 0),
        "frames_good": base.get("frames_good", 0),
        "frames_selected": base.get("frames_selected", 0),
        "no_face": base.get("no_face", skipped_no_face),
        "multi_face": base.get("multi_face", 0),
        "rejected": base.get("rejected", {}),
        "outliers": base.get("outliers", 0),
        "bins": base.get("bins", {}),
        "pose_groups": base.get("pose_groups", []),
        "ready_90": False,
        "min_pair_cosine": base.get("min_pair_cosine", 0.0),
        "warnings": [*prev_warnings, reason],
        "model_version": base.get("model_version", model_version),
        "backfill_failed_model": model_version,
        "guided": base.get("guided", False),
        "supplement_only": base.get("supplement_only", False),
        "crops": base.get("crops", []),
    })


def backfill_legacy_embeddings(ctx, stop_event: threading.Event | None = None) -> int:
    """Tự động tái trích xuất embedding từ data/enroll/<code>/*.jpg cho nhân viên cũ khi đổi model_version."""
    if stop_event is not None and stop_event.is_set():
        return 0
    cfg, db, engine = ctx.cfg, ctx.db, ctx.engine
    pose_est = getattr(ctx, "pose", None)
    enroll_root: Path = cfg["_paths"]["enroll"]
    if not enroll_root.exists():
        return 0

    rows = db.list_employees(engine.model_version)
    missing_emps = [r for r in rows if int(r.get("n_emb") or 0) == 0]
    if not missing_emps:
        return 0

    ecfg = cfg.get("enrollment", {})
    out_cos = float(ecfg.get("outlier_min_cosine", 0.42))
    cross_cos = float(ecfg.get("cross_pose_min_cosine", DEFAULT_CROSS_POSE_COS))
    qcfg = dict(cfg.get("quality", {}))
    qcfg["max_yaw"] = max(float(qcfg.get("max_yaw", 90)), 90.0)

    migrated = 0
    for emp in missing_emps:
        if stop_event is not None and stop_event.is_set():
            break
        code, emp_id = emp["code"], int(emp["id"])
        prev_rep = _parse_report(emp.get("report"))
        if prev_rep and prev_rep.get("backfill_failed_model") == engine.model_version:
            continue
        emp_dir = enroll_root / code
        if not emp_dir.is_dir():
            continue
        jpgs = sorted(emp_dir.glob("*.jpg"))
        if not jpgs:
            continue

        cands: list[dict] = []
        skipped_no_face = 0
        rejected_q = 0
        for img_path in jpgs:
            if stop_event is not None and stop_event.is_set():
                return migrated
            img = cv2.imread(str(img_path))
            if img is None or img.size == 0:
                continue
            m = re.match(r"^\d+_(.+)\.jpg$", img_path.name)
            bin_name = m.group(1) if m else "thang-giua"
            hint_grp, hint_yaw = _group_from_bin_name(bin_name)

            # Đệm viền đủ rộng cho ảnh crop sát lề từ bản cũ để detector và bộ lọc biên ('cut') hoạt động chính xác
            pad = max(24, int(max(img.shape[:2]) * 0.20))
            padded = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
            with ctx.engine_lock:
                faces = engine.detect(padded)
                if not faces:
                    skipped_no_face += 1
                    log.warning("Backfill bo qua anh khong phat hien duoc mat: %s/%s", code, img_path.name)
                    continue
                faces.sort(key=lambda f: f.w * f.h, reverse=True)
                face = faces[0]
                pose = pose_est.estimate(padded, face) if pose_est is not None else None
            q = assess(padded, face, qcfg, pose=pose)
            if not q.ok:
                rejected_q += 1
                log.warning("Backfill bo qua anh chat luong thap (%s): %s/%s", q.reason, code, img_path.name)
                continue
            grp = pose.pose_group if pose is not None else hint_grp
            yaw_val = float(pose.yaw) if pose is not None else hint_yaw
            pitch_val = float(pose.pitch) if pose is not None else float(q.pitch if q else 0.0)
            with ctx.engine_lock:
                aligned = engine.align(padded, face, pose=pose, pose_group=grp)
                emb = engine.embed_aligned(aligned)
                cross_emb = (
                    engine.embed_aligned(engine.align(padded, face, pose=pose, pose_group="frontal"))
                    if grp != "frontal"
                    else emb
                )
            cands.append({
                "emb": emb,
                "cross_emb": cross_emb,
                "q": q,
                "bin": bin_name,
                "pose_group": grp,
                "yaw": yaw_val,
                "pitch": pitch_val,
                "rel_crop": f"enroll/{code}/{img_path.name}",
            })

        if stop_event is not None and stop_event.is_set():
            break

        if not cands:
            with db.lock:
                still_missing = db.q(
                    "SELECT id FROM employees WHERE id=? AND NOT EXISTS (SELECT 1 FROM embeddings WHERE employee_id=? AND model_version=?)",
                    (emp_id, emp_id, engine.model_version),
                )
                if still_missing:
                    _record_backfill_warning(
                        db, emp_id, engine.model_version,
                        f"Backfill không trích xuất được khuôn mặt hợp lệ (bỏ qua {skipped_no_face} ảnh không thấy mặt, {rejected_q} ảnh chất lượng thấp) — cần đăng ký lại.",
                        skipped_no_face=skipped_no_face, prev_report=prev_rep,
                    )
            continue

        try:
            kept, outliers = filter_consistent_candidates(cands, out_cos, cross_cos)
            if len(outliers) > MAX_OUTLIER_RATIO * (len(kept) + len(outliers)) or not kept:
                raise EnrollError("Các ảnh mẫu cũ không nhất quán giữa các góc nhìn")
        except EnrollError as e:
            log.warning("Backfill tu choi nhan vien %s vi khong nhat quan (%s) -> can dang ky lai", code, e)
            with db.lock:
                still_missing = db.q(
                    "SELECT id FROM employees WHERE id=? AND NOT EXISTS (SELECT 1 FROM embeddings WHERE employee_id=? AND model_version=?)",
                    (emp_id, emp_id, engine.model_version),
                )
                if still_missing:
                    _record_backfill_warning(
                        db, emp_id, engine.model_version,
                        f"Ảnh mẫu cũ không nhất quán ({e}) — cần đăng ký lại.",
                        skipped_no_face=skipped_no_face, prev_report=prev_rep,
                    )
            continue

        # Kiểm tra nguyên tử trong db.lock: chỉ ghi nếu nhân viên chưa bị xóa và chưa được HR đăng ký mới trong lúc chờ
        with db.lock:
            still_missing = db.q(
                "SELECT id FROM employees WHERE id=? AND NOT EXISTS (SELECT 1 FROM embeddings WHERE employee_id=? AND model_version=?)",
                (emp_id, emp_id, engine.model_version),
            )
            if not still_missing:
                continue
            for c in kept:
                db.add_embedding(
                    emp_id, c["emb"], engine.model_version, float(c["q"].score), c["bin"], c["rel_crop"],
                    source="backfill", pose_group=c["pose_group"], yaw=c["yaw"], pitch=c["pitch"],
                )
        migrated += 1
        log.info(
            "Backfill %d embedding (%s) cho nhan vien %s (bo qua khong mat=%d, lech=%d)",
            len(kept), engine.model_version, code, skipped_no_face, len(outliers),
        )
    return migrated
