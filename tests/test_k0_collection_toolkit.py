from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.config import load_config
from scripts.k0_field_runner import expand_session_plan, finalize_k0_evaluation, plan_progress
from scripts.record_k0_bundle import record_bundle
from scripts.replay_eval import evaluate_observations, render_k0_5_markdown_summary


def _unit_vec(dim: int, idx: int, noise: float = 0.0) -> list[float]:
    v = np.zeros(dim, dtype=np.float32)
    v[idx % dim] = 1.0
    if noise != 0.0:
        v[(idx + 1) % dim] = noise
    v /= np.linalg.norm(v) + 1e-9
    return [round(float(x), 6) for x in v.tolist()]


def test_k0_session_plan_expansion_and_resume(tmp_path: Path):
    plan_file = Path(__file__).resolve().parent.parent / "deploy" / "k0_session_plan.yaml"
    plan = yaml.safe_load(plan_file.read_text(encoding="utf-8"))
    tasks = expand_session_plan(plan)
    assert len(tasks) >= 50
    session_ids = [t["session_id"] for t in tasks]
    assert len(session_ids) == len(set(session_ids))
    pai_types = {t["pai_type"] for t in tasks if t["label"] == "spoof"}
    assert pai_types == {"print", "phone_photo", "phone_video", "large_screen"}

    # Gia lap 2 phien da hoan thanh -> plan_progress phai bo qua dung 2 phien do khi resume
    mdir = tmp_path / "manifests"
    mdir.mkdir(parents=True, exist_ok=True)
    for sid in session_ids[:2]:
        (mdir / f"{sid}.json").write_text("{}", encoding="utf-8")
    prog = plan_progress(tasks, tmp_path)
    assert prog["completed"] == 2
    assert prog["remaining"] == len(tasks) - 2


def test_track_level_stateful_replay_pose_group_and_calibrate(tmp_path: Path):
    bundle = tmp_path / "k0_bundle"
    bundle.mkdir(parents=True)
    # Gallery co 2 nhan vien NV001 (frontal + left_profile) va NV002 (frontal)
    gallery_refs = {
        "model_version": "test_v4",
        "entries": [
            {"employee_id": 1, "code": "NV001", "pose_group": "frontal", "vec": _unit_vec(16, 0)},
            {"employee_id": 1, "code": "NV001", "pose_group": "left_profile", "vec": _unit_vec(16, 1)},
            {"employee_id": 2, "code": "NV002", "pose_group": "frontal", "vec": _unit_vec(16, 2)},
        ],
    }
    (bundle / "gallery_refs.json").write_text(json.dumps(gallery_refs), encoding="utf-8")

    rows = []
    # 1. Phien Bonafide NV001 di thang (4 frame cach nhau 300ms vuot Novelty Gate + du 4 frame MiniFASNet >= 0.70) -> Checkin DUNG
    for i in range(4):
        rows.append({
            "session_id": "bf_nv001",
            "frame_idx": i,
            "video_ts_ms": i * 300.0,
            "frame_size": [2304, 1296],
            "decode_ms": 7.5,
            "infer_ms": 48.0,
            "layout": "frontal_corridor",
            "gt_label": "bonafide",
            "subject_code": "NV001",
            "person_boxes": [[800, 200, 1300, 1200]],
            "medium_boxes": [],
            "faces": [{
                "bbox": [980, 260, 1100, 400],
                "det_score": 0.95,
                "yaw": 5.0,
                "pose_group": "frontal",
                "quality": {"ok": True, "blur": 92.0, "score": 0.90, "yaw": 5.0},
                "embedding": _unit_vec(16, 0, noise=0.05 * (i + 1)),
                "fas_prob": 0.88,
            }],
        })

    # 2. Phien Spoof phone_video: co 1 frame le vot fas_prob=0.80 nhung trung binh cua so 4 frame chi 0.31 (<0.35)
    #    Frame-level cu se bao 1/4 frame lot (25%), nhung Track-level Stateful Replay phai CHAN CHAC CHAN (0% lot)!
    spoof_fas = [0.80, 0.15, 0.15, 0.14]
    for i, fp in enumerate(spoof_fas):
        rows.append({
            "session_id": "sp_phone_01",
            "frame_idx": i,
            "video_ts_ms": i * 300.0,
            "frame_size": [2304, 1296],
            "decode_ms": 8.0,
            "infer_ms": 50.0,
            "layout": "frontal_corridor",
            "gt_label": "spoof",
            "pai_type": "phone_video",
            "subject_code": "NV001",
            "person_boxes": [[800, 200, 1300, 1200]],
            "medium_boxes": [],
            "faces": [{
                "bbox": [980, 260, 1100, 400],
                "det_score": 0.94,
                "yaw": 4.0,
                "pose_group": "frontal",
                "quality": {"ok": True, "blur": 65.0, "score": 0.85, "yaw": 4.0},
                "embedding": _unit_vec(16, 0, noise=0.02),
                "fas_prob": fp,
            }],
        })

    # 3. Phien Stranger (nguoi la khong co trong Gallery, fas_prob=0.92 la nguoi that) -> KHONG duoc phat checkin!
    for i in range(4):
        rows.append({
            "session_id": "stranger_01",
            "frame_idx": i,
            "video_ts_ms": i * 300.0,
            "frame_size": [2304, 1296],
            "decode_ms": 7.8,
            "infer_ms": 46.0,
            "layout": "side_walk",
            "gt_label": "stranger",
            "subject_code": "",
            "person_boxes": [[700, 220, 1200, 1200]],
            "medium_boxes": [],
            "faces": [{
                "bbox": [900, 280, 1010, 410],
                "det_score": 0.91,
                "yaw": -55.0,
                "pose_group": "left_profile",
                "quality": {"ok": True, "blur": 58.0, "score": 0.80, "yaw": -55.0},
                "embedding": _unit_vec(16, 7, noise=0.03 * i),
                "fas_prob": 0.92,
            }],
        })

    obs_path = bundle / "observations.jsonl"
    obs_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    json_out, md_out = finalize_k0_evaluation(bundle, threshold=0.40, fas_threshold=0.70, calibrate=True)
    rep = json.loads(json_out.read_text(encoding="utf-8"))

    # Frame-level van giu nguyen so lieu de doi chieu (1/4 frame >= 0.70 = 0.25),
    # trong khi Track-level phan anh dung he thong that chan thanh cong 100% (APCER = 0.0)!
    assert rep["iso_30107_3"]["apcer_by_pai"]["phone_video"]["apcer"] == 0.25
    trk = rep["track_level"]
    assert trk["iso_30107_3_track_level"]["apcer_by_pai"]["phone_video"]["apcer"] == 0.0
    assert trk["event_far"]["stranger_false_accepts"] == 0
    assert trk["event_far"]["false_checkins"] == 0
    assert trk["event_frr"]["missed_checkins"] == 0
    assert "calibrated_llr" in rep
    assert rep["calibrated_llr"]["llr_weights"]["a_cos"] > 0.5
    md_text = md_out.read_text(encoding="utf-8")
    assert "Track-level" in md_text
    assert "phone_video: 0.0%" in md_text


