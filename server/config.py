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
    pl.setdefault("backend", "opencv")
    pl.setdefault("execution_provider", "cpu")
    pl["detect_fps"] = _clamp(pl.get("detect_fps", 6.0), 0.5, 30.0)
    pl["det_score"] = _clamp(pl.get("det_score", 0.55), 0.2, 0.99)
    pl["max_faces"] = int(_clamp(pl.get("max_faces", 10), 1, 32))
    pl["stream_fps"] = _clamp(pl.get("stream_fps", 15.0), 1.0, 60.0)
    pl["stream_width"] = int(_clamp(pl.get("stream_width", 960), 320, 2560))
    pl.setdefault("two_stage_roi", True)
    pl.setdefault("always_fallback_unmatched", True)
    pl["fallback_det_width"] = int(_clamp(pl.get("fallback_det_width", 1280), 480, 2560))
    # Mac dinh bat isolated_worker cho luong mang RTSP/HTTP; webcam USB va file cuc bo giu single-process (ngoai le FR-006)
    src_val = str(cam.get("source", 0)).strip().lower()
    is_network_stream = src_val.startswith(("rtsp://", "rtsps://", "http://", "https://"))
    if "isolated_worker" not in pl:
        pl["isolated_worker"] = is_network_stream
    else:
        pl["isolated_worker"] = bool(pl["isolated_worker"])

    q = cfg.setdefault("quality", {})
    q.setdefault("min_face", 60)
    q.setdefault("min_blur", 35)
    q.setdefault("min_brightness", 45)
    q.setdefault("max_brightness", 225)
    q.setdefault("max_yaw", 90)
    q.setdefault("max_pitch", 35)
    q.setdefault("max_roll", 35)

    # Feature 002: kiem tra giay phep model + uoc luong tu the dau sach giay phep
    lic = cfg.setdefault("license", {})
    lic["research_mode"] = bool(lic.get("research_mode", False))
    pose_cfg = cfg.setdefault("pose", {})
    pose_cfg.setdefault("engine", "pnp5")
    pose_cfg["max_reliable_yaw"] = _clamp(pose_cfg.get("max_reliable_yaw", 60.0), 20.0, 90.0)
    pose_cfg["ema_tau_sec"] = _clamp(pose_cfg.get("ema_tau_sec", 0.3), 0.0, 5.0)

    rc = cfg.setdefault("recognition", {})
    rc.setdefault("frontal_max_yaw", 40)
    rc.setdefault("pose_hysteresis", 5)
    thrs = rc.setdefault("thresholds", {})
    thrs.setdefault("opencv", {"frontal": 0.42, "profile": 0.48})
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
    dec["single_gallery_baseline_llr"] = float(dec.get("single_gallery_baseline_llr", 0.0))
    dec["auto_enroll_online"] = False  # H17: cam tuyet doi auto-enroll online chong dau doc mau

    trk = cfg.setdefault("tracking", {})
    trk["crossing_face_iou"] = _clamp(trk.get("crossing_face_iou", 0.25), 0.05, 0.95)
    trk["crossing_person_iou"] = _clamp(trk.get("crossing_person_iou", 0.40), 0.05, 0.95)
    trk["viewport_bottom_grace"] = _clamp(trk.get("viewport_bottom_grace", 0.80), 0.50, 1.0)
    trk["unattached_confirm_frames"] = int(_clamp(trk.get("unattached_confirm_frames", 4), 1, 30))

    liv = cfg.setdefault("liveness", {})
    liv.setdefault("enabled", True)
    liv.setdefault("threshold", 0.70)
    liv.setdefault("spoof_below", 0.35)
    liv.setdefault("min_frames", 4)
    liv.setdefault("window", 8)
    liv.setdefault("require_frontal_for_checkin", True)

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
    gd.setdefault("profile_yaw_min", 45.0)
    gd.setdefault("profile_yaw_max", 60.0)

    aud = cfg.setdefault("audit_and_safety", {})
    aud["profile_audit_sample_rate"] = _clamp(aud.get("profile_audit_sample_rate", 0.05), 0.0, 1.0)
    aud["near_spoof_window_sec"] = float(aud.get("near_spoof_window_sec", 300.0))
    aud["min_free_disk_gb"] = float(aud.get("min_free_disk_gb", 2.0))
    aud["heartbeat_timeout_sec"] = float(aud.get("heartbeat_timeout_sec", 5.0))

    ap_cfg = cfg.setdefault("audio_prompt", {})
    ap_cfg["enabled"] = bool(ap_cfg.get("enabled", True))
    ap_cfg.setdefault("backend", "web")
    ap_cfg.setdefault("host", "")
    ap_cfg.setdefault("user", "admin")
    ap_cfg.setdefault("password", "")
    ap_cfg.setdefault("cloud_password", "")
    ap_cfg.setdefault("mode", "talk_8800")
    ap_cfg["duration_sec"] = _clamp(ap_cfg.get("duration_sec", 0.85), 0.2, 10.0)
    ap_cfg["spoof_duration_sec"] = _clamp(ap_cfg.get("spoof_duration_sec", 1.2), 0.3, 15.0)
    ap_cfg["alarm_type"] = int(ap_cfg.get("alarm_type", 0))
    ap_cfg.setdefault("alarm_volume", "low")
    ap_cfg["custom_audio_id"] = int(ap_cfg.get("custom_audio_id", 1))
    ap_cfg["profile_wait_sec"] = _clamp(ap_cfg.get("profile_wait_sec", 1.2), 0.2, 10.0)
    ap_cfg["cooldown_sec"] = _clamp(ap_cfg.get("cooldown_sec", 6.0), 0.5, 60.0)
    ap_cfg["spoof_chime"] = bool(ap_cfg.get("spoof_chime", True))
    ap_cfg["spoof_cooldown_sec"] = _clamp(ap_cfg.get("spoof_cooldown_sec", 4.0), 0.5, 60.0)
    ap_cfg["backoff_sec"] = _clamp(ap_cfg.get("backoff_sec", 15.0), 1.0, 300.0)
    ap_cfg.setdefault("prompt_wav", "")

    st = cfg.setdefault("storage", {})
    st.setdefault("data_dir", "data")
    st.setdefault("database_url", "")

    ev = cfg.setdefault("events", {})
    ev.setdefault("direction", "none")
    ev.setdefault("log_unknown", True)
    ev.setdefault("line", [[0.0, 0.5], [1.0, 0.5]])
    ev.setdefault("in_when", "down")

    sku = cfg.setdefault("sku", {})
    sku.setdefault("feature_type", "orb")
    sku["orb_nfeatures"] = int(_clamp(sku.get("orb_nfeatures", 800), 100, 5000))
    sku["sift_nfeatures"] = int(_clamp(sku.get("sift_nfeatures", 600), 100, 5000))
    sku["min_template_kps"] = int(_clamp(sku.get("min_template_kps", 30), 8, 500))
    sku["min_inliers"] = int(_clamp(sku.get("min_inliers", 12), 6, 200))
    sku["min_inlier_ratio"] = _clamp(sku.get("min_inlier_ratio", 0.25), 0.05, 0.95)
    sku["ratio_thresh"] = _clamp(sku.get("ratio_thresh", 0.75), 0.4, 0.95)
    sku["ransac_reproj_thresh"] = _clamp(sku.get("ransac_reproj_thresh", 4.5), 1.0, 15.0)
    sku["min_blur"] = float(sku.get("min_blur", 20.0))
    sku["scan_max_width"] = int(_clamp(sku.get("scan_max_width", 960), 320, 2560))
    sku["min_hold_sec"] = _clamp(sku.get("min_hold_sec", 0.4), 0.0, 10.0)
    sku["cooldown_sec"] = _clamp(sku.get("cooldown_sec", 4.0), 0.5, 120.0)
    sku["track_max_age"] = _clamp(sku.get("track_max_age", 1.2), 0.2, 10.0)
    sku["max_instances"] = int(_clamp(sku.get("max_instances", 4), 1, 20))
    cm = str(sku.get("count_mode", "hold")).strip().lower()
    sku["count_mode"] = cm if cm in ("hold", "line") else "hold"
    sku.setdefault("line", [[0.0, 0.55], [1.0, 0.55]])
    iw = str(sku.get("in_when", "down")).strip().lower()
    sku["in_when"] = iw if iw in ("down", "up", "right", "left") else "down"
    sku["log_unknown"] = bool(sku.get("log_unknown", True))
    sku["prefer_code_short_circuit"] = bool(sku.get("prefer_code_short_circuit", True))

    zn = cfg.setdefault("zones", {})
    zn["person_conf"] = _clamp(zn.get("person_conf", 0.45), 0.15, 0.95)
    zn["enter_confirm_sec"] = _clamp(zn.get("enter_confirm_sec", 1.0), 0.0, 30.0)
    zn["min_dwell_sec"] = _clamp(zn.get("min_dwell_sec", 3.0), 0.5, 600.0)
    zn["exit_grace_sec"] = _clamp(zn.get("exit_grace_sec", 2.0), 0.2, 60.0)
    zn["alert_empty_sec"] = _clamp(zn.get("alert_empty_sec", 0.0), 0.0, 3600.0)
    zn["alert_cooldown_sec"] = _clamp(zn.get("alert_cooldown_sec", 30.0), 2.0, 3600.0)

    hrm = cfg.setdefault("hrm", {})
    hrm.setdefault("adapter", "mock")
    erp_cfg = hrm.setdefault("erpnext", {})
    erp_cfg.setdefault("url", "http://localhost:8000")
    erp_cfg.setdefault("api_key", "")
    erp_cfg.setdefault("api_secret", "")
    erp_cfg.setdefault("device_id", cam.get("id", "gate-1"))
    erp_cfg.setdefault("warehouse", "Stores - MSW")

    pl["mode"] = str(pl.get("mode", "attendance")).strip().lower()
    if pl["mode"] not in ("attendance", "sku", "zone"):
        pl["mode"] = "attendance"
    return cfg


def load_config(path: Path | None = None) -> dict:
    path = path or Path(os.environ.get("FACEGATE_CONFIG", ROOT / "config.yaml"))
    with open(path, encoding="utf-8") as f:
        cfg = _apply_v4_defaults(yaml.safe_load(f) or {})
    data_dir = ROOT / cfg["storage"]["data_dir"]
    cfg["_paths"] = {
        "root": ROOT,
        "models": Path(__file__).resolve().parent / "models",
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
