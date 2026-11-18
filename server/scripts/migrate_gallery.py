"""Tao embedding cho model_version moi tu cac crop dang ky da luu (feature 002, T024).

    python server/scripts/migrate_gallery.py --from yunet2023mar+edgeface_xs_gamma_06_v41 --to auto --dry-run
    python server/scripts/migrate_gallery.py --from yunet2023mar+edgeface_xs_gamma_06_v41 --to auto
    python server/scripts/migrate_gallery.py --from ... --to auto --mirror-profiles   # them ban lat guong cho mau nghieng

- Idempotent theo (nhan vien, crop_path, source): chay lai khong nhan doi; nhan vien da co MOT PHAN mau o version moi
  (vd chi co mau chinh dien) van duoc bo sung cac crop con thieu (vd mau nghieng).
- KHONG xoa hang cu. Crop chi duoc doc trong RAM.
- --mirror-profiles: moi mau nghieng mot ben them ban lat guong vao nhom nghieng ben kia (source=migrate_mirror).
  Mac dinh TAT: chua do anh huong toi FAR tren replay K0 (Hien phap II).
- Exit 1 neu co nhan vien needs_reenroll (khong tao duoc vector nao tu crop).
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

MIRROR_GROUP = {"left_profile": "right_profile", "right_profile": "left_profile"}


@dataclass
class MigrateResult:
    migrated: list[str] = field(default_factory=list)
    needs_reenroll: list[str] = field(default_factory=list)
    up_to_date: list[str] = field(default_factory=list)
    vectors_added: int = 0


def _embed_crop(engine, pose_est, img: np.ndarray, pose_group: str) -> np.ndarray | None:
    h, w = img.shape[:2]
    if (h, w) == (112, 112):
        return engine.embed_aligned(img)
    faces = engine.detect(img)
    if not faces:
        return None
    f = max(faces, key=lambda x: x.w * x.h)
    pose = pose_est.estimate(img, f) if pose_est is not None else None
    aligned = engine.align(img, f, pose=pose, pose_group=pose_group)
    return engine.embed_aligned(aligned)


def migrate(db, engine, data_dir: Path, from_version: str, to_version: str | None = None,
            pose_est=None, dry_run: bool = False, mirror_profiles: bool = False,
            actor: str = "migrate_gallery") -> MigrateResult:
    to_version = to_version or engine.model_version
    data_dir = Path(data_dir)
    res = MigrateResult()
    emps = db.q(
        "SELECT DISTINCT e.id, e.code FROM employees e JOIN embeddings m ON m.employee_id=e.id "
        "WHERE m.model_version=? AND e.status IN ('active','pending') ORDER BY e.id", (from_version,))
    for emp in emps:
        rows = db.q(
            "SELECT pose_group, pose_bin, quality, crop_path, yaw, pitch FROM embeddings "
            "WHERE employee_id=? AND model_version=? ORDER BY id", (emp["id"], from_version))
        have = {(r["crop_path"], r["source"]) for r in db.q(
            "SELECT crop_path, source FROM embeddings WHERE employee_id=? AND model_version=?",
            (emp["id"], to_version))}
        have_paths = {p for p, _ in have}
        added, failed, todo = 0, 0, 0
        for r in rows:
            crop = r["crop_path"]
            if not crop:
                continue
            want_main = crop not in have_paths
            want_mirror = mirror_profiles and r["pose_group"] in MIRROR_GROUP and (crop, "migrate_mirror") not in have
            if not (want_main or want_mirror):
                continue
            todo += 1
            img = cv2.imread(str(data_dir / crop))
            if img is None:
                failed += 1
                continue
            jobs = []
            if want_main:
                jobs.append((img, r["pose_group"], "migrate", r["yaw"]))
            if want_mirror:
                jobs.append((cv2.flip(img, 1), MIRROR_GROUP[r["pose_group"]], "migrate_mirror",
                             -r["yaw"] if r["yaw"] is not None else None))
            for im, grp, src, yaw in jobs:
                vec = _embed_crop(engine, pose_est, im, grp)
                if vec is None:
                    failed += 1
                    continue
                if not dry_run:
                    db.add_embedding(emp["id"], vec, to_version, float(r["quality"] or 0.5), r["pose_bin"] or grp, crop,
                                     source=src, pose_group=grp, yaw=float(yaw or 0.0), pitch=float(r["pitch"] or 0.0))
                added += 1
        total_new = db.q("SELECT COUNT(*) AS n FROM embeddings WHERE employee_id=? AND model_version=?",
                         (emp["id"], to_version))[0]["n"]
        if todo == 0:
            res.up_to_date.append(emp["code"])
            status = "up_to_date"
        elif added == 0 and total_new == 0:
            res.needs_reenroll.append(emp["code"])
            status = "needs_reenroll"
        else:
            res.migrated.append(emp["code"])
            status = "migrated"
        res.vectors_added += added
        if not dry_run:
            db.add_audit_log(actor, "gallery_migrate", "employee", str(emp["id"]), {
                "code": emp["code"], "from_version": from_version, "to_version": to_version,
                "n_vectors": added, "failed": failed, "mirror_profiles": mirror_profiles, "status": status})
    return res


def main() -> None:
    from server.api.security import BiometricCipher
    from server.config import load_config
    from server.store.db import DB
    from server.vision.engines import create_engine
    from server.vision.pose3d import create_pose_estimator

    ap = argparse.ArgumentParser(description="Tạo embedding cho model mới từ crop đăng ký đã lưu")
    ap.add_argument("--from", dest="from_version", required=True, help="model_version nguồn")
    ap.add_argument("--to", dest="to_version", default="auto", help="'auto' = model_version của engine hiện tại")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--mirror-profiles", action="store_true",
                    help="thêm bản lật gương của mẫu nghiêng vào nhóm nghiêng bên kia (mặc định tắt)")
    args = ap.parse_args()
    cfg = load_config()
    db = DB(cfg["_paths"]["db"], database_url=cfg["storage"].get("database_url", ""),
            cipher=BiometricCipher.from_env_or_cfg(cfg))
    engine = create_engine(cfg)
    to_version = engine.model_version if args.to_version == "auto" else args.to_version
    res = migrate(db, engine, cfg["_paths"]["data"], args.from_version, to_version,
                  pose_est=create_pose_estimator(cfg), dry_run=args.dry_run, mirror_profiles=args.mirror_profiles)
    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}{args.from_version} -> {to_version}")
    print(f"{tag}migrated={len(res.migrated)} needs_reenroll={len(res.needs_reenroll)} "
          f"up_to_date={len(res.up_to_date)} vectors_added={res.vectors_added}")
    if res.needs_reenroll:
        print("Cần HR mời đăng ký lại:", ", ".join(res.needs_reenroll))
        sys.exit(1)


if __name__ == "__main__":
    main()
