import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _normalize_thresholds(raw: dict) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for backend, val in (raw or {}).items():
        if isinstance(val, dict):
            fr = float(val.get("frontal", 0.42))
            pr = float(val.get("profile", round(fr + 0.06, 2)))
            out[backend] = {"frontal": fr, "profile": pr}
        else:
            fr = float(val)
            out[backend] = {"frontal": fr, "profile": round(fr + 0.06, 2)}
    out.setdefault("opencv", {"frontal": 0.42, "profile": 0.48})
    out.setdefault("edgeface", {"frontal": 0.40, "profile": 0.46})
    out.setdefault("onnx", {"frontal": 0.40, "profile": 0.46})
    out.setdefault("adaface", {"frontal": 0.40, "profile": 0.46})
    return out


def _apply_defaults(cfg: dict) -> None:
    q = cfg.setdefault("quality", {})
    q.setdefault("max_yaw", 90)
    q.setdefault("max_pitch", 35)
    q.setdefault("max_roll", 35)

    rc = cfg.setdefault("recognition", {})
    rc["thresholds"] = _normalize_thresholds(rc.get("thresholds", {}))
    rc.setdefault("frontal_max_yaw", 40.0)
    rc.setdefault("pose_hysteresis", 5.0)
    margin = float(rc.get("margin", 0.06))
    rc.setdefault("profile_margin", round(max(margin + 0.02, 0.08), 3))
    rc.setdefault("min_good_frames", 2)
    rc.setdefault("min_profile_frames", 4)
    rc.setdefault("max_track_embeddings", 10)
    rc.setdefault("split_cosine", 0.25)

    lc = cfg.setdefault("liveness", {})
    ctx_cfg = lc.setdefault("context", {})
    ctx_cfg.setdefault("enabled", True)
    ctx_cfg.setdefault("person_conf", 0.45)
    ctx_cfg.setdefault("medium_conf", 0.35)
    ctx_cfg.setdefault("screen_face_overlap", 0.80)
    ctx_cfg.setdefault("screen_window", 5)
    ctx_cfg.setdefault("screen_min_hits", 3)
    ctx_cfg.setdefault("require_body_coupling", True)
    ctx_cfg.setdefault("face_body_ratio", [0.12, 0.65])

    par_cfg = lc.setdefault("parallax", {})
    par_cfg.setdefault("enabled", True)
    par_cfg.setdefault("min_frames", 6)
    par_cfg.setdefault("min_travel_ratio", 0.08)
    par_cfg.setdefault("min_depth_score", 0.55)

    mr_cfg = lc.setdefault("moire", {})
    mr_cfg.setdefault("enabled", True)
    mr_cfg.setdefault("max_screen_score", 0.65)

    ec = cfg.setdefault("enrollment", {})
    ec.setdefault("max_frames", 21)
    ec.setdefault("outlier_min_cosine", 0.42)
    ec.setdefault("cross_pose_min_cosine", 0.20)
    gc = ec.setdefault("guided", {})
    gc.setdefault("enable_profile_90", True)
    gc.setdefault("profile_yaw_min", 65.0)
    gc.setdefault("profile_yaw_max", 88.0)


def load_config(path: Path | None = None) -> dict:
    path = path or Path(os.environ.get("FACEGATE_CONFIG", ROOT / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _apply_defaults(cfg)
    data_dir = ROOT / cfg["storage"]["data_dir"]
    cfg["_paths"] = {
        "root": ROOT,
        "models": ROOT / "models",
        "data": data_dir,
        "db": data_dir / "facegate.db",
        "snapshots": data_dir / "snapshots",
        "enroll": data_dir / "enroll",
        "uploads": data_dir / "uploads",
        "sources": data_dir / "sources",   # video dung lam nguon nhan dien
        "clips": data_dir / "clips",       # clip moi lan xuat hien truoc camera
    }
    for key in ("data", "snapshots", "enroll", "uploads", "sources", "clips"):
        cfg["_paths"][key].mkdir(parents=True, exist_ok=True)
    return cfg
