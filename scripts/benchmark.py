"""Do kiem toc do cac backend, chi phi decode 3MP@15fps, Laplacian blur & batch 10 khuon mat dong thoi (K0 & K4 - T029).

    python scripts/benchmark.py data/footage.mp4
    python scripts/benchmark.py --ten-faces-bench
"""
import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import load_config  # noqa: E402
from app.engines import create_engine  # noqa: E402
from app.pose3d import PoseEstimator3D  # noqa: E402
from app.quality import assess  # noqa: E402
from app.worker_perception import detect_faces_two_stage_3mp  # noqa: E402


def run_ten_faces_budget_bench(cfg: dict, n_iters: int = 12) -> dict:
    """Do ngan sach K4 (< 200 ms/chu ky) cho 10 khuon mat dong thoi (align + batch embed + pose3d)."""
    eng = create_engine(cfg)
    rng = np.random.default_rng(42)
    aligned_10 = [rng.integers(40, 220, (112, 112, 3), dtype=np.uint8) for _ in range(10)]
    # Warm-up
    eng.embed_batch(aligned_10[:2])
    times = []
    for _ in range(n_iters):
        t0 = time.perf_counter()
        embs = eng.embed_batch(aligned_10)
        times.append((time.perf_counter() - t0) * 1000.0)
        assert len(embs) == 10
    mean_ms = float(np.mean(times))
    p90_ms = float(np.percentile(times, 90))
    print(f"[K4 10-Faces Batch] backend={eng.name} ({eng.model_version}) ep={cfg['pipeline'].get('execution_provider')}")
    print(f"   10-face batch embed: TB {mean_ms:.1f} ms | p90 {p90_ms:.1f} ms (muc tieu chu ky < 200 ms)")
    return {"mean_ms": round(mean_ms, 2), "p90_ms": round(p90_ms, 2)}


def run(backend: str, frames: list, dec_ms_list: list[float], cfg: dict) -> None:
    cfg = {**cfg, "pipeline": {**cfg["pipeline"], "backend": backend}}
    try:
        eng = create_engine(cfg)
    except FileNotFoundError as e:
        print(f"[{backend}] bo qua: {e}")
        return
    pose_est = PoseEstimator3D(cfg["_paths"]["models"])
    det_t, emb_t, blurs, n_faces, n_good = [], [], [], 0, 0
    for fr in frames:
        t = time.perf_counter()
        faces, _ = detect_faces_two_stage_3mp(fr, eng, None, fallback_det_width=1280, max_faces=10)
        det_t.append((time.perf_counter() - t) * 1000)
        aligned_batch = []
        for f in faces:
            n_faces += 1
            pose = pose_est.estimate(fr, f)
            q = assess(fr, f, cfg["quality"], pose=pose)
            blurs.append(q.blur)
            if q.ok:
                n_good += 1
                aligned_batch.append(eng.align(fr, f, pose=pose, pose_group=q.pose_group))
        if aligned_batch:
            t = time.perf_counter()
            eng.embed_batch(aligned_batch)
            emb_t.append((time.perf_counter() - t) * 1000 / len(aligned_batch))
    print(f"[{backend}] {eng.model_version}")
    if dec_ms_list:
        print(f"   decode 3MP : TB {np.mean(dec_ms_list):.1f} ms | p90 {np.percentile(dec_ms_list, 90):.1f} ms")
    print(f"   detect ROI : TB {np.mean(det_t):.1f} ms | p90 {np.percentile(det_t, 90):.1f} ms ({len(frames)} frame)")
    if emb_t:
        print(f"   embed batch: TB {np.mean(emb_t):.1f} ms / mat")
    if blurs:
        print(f"   blur med   : {np.median(blurs):.1f} (min_blur={cfg['quality']['min_blur']})")
    print(f"   mat phat hien: {n_faces} | dat chat luong: {n_good}\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark FaceGate v4 (Tapo C310 3MP + Batch 10 mat)")
    ap.add_argument("video", nargs="?", default="", help="Duong dan file video can do")
    ap.add_argument("--ten-faces-bench", action="store_true", help="Do toc do batch 10 khuon mat dong thoi (K4)")
    ap.add_argument("--ep", choices=["cpu", "openvino"], default="cpu")
    args = ap.parse_args()
    cfg = load_config()
    cfg["pipeline"]["execution_provider"] = args.ep
    if args.ten_faces_bench:
        run_ten_faces_budget_bench(cfg)
        if not args.video:
            return
    if not args.video:
        ap.print_help()
        sys.exit(1)
    cap = cv2.VideoCapture(args.video)
    frames, dec_ms = [], []
    while len(frames) < 300:
        t0 = time.perf_counter()
        ok, f = cap.read()
        dec_ms.append((time.perf_counter() - t0) * 1000.0)
        if not ok:
            break
        frames.append(f)
    cap.release()
    print(f"Doc {len(frames)} frame tu {args.video}\n")
    for b in ("edgeface", "opencv"):
        run(b, frames, dec_ms, cfg)


if __name__ == "__main__":
    main()
