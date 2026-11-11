"""So sanh toc do cac backend & do phu goc yaw tren mot video (footage that tu camera cong).

    python scripts/benchmark.py data/footage.mp4
    python scripts/benchmark.py data/footage.mp4 --profile-eval
"""
import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import load_config  # noqa: E402
from app.context_detector import create_context_detector  # noqa: E402
from app.engines import create_engine  # noqa: E402
from app.liveness import create_liveness  # noqa: E402
from app.pose3d import PoseEstimator3D  # noqa: E402
from app.quality import assess  # noqa: E402


def _yaw_bucket_name(abs_yaw: float) -> str:
    if abs_yaw <= 40.0:
        return "0-40 (frontal)"
    if abs_yaw <= 65.0:
        return "40-65 (half-profile)"
    return "65-90 (full-profile)"


def run(backend: str, frames: list, cfg: dict, profile_eval: bool = False) -> None:
    cfg = {**cfg, "pipeline": {**cfg["pipeline"], "backend": backend}}
    try:
        eng = create_engine(cfg)
    except FileNotFoundError as e:
        print(f"[{backend}] bo qua: {e}")
        return

    pose_est = PoseEstimator3D(cfg["_paths"]["models"]) if profile_eval else None
    ctx_det = create_context_detector(cfg) if profile_eval else None
    liv = create_liveness(cfg) if profile_eval else None

    det_t, ctx_t, pose_t, emb_t, liv_t = [], [], [], [], []
    n_faces, n_good = 0, 0
    yaw_hist = {"0-40 (frontal)": [0, 0], "40-65 (half-profile)": [0, 0], "65-90 (full-profile)": [0, 0]}

    for fr in frames:
        h, w = fr.shape[:2]
        s = min(1.0, 960 / w)
        small = cv2.resize(fr, (int(w * s), int(h * s))) if s < 1 else fr
        t = time.perf_counter()
        faces = eng.detect(small)
        det_t.append((time.perf_counter() - t) * 1000)

        if ctx_det is not None and faces:
            t = time.perf_counter()
            ctx_det.detect(fr)
            ctx_t.append((time.perf_counter() - t) * 1000)

        for f in faces:
            f.bbox, f.kps = f.bbox / s, f.kps / s
            n_faces += 1
            pose = None
            if pose_est is not None:
                t = time.perf_counter()
                pose = pose_est.estimate(fr, f)
                pose_t.append((time.perf_counter() - t) * 1000)
            q = assess(fr, f, cfg["quality"], pose=pose)
            yb = _yaw_bucket_name(abs(q.yaw))
            yaw_hist[yb][0] += 1
            if q.ok:
                n_good += 1
                yaw_hist[yb][1] += 1
                t = time.perf_counter()
                eng.embed_aligned(eng.align(fr, f, pose=pose, pose_group=q.pose_group))
                emb_t.append((time.perf_counter() - t) * 1000)
                if liv is not None and abs(q.yaw) <= 40.0:
                    t = time.perf_counter()
                    liv.real_prob(fr, f.bbox)
                    liv_t.append((time.perf_counter() - t) * 1000)

    print(f"[{backend}] {eng.model_version}")
    print(f"   detect face : TB {np.mean(det_t):.1f} ms | p90 {np.percentile(det_t, 90):.1f} ms ({len(frames)} frame)")
    if ctx_t:
        print(f"   yolox ctx   : TB {np.mean(ctx_t):.1f} ms | p90 {np.percentile(ctx_t, 90):.1f} ms")
    if pose_t:
        print(f"   3ddfa pose  : TB {np.mean(pose_t):.2f} ms / mat")
    if emb_t:
        print(f"   embed       : TB {np.mean(emb_t):.1f} ms / mat")
    if liv_t:
        print(f"   minifasnet  : TB {np.mean(liv_t):.1f} ms / mat")
    print(f"   mat phat hien: {n_faces} | dat chat luong: {n_good}")
    if profile_eval:
        for k, (tot, ok_cnt) in yaw_hist.items():
            print(f"     - goc {k}: dat {ok_cnt}/{tot}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark toc do va do phu goc nhan dien FaceGate")
    ap.add_argument("video", help="Duong dan file video can do")
    ap.add_argument("--profile-eval", action="store_true", help="Do chi tiet tung tang 3DDFA_V2 + YOLOX + phan bo yaw 0..90")
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.video)
    frames = []
    while len(frames) < 300:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    if not frames:
        print(f"Khong doc duoc frame nao tu {args.video}", file=sys.stderr)
        sys.exit(1)
    print(f"Doc {len(frames)} frame tu {args.video}\n")
    cfg = load_config()
    backends = ("edgeface", "opencv", "onnx", "adaface") if args.profile_eval else ("edgeface", "opencv", "onnx")
    for b in backends:
        run(b, frames, cfg, profile_eval=args.profile_eval)


if __name__ == "__main__":
    main()