def test_record_bundle_end_to_end_on_mp4_file(tmp_path: Path):
    cfg = load_config()
    cfg["storage"]["data_dir"] = str(tmp_path / "data")
    cfg["_paths"]["db"] = tmp_path / "data" / "facegate.db"

    # Tao file MP4 6 khung hinh 1280x720 thuc te (chen anh khuon mat enroll that neu san co tren may)
    mp4_path = tmp_path / "sample_gate.mp4"
    w, h = 1280, 720
    writer = cv2.VideoWriter(str(mp4_path), cv2.VideoWriter_fourcc(*"mp4v"), 15.0, (w, h))
    sample_face_jpg = Path(__file__).resolve().parent.parent / "data" / "enroll" / "NV001" / "00_thang-giua.jpg"
    face_patch = cv2.imread(str(sample_face_jpg)) if sample_face_jpg.is_file() else None
    for i in range(6):
        canvas = np.full((h, w, 3), 180, dtype=np.uint8)
        if face_patch is not None and face_patch.size > 0:
            ph, pw = min(360, h - 40), min(360, w - 40)
            resized = cv2.resize(face_patch, (pw, ph))
            x0 = 400 + i * 8
            y0 = 120
            canvas[y0:y0 + ph, x0:x0 + pw] = resized
        writer.write(canvas)
    writer.release()

    out_bundle = tmp_path / "out_k0"
    manifest = record_bundle(
        video_source=str(mp4_path),
        out_dir=out_bundle,
        layout="frontal_corridor",
        lux=300.0,
        mode="day_mode",
        label="bonafide",
        subject_code="NV001",
        max_frames=6,
        session_id="e2e_mp4_01",
        distance_m=2.0,
        scenario="walk_frontal",
        consent_ref="FG-CONSENT-K0-TEST",
        export_gallery=False,
        cfg=cfg,
    )
    assert manifest["session_id"] == "e2e_mp4_01"
    assert manifest["frames_recorded"] == 6
    assert manifest["two_stage_roi"] is True
    assert manifest["audio_stripped"] is True
    assert (out_bundle / "manifests" / "e2e_mp4_01.json").is_file()
    obs_lines = (out_bundle / "observations.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(obs_lines) == 6
    first_rec = json.loads(obs_lines[0])
    assert "infer_ms" in first_rec
    assert "medium_boxes" in first_rec
    if first_rec["faces"]:
        f0 = first_rec["faces"][0]
        assert "pose_group" in f0
        assert "context" in f0
    summary_md = render_k0_5_markdown_summary(evaluate_observations(out_bundle / "observations.jsonl"), out_bundle)
    assert "frontal_corridor" in summary_md
