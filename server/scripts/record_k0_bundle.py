"""Thu thap goi du lieu thuc dia Pha K0 tren Tapo C310 (manifest.json + observations.jsonl + crops/)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from server.config import load_config  # noqa: E402
from server.vision.context_detector import ContextScene, create_context_detector, evaluate_face_context  # noqa: E402
from server.store.db import DB  # noqa: E402
from server.vision.engines import create_engine  # noqa: E402
from server.vision.liveness import create_liveness  # noqa: E402
from server.vision.quality import assess  # noqa: E402
from server.api.security import BiometricCipher  # noqa: E402
from server.vision.worker_perception import detect_faces_two_stage_3mp  # noqa: E402


def probe_stream_info(source: str) -> dict:
    info: dict = {"has_audio": False, "width": None, "height": None, "fps": None, "gop_seconds": None}
    try:
        st_out = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,codec_name,width,height,r_frame_rate", "-of", "json", source,
            ],
            timeout=15,
        )
        streams = json.loads(st_out.decode("utf-8")).get("streams", [])
        info["has_audio"] = any(s.get("codec_type") == "audio" for s in streams)
        v_streams = [s for s in streams if s.get("codec_type") == "video"]
        if v_streams:
            vs = v_streams[0]
            info["codec"] = vs.get("codec_name")
            info["width"] = vs.get("width")
            info["height"] = vs.get("height")
            r_fps = str(vs.get("r_frame_rate") or "")
            if "/" in r_fps:
                num, den = r_fps.split("/", 1)
                if float(den) > 0:
                    info["fps"] = round(float(num) / float(den), 2)
    except Exception:  # noqa: BLE001
        pass
    info["gop_seconds"] = probe_gop_seconds(source)
    return info


def capture_raw_stream_to_mp4(source: str, dst_mp4: Path, duration_sec: float = 12.0) -> Path:
    dst_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if source.lower().startswith(("rtsp://", "rtsps://")):
        cmd += ["-rtsp_transport", "tcp"]
    cmd += [
        "-i", source,
        "-t", f"{max(1.0, float(duration_sec)):.2f}",
        "-map", "0:v:0", "-c:v", "copy", "-an",
        "-movflags", "+faststart",
        str(dst_mp4),
    ]
    subprocess.run(cmd, check=True, timeout=max(30, int(duration_sec * 3) + 15))
    if not dst_mp4.is_file() or dst_mp4.stat().st_size < 256:
        raise RuntimeError(f"File MP4 tho ghi ra bi rong hoac loi: {dst_mp4}")
    return dst_mp4


def export_gallery_refs(cfg: dict, out_dir: Path) -> Path | None:
    db_path = cfg["_paths"]["db"]
    db_url = cfg["storage"].get("database_url", "")
    if not db_url and not Path(db_path).exists():
        return None
    eng = create_engine(cfg)
    db = DB(db_path, database_url=db_url, cipher=BiometricCipher.from_env_or_cfg(cfg))
    rows = db.load_gallery(eng.model_version)
    if not rows:
        return None
    payload = {
        "model_version": eng.model_version,
        "exported_at": time.time(),
        "entries": [
            {
                "employee_id": int(r["employee_id"]),
                "code": str(r["code"]),
                "pose_group": str(r.get("pose_group") or "frontal"),
                "vec": [round(float(v), 6) for v in np.asarray(r["vec"], dtype=np.float32).tolist()],
            }
            for r in rows
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / "gallery_refs.json"
    dst.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return dst


def probe_gop_seconds(source: str) -> float | None:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "frame=pict_type,pts_time", "-of", "json",
                "-read_intervals", "%+12", source,
            ],
            timeout=20,
        )
        frames = json.loads(out.decode("utf-8")).get("frames", [])
        i_pts = [float(f["pts_time"]) for f in frames if f.get("pict_type") == "I" and "pts_time" in f]
        if len(i_pts) >= 2:
            return round(float(np.median(np.diff(i_pts))), 3)
    except Exception:  # noqa: BLE001
        pass
    return None


def record_bundle(video_source: str, out_dir: Path, layout: str = "side_walk",
                  lux: float = 300.0, mode: str = "day_mode", label: str = "bonafide",
                  pai_type: str = "", subject_code: str = "", max_frames: int = 450,
                  session_id: str = "", distance_m: float | None = None, scenario: str = "",
                  consent_ref: str = "", capture_seconds: float = 0.0,
                  delete_raw: bool = False, export_gallery: bool = True,
                  cfg: dict | None = None) -> dict:
    cfg = cfg or load_config()
    session_id = session_id or time.strftime("s%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    crops_dir = out_dir / "crops" / session_id
    if (out_dir / "manifests" / f"{session_id}.json").exists() or (crops_dir.exists() and any(crops_dir.iterdir())):
        raise RuntimeError(f"Phien {session_id} da ton tai trong {out_dir} — dat --session khac")
    crops_dir.mkdir(parents=True, exist_ok=True)

    is_live = video_source.isdigit() or video_source.lower().startswith(("rtsp://", "rtsps://", "rtmp://", "http://", "https://"))
    raw_mp4_path: Path | None = None
    effective_source = video_source
    if capture_seconds > 0 and is_live and not video_source.isdigit():
        raw_mp4_path = out_dir / "raw" / f"{session_id}.mp4"
        capture_raw_stream_to_mp4(video_source, raw_mp4_path, duration_sec=capture_seconds)
        effective_source = str(raw_mp4_path)
        is_live = False
    elif is_live:
        print("[CANH BAO] Nguon truc tiep: xu ly tung frame tren CPU cham hon 15 fps -> mat frame, ket qua khong lap lai duoc (H14). "
              "Khuyen nghi truyen --capture-seconds 12 de ghi MP4 tho (-c:v copy -an) truoc khi trich xuat.", file=sys.stderr)

    if export_gallery and not (out_dir / "gallery_refs.json").exists():
        try:
            export_gallery_refs(cfg, out_dir)
        except Exception:  # noqa: BLE001
            pass

    eng = create_engine(cfg)
    liv = create_liveness(cfg)
    pose_est = None
    try:
        from server.vision.pose3d import create_pose_estimator
        pose_est = create_pose_estimator(cfg)
    except Exception:  # noqa: BLE001
        pass
    ctx_det = None
    try:
        ctx_det = create_context_detector(cfg)
    except Exception:  # noqa: BLE001
        pass

    cap = cv2.VideoCapture(int(effective_source) if effective_source.isdigit() else effective_source)
    if not cap.isOpened():
        raise RuntimeError(f"Khong mo duoc nguon video: {effective_source}")

    src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 15.0)
    if src_fps <= 1.0 or src_fps > 120.0:
        src_fps = 15.0
    pcfg = cfg.get("pipeline", {})
    use_two_stage = bool(pcfg.get("two_stage_roi", True))
    max_faces = int(pcfg.get("max_faces", 10))
    fallback_w = int(pcfg.get("fallback_det_width", 1280))
    always_fb = bool(pcfg.get("always_fallback_unmatched", True))
    frontal_max = float(cfg.get("recognition", {}).get("frontal_max_yaw", 40.0))
    viewport_grace = float(cfg.get("tracking", {}).get("viewport_bottom_grace", 0.80))

    obs_path = out_dir / "observations.jsonl"
    decode_ms_list: list[float] = []
    infer_ms_list: list[float] = []
    blur_list: list[float] = []
    face_widths: list[float] = []
    n_frames = 0
    t0_mono = time.monotonic()

    try:
        with open(obs_path, "a", encoding="utf-8") as obs_f:
            while n_frames < max_frames:
                t_dec = time.perf_counter()
                ok, frame = cap.read()
                dec_ms = (time.perf_counter() - t_dec) * 1000.0
                if not ok or frame is None:
                    break
                t_inf = time.perf_counter()
                decode_ms_list.append(dec_ms)
                ts_ns = time.time_ns()
                pos_ms = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
                if not is_live:
                    video_ts_ms = pos_ms if pos_ms > 0.0 or n_frames == 0 else (n_frames * 1000.0 / src_fps)
                else:
                    video_ts_ms = (time.monotonic() - t0_mono) * 1000.0
                h, w = frame.shape[:2]

                scene: ContextScene | None = None
                if use_two_stage:
                    faces, scene = detect_faces_two_stage_3mp(
                        frame, eng, ctx_det,
                        fallback_det_width=fallback_w,
                        max_faces=max_faces,
                        always_fallback_unmatched=always_fb,
                    )
                else:
                    faces = eng.detect(frame)[:max_faces]
                    scene = ctx_det.detect(frame) if ctx_det is not None and faces else None

                persons = [b.tolist() for b in scene.persons] if scene is not None else []
                mediums = [
                    {"bbox": [round(float(v), 2) for v in mb.tolist()], "label": lbl, "score": round(float(sc), 4)}
                    for mb, lbl, sc in (scene.mediums if scene is not None else [])
                ]

                poses, quals, aligned_list = [], [], []
                for f in faces:
                    pose = pose_est.estimate(frame, f) if pose_est is not None else None
                    q = assess(frame, f, cfg["quality"], pose=pose)
                    poses.append(pose)
                    quals.append(q)
                    pose_grp = pose.pose_group if pose is not None else q.pose_group
                    aligned = (
                        eng.align(frame, f, pose=pose, pose_group=pose_grp)
                        if "pose" in eng.align.__code__.co_varnames
                        else eng.align(frame, f)
                    )
                    aligned_list.append(aligned)

                embs = (
                    eng.embed_batch(aligned_list)
                    if aligned_list and hasattr(eng, "embed_batch")
                    else [eng.embed_aligned(a) for a in aligned_list]
                )

                face_records = []
                for fi, (f, pose, q, emb) in enumerate(zip(faces, poses, quals, embs)):
                    blur_list.append(q.blur)
                    face_widths.append(f.w)
                    x1, y1, x2, y2 = (
                        max(0, int(f.bbox[0] - 0.25 * f.w)),
                        max(0, int(f.bbox[1] - 0.25 * f.h)),
                        min(w, int(f.bbox[2] + 0.25 * f.w)),
                        min(h, int(f.bbox[3] + 0.25 * f.h)),
                    )
                    crop = frame[y1:y2, x1:x2]
                    crop_rel = f"crops/{session_id}/f{n_frames:05d}_{fi}.jpg"
                    if crop.size > 0:
                        cv2.imwrite(str(out_dir / crop_rel), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])

                    # Ghi yaw/pitch THO: replay_eval doc truong nay de hieu chuan tau/margin va danh gia SC-002/SC-006,
                    # nen phai cung tin hieu voi production (production dung gate_yaw cho moi cong quyet dinh).
                    yaw_val = float(pose.gate_yaw() if pose is not None else q.yaw)
                    pitch_val = float(pose.gate_pitch() if pose is not None else q.pitch)
                    roll_val = float(pose.roll if pose is not None else q.roll)
                    pose_grp = str(pose.pose_group if pose is not None else q.pose_group)
                    ctx_eval = evaluate_face_context(
                        f.bbox, scene, frame_height=h, viewport_bottom_grace=viewport_grace,
                    )
                    fas_prob = liv.real_prob(frame, f.bbox) if liv is not None else None
                    face_records.append({
                        "bbox": [round(float(v), 2) for v in f.bbox],
                        "det_score": round(float(f.score), 4),
                        "crop_rel": crop_rel,
                        "quality": q.as_dict(),
                        "yaw": round(yaw_val, 2),
                        "pitch": round(pitch_val, 2),
                        "roll": round(roll_val, 2),
                        "pose_group": pose_grp,
                        "context": ctx_eval,
                        "fas_eligible": bool(q.ok and abs(yaw_val) <= frontal_max),
                        "embedding": [round(float(v), 6) for v in emb.tolist()],
                        "fas_prob": round(float(fas_prob), 4) if fas_prob is not None else None,
                    })

                inf_ms = (time.perf_counter() - t_inf) * 1000.0
                infer_ms_list.append(inf_ms)
                rec = {
                    "session_id": session_id,
                    "frame_idx": n_frames,
                    "ts_ns": ts_ns,
                    "video_ts_ms": round(video_ts_ms, 2),
                    "frame_size": [w, h],
                    "decode_ms": round(dec_ms, 3),
                    "infer_ms": round(inf_ms, 3),
                    "layout": layout,
                    "mode": mode,
                    "lux": lux,
                    "distance_m": distance_m,
                    "scenario": scenario,
                    "gt_label": label,
                    "pai_type": pai_type,
                    "subject_code": subject_code,
                    "person_boxes": persons,
                    "medium_boxes": mediums,
                    "faces": face_records,
                }
                obs_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_frames += 1
    finally:
        cap.release()

    stream_meta = probe_stream_info(effective_source) if not effective_source.isdigit() else {"has_audio": False, "gop_seconds": None}
    if raw_mp4_path is not None and delete_raw:
        raw_mp4_path.unlink(missing_ok=True)

    manifest = {
        "session_id": session_id,
        "created_at": time.time(),
        "source": video_source,
        "raw_mp4": (
            raw_mp4_path.relative_to(out_dir).as_posix()
            if raw_mp4_path is not None and raw_mp4_path.exists()
            else None
        ),
        "model_version": eng.model_version,
        "two_stage_roi": use_two_stage,
        "layout": layout,
        "lux": lux,
        "distance_m": distance_m,
        "scenario": scenario,
        "mode": mode,
        "gt_label": label,
        "pai_type": pai_type,
        "subject_code": subject_code,
        "gop_seconds": stream_meta.get("gop_seconds"),
        "source_has_audio": bool(stream_meta.get("has_audio", False)),
        "frames_recorded": n_frames,
        "faces_detected": len(face_widths),
        "decode_ms_mean": round(float(np.mean(decode_ms_list)), 2) if decode_ms_list else 0.0,
        "infer_ms_mean": round(float(np.mean(infer_ms_list)), 2) if infer_ms_list else 0.0,
        "blur_median": round(float(np.median(blur_list)), 2) if blur_list else 0.0,
        "face_width_median_px": round(float(np.median(face_widths)), 1) if face_widths else 0.0,
        "consent_ref": consent_ref or None,
        "audio_stripped": not bool(stream_meta.get("has_audio", False)),
    }
    mdir = out_dir / "manifests"
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / f"{session_id}.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Ghi goi danh gia thuc dia K0 dong bo runtime v4 (JSONL + crops + raw MP4)")
    ap.add_argument("source", help="Duong dan video hoac RTSP URL")
    ap.add_argument("--out", default="data/k0_bundle", help="Thu muc luu goi K0 (ngoai Git)")
    ap.add_argument("--layout", choices=["frontal_corridor", "side_walk"], default="side_walk")
    ap.add_argument("--lux", type=float, default=300.0)
    ap.add_argument("--mode", choices=["day_mode", "ir_night"], default="day_mode")
    ap.add_argument("--label", choices=["bonafide", "spoof", "stranger"], default="bonafide")
    ap.add_argument("--pai", default="", help="print | phone_photo | phone_video | large_screen")
    ap.add_argument("--subject", default="")
    ap.add_argument("--max-frames", type=int, default=450)
    ap.add_argument("--capture-seconds", type=float, default=0.0)
    ap.add_argument("--delete-raw", action="store_true")
    ap.add_argument("--session", default="", help="Ma phien duy nhat (mac dinh theo thoi gian)")
    ap.add_argument("--distance", type=float, default=None, help="Khoang cach mat - ong kinh (m)")
    ap.add_argument("--scenario", default="", help="vd: walk_frontal | walk_side | crossing | backlight | stop_and_look")
    ap.add_argument("--consent-ref", default="", help="Ma don dong y FG-CONSENT-K0 cua nguoi trong khung hinh (bat buoc, H11)")
    ap.add_argument("--allow-no-consent", action="store_true", help="Chi dung cho video thu nghiem khong co nguoi that")
    args = ap.parse_args()
    if not args.consent_ref and not args.allow_no_consent:
        ap.error("Thieu --consent-ref: khong quay/xu ly mat nguoi that khi chua co don dong y (H11, Nguyen tac IV)")
    m = record_bundle(
        args.source, Path(args.out), args.layout, args.lux, args.mode, args.label, args.pai,
        args.subject, args.max_frames, session_id=args.session, distance_m=args.distance,
        scenario=args.scenario, consent_ref=args.consent_ref,
        capture_seconds=args.capture_seconds, delete_raw=args.delete_raw,
    )
    print(json.dumps(m, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
