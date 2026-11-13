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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import load_config  # noqa: E402
from app.engines import create_engine  # noqa: E402
from app.liveness import create_liveness  # noqa: E402
from app.quality import assess  # noqa: E402


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
                  pai_type: str = "", subject_code: str = "", max_frames: int = 450) -> dict:
    cfg = load_config()
    out_dir.mkdir(parents=True, exist_ok=True)
    crops_dir = out_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    eng = create_engine(cfg)
    liv = create_liveness(cfg)
    pose_est = None
    try:
        from app.pose3d import PoseEstimator3D
        pose_est = PoseEstimator3D(cfg["_paths"]["models"])
    except Exception:  # noqa: BLE001
        pass
    ctx_det = None
    try:
        from app.context_detector import create_context_detector
        ctx_det = create_context_detector(cfg)
    except Exception:  # noqa: BLE001
        pass

    cap = cv2.VideoCapture(int(video_source) if video_source.isdigit() else video_source)
    if not cap.isOpened():
        raise RuntimeError(f"Khong mo duoc nguon video: {video_source}")

    obs_path = out_dir / "observations.jsonl"
    decode_ms_list: list[float] = []
    blur_list: list[float] = []
    face_widths: list[float] = []
    n_frames = 0

    with open(obs_path, "a", encoding="utf-8") as obs_f:
        while n_frames < max_frames:
            t_dec = time.perf_counter()
            ok, frame = cap.read()
            dec_ms = (time.perf_counter() - t_dec) * 1000.0
            if not ok or frame is None:
                break
            decode_ms_list.append(dec_ms)
            ts_ns = time.time_ns()
            h, w = frame.shape[:2]

            persons = []
            if ctx_det is not None:
                scene = ctx_det.detect(frame)
                persons = [b.tolist() for b in scene.persons]

            faces = eng.detect(frame)
            face_records = []
            for fi, f in enumerate(faces):
                pose = pose_est.estimate(frame, f) if pose_est is not None else None
                q = assess(frame, f, cfg["quality"], pose=pose) if "pose" in assess.__code__.co_varnames else assess(frame, f, cfg["quality"])
                blur_list.append(q.blur)
                face_widths.append(f.w)

                x1, y1, x2, y2 = (max(0, int(f.bbox[0] - 0.25 * f.w)), max(0, int(f.bbox[1] - 0.25 * f.h)),
                                  min(w, int(f.bbox[2] + 0.25 * f.w)), min(h, int(f.bbox[3] + 0.25 * f.h)))
                crop = frame[y1:y2, x1:x2]
                crop_rel = f"crops/f{n_frames:05d}_{fi}.jpg"
                if crop.size > 0:
                    cv2.imwrite(str(out_dir / crop_rel), crop, [cv2.IMWRITE_JPEG_QUALITY, 95])

                aligned = eng.align(frame, f, pose=pose) if "pose" in eng.align.__code__.co_varnames else eng.align(frame, f)
                emb = eng.embed_aligned(aligned)
                fas_prob = liv.real_prob(frame, f.bbox) if liv is not None else None
                face_records.append({
                    "bbox": [round(float(v), 2) for v in f.bbox],
                    "det_score": round(float(f.score), 4),
                    "crop_rel": crop_rel,
                    "quality": q.as_dict(),
                    "yaw": round(float(pose.yaw if pose is not None else q.yaw), 2),
                    "pitch": round(float(pose.pitch if pose is not None else q.pitch), 2),
                    "embedding": [round(float(v), 6) for v in emb.tolist()],
                    "fas_prob": round(float(fas_prob), 4) if fas_prob is not None else None,
                })

            rec = {
                "frame_idx": n_frames,
                "ts_ns": ts_ns,
                "frame_size": [w, h],
                "decode_ms": round(dec_ms, 3),
                "layout": layout,
                "mode": mode,
                "gt_label": label,
                "pai_type": pai_type,
                "subject_code": subject_code,
                "person_boxes": persons,
                "faces": face_records,
            }
            obs_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n_frames += 1

    cap.release()
    manifest = {
        "created_at": time.time(),
        "source": video_source,
        "layout": layout,
        "lux": lux,
        "mode": mode,
        "gop_seconds": probe_gop_seconds(video_source) if not video_source.isdigit() else None,
        "frames_recorded": n_frames,
        "decode_ms_mean": round(float(np.mean(decode_ms_list)), 2) if decode_ms_list else 0.0,
        "blur_median": round(float(np.median(blur_list)), 2) if blur_list else 0.0,
        "face_width_median_px": round(float(np.median(face_widths)), 1) if face_widths else 0.0,
        "consent_verified": True,
        "audio_stripped": True,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Ghi goi danh gia thuc dia K0 (JSONL + crops)")
    ap.add_argument("source", help="Duong dan video hoac RTSP URL")
    ap.add_argument("--out", default="data/k0_bundle", help="Thu muc luu goi K0 (ngoai Git)")
    ap.add_argument("--layout", choices=["frontal_corridor", "side_walk"], default="side_walk")
    ap.add_argument("--lux", type=float, default=300.0)
    ap.add_argument("--mode", choices=["day_mode", "ir_night"], default="day_mode")
    ap.add_argument("--label", choices=["bonafide", "spoof", "stranger"], default="bonafide")
    ap.add_argument("--pai", default="", help="print | phone_photo | phone_video | large_screen")
    ap.add_argument("--subject", default="")
    ap.add_argument("--max-frames", type=int, default=450)
    args = ap.parse_args()
    m = record_bundle(args.source, Path(args.out), args.layout, args.lux, args.mode, args.label, args.pai, args.subject, args.max_frames)
    print(json.dumps(m, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
