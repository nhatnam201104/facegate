import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(val)))


def _apply_v4_defaults(cfg: dict) -> dict:
    cam = cfg.setdefault("camera", {})
    cam.setdefault("source", 0)
    cam.setdefault("name", "gate-1")
    cam.setdefault("id", cam.get("name", "gate-1"))
    cam.setdefault("loop_file", True)
    cam.setdefault("model_profile", "tapo_c310")
    cam.setdefault("main_source", "rtsp://127.0.0.1:8554/gate1_main")
    cam.setdefault("sub_source", "rtsp://127.0.0.1:8554/gate1_sub")
    cam.setdefault("whep_url", "/media/gate1_main/whep")
    cam.setdefault("playback_url", "http://127.0.0.1:9996")
    cam["overlay_offset_ms"] = float(cam.get("overlay_offset_ms", 0.0))

    pl = cfg.setdefault("pipeline", {})
    pl.setdefault("backend", "edgeface")
    pl.setdefault("execution_provider", "cpu")
    pl["detect_fps"] = _clamp(pl.get("detect_fps", 6.0), 0.5, 30.0)
    pl["det_score"] = _clamp(pl.get("det_score", 0.55), 0.2, 0.99)
    pl["max_faces"] = int(_clamp(pl.get("max_faces", 10), 1, 32))
    pl["stream_fps"] = _clamp(pl.get("stream_fps", 15.0), 1.0, 60.0)
    pl["stream_width"] = int(_clamp(pl.get("stream_width", 960), 320, 2560))
    pl.setdefault("two_stage_roi", True)
    pl["fallback_det_width"] = int(_clamp(pl.get("fallback_det_width", 1280), 480, 2560))
    pl.setdefault("isolated_worker", False)  # True khi chay che do worker_perception rieng

    q = cfg.setdefault("quality", {})
    q.setdefault("min_face", 60)
    q.setdefault("min_blur", 35)
    q.setdefault("min_brightness", 45)
    q.setdefault("max_brightness", 225)
    q.setdefault("max_yaw", 90)
    q.setdefault("max_pitch", 35)
    q.setdefault("max_roll", 35)

    rc = cfg.setdefault("recognition", {})
    rc.setdefault("frontal_max_yaw", 40)
    rc.setdefault("pose_hysteresis", 5)
    thrs = rc.setdefault("thresholds", {})
    thrs.setdefault("edgeface", {"frontal": 0.40, "profile": 0.46})
    thrs.setdefault("opencv", {"frontal": 0.42, "profile": 0.48})
    thrs.setdefault("onnx", {"frontal": 0.40, "profile": 0.46})
    rc.setdefault("margin", 0.06)
    rc.setdefault("profile_margin", 0.08)
    rc.setdefault("min_good_frames", 2)
    rc.setdefault("min_profile_frames", 4)
    rc.setdefault("max_track_embeddings", 10)
    rc.setdefault("track_max_age", 2.0)
    rc.setdefault("split_cosine", 0.25)

    dec = cfg.setdefault("decision", {})
    dec["novelty_min_dt_ms"] = float(dec.get("novelty_min_dt_ms", 250.0))
    dec["novelty_min_dyaw_deg"] = float(dec.get("novelty_min_dyaw_deg", 8.0))
    lw = dec.setdefault("llr_weights", {})
    lw["a_cos"] = max(0.01, float(lw.get("a_cos", 12.0)))
    lw["b_bias"] = float(lw.get("b_bias", -4.8))
    lw["c_abs_yaw"] = float(lw.get("c_abs_yaw", -0.015))
    lw["d_quality"] = float(lw.get("d_quality", 0.60))
    dec["tau_threshold"] = float(dec.get("tau_threshold", 0.45))
    dec["margin_base"] = float(dec.get("margin_base", 0.70))
    dec["margin_yaw_slope"] = float(dec.get("margin_yaw_slope", 0.30))
    dec["auto_enroll_online"] = False  # H17: cam tuyet doi auto-enroll online chong dau doc mau

    trk = cfg.setdefault("tracking", {})
    trk["crossing_face_iou"] = _clamp(trk.get("crossing_face_iou", 0.25), 0.05, 0.95)
    trk["crossing_person_iou"] = _clamp(trk.get("crossing_person_iou", 0.40), 0.05, 0.95)
    trk["viewport_bottom_grace"] = _clamp(trk.get("viewport_bottom_grace", 0.80), 0.50, 1.0)

    pw = cfg.setdefault("power", {})
    pw.setdefault("sleep", True)
    pw.setdefault("active_windows", [["06:30", "09:30"], ["16:30", "18:30"]])
    pw.setdefault("idle_seconds", 30)
    pw.setdefault("motion_threshold", 0.01)
    pw.setdefault("sleep_check_fps", 4)
    pw.setdefault("confirm_seconds", 1.0)

    enr = cfg.setdefault("enrollment", {})
    enr.setdefault("sample_fps", 10)
    enr.setdefault("min_frames", 5)
    enr.setdefault("max_frames", 21)
    enr.setdefault("per_bin", 3)
    enr.setdefault("dedup_cosine", 0.95)
    enr.setdefault("outlier_min_cosine", 0.42)
    enr.setdefault("cross_pose_min_cosine", 0.20)
    enr.setdefault("keep_video", False)
    gd = enr.setdefault("guided", {})
    gd.setdefault("enable_profile_90", True)
    gd.setdefault("profile_yaw_min", 65.0)
    gd.setdefault("profile_yaw_max", 88.0)

    aud = cfg.setdefault("audit_and_safety", {})
    aud["profile_audit_sample_rate"] = _clamp(aud.get("profile_audit_sample_rate", 0.05), 0.0, 1.0)
    aud["near_spoof_window_sec"] = float(aud.get("near_spoof_window_sec", 300.0))
    aud["min_free_disk_gb"] = float(aud.get("min_free_disk_gb", 2.0))
    aud["heartbeat_timeout_sec"] = float(aud.get("heartbeat_timeout_sec", 5.0))

    st = cfg.setdefault("storage", {})
    st.setdefault("data_dir", "data")
    st.setdefault("database_url", "")
    return cfg


def load_config(path: Path | None = None) -> dict:
    path = path or Path(os.environ.get("FACEGATE_CONFIG", ROOT / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        cfg = _apply_v4_defaults(yaml.safe_load(f) or {})
    data_dir = ROOT / cfg["storage"]["data_dir"]
    cfg["_paths"] = {
        "root": ROOT,
        "models": ROOT / "models",
        "data": data_dir,
        "db": data_dir / "facegate.db",
        "snapshots": data_dir / "snapshots",
        "enroll": data_dir / "enroll",
        "uploads": data_dir / "uploads",
        "sources": data_dir / "sources",
        "clips": data_dir / "clips",
        "run": data_dir / "run",
    }
    for key in ("data", "snapshots", "enroll", "uploads", "sources", "clips", "run"):
        cfg["_paths"][key].mkdir(parents=True, exist_ok=True)
    return cfg
