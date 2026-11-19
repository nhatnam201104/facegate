"""Do kiem toc do cac backend, chi phi decode 3MP@15fps, Laplacian blur & batch 10 khuon mat dong thoi (K0 & K4 - T029).

    python scripts/benchmark.py data/footage.mp4
    python scripts/benchmark.py --ten-faces-bench
    python server/scripts/benchmark.py video.mp4 --full-pipeline --backend opencv --min-faces 1 --out r.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from server.config import load_config  # noqa: E402
from server.vision.engines import create_engine  # noqa: E402
from server.vision.pose3d import create_pose_estimator  # noqa: E402
from server.vision.quality import assess  # noqa: E402
from server.vision.runtime_threads import apply_cv_threads  # noqa: E402
from server.vision.worker_perception import detect_faces_two_stage_3mp  # noqa: E402


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
    pose_est = create_pose_estimator(cfg)
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


STAGES = ("decode", "detect", "context", "pose", "quality", "align", "embed", "liveness")


class _TimedContext:
    """Boc ContextDetector de tach thoi gian YOLOX ra khoi buoc detect (detect_faces_two_stage_3mp goi ben trong)."""

    def __init__(self, inner):
        self.inner = inner
        self.elapsed_ms = 0.0

    def detect(self, img):
        t = time.perf_counter()
        try:
            return self.inner.detect(img)
        finally:
            self.elapsed_ms += (time.perf_counter() - t) * 1000.0

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _paste_faces(frame: np.ndarray, crop: np.ndarray, n: int) -> np.ndarray:
    """Ghep lap crop mat that len frame cho du n mat (chi dung khi video khong co frame dat --min-faces)."""
    out = frame.copy()
    h, w = out.shape[:2]
    cols = int(np.ceil(np.sqrt(n)))
    rows = int(np.ceil(n / cols))
    cw, ch = w // cols, h // rows
    side = max(32, int(min(cw, ch) * 0.8))
    tile = cv2.resize(crop, (side, side))
    k = 0
    for r in range(rows):
        for c in range(cols):
            if k >= n:
                break
            x0, y0 = c * cw + (cw - side) // 2, r * ch + (ch - side) // 2
            out[y0:y0 + side, x0:x0 + side] = tile
            k += 1
    return out


def run_full_pipeline(cfg: dict, video: str, backend: str | None = None, min_faces: int = 1,
                      max_frames: int = 300, simulate_faces: int = 0,
                      engine=None, pose_est=None, ctx_det=None, liveness=None) -> dict:
    """Do tron chuoi theo dung thu tu worker_perception.process_frame (Hien phap III).

    decode -> detect (2 tang ROI, khong tinh YOLOX) -> context (YOLOX) -> pose -> quality -> align -> embed -> liveness
    Cac tham so engine/pose_est/ctx_det/liveness cho phep tiem stub khi test.
    """
    from server.vision.context_detector import create_context_detector
    from server.vision.liveness import create_liveness

    if backend:
        cfg = {**cfg, "pipeline": {**cfg["pipeline"], "backend": backend}}
    pcfg = cfg["pipeline"]
    engine = engine if engine is not None else create_engine(cfg)
    pose_est = pose_est if pose_est is not None else create_pose_estimator(cfg)
    raw_ctx = ctx_det if ctx_det is not None else create_context_detector(cfg)
    tctx = _TimedContext(raw_ctx) if raw_ctx is not None else None
    liveness = liveness if liveness is not None else create_liveness(cfg)
    frontal_max = float(cfg.get("recognition", {}).get("frontal_max_yaw", 40.0))
    max_faces = int(pcfg.get("max_faces", 10))

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise FileNotFoundError(f"Khong mo duoc video: {video}")
    cycles: list[float] = []
    per_stage: dict[str, list[float]] = {s: [] for s in STAGES}
    faces_counts: list[int] = []
    sim_crop: np.ndarray | None = None
    simulated = False
    n_read = 0
    while n_read < max_frames:
        t = time.perf_counter()
        ok, frame = cap.read()
        dec = (time.perf_counter() - t) * 1000.0
        if not ok:
            break
        n_read += 1
        st = dict.fromkeys(STAGES, 0.0)
        st["decode"] = dec
        if simulate_faces and sim_crop is not None:
            frame = _paste_faces(frame, sim_crop, simulate_faces)
            simulated = True
        if tctx is not None:
            tctx.elapsed_ms = 0.0
        t = time.perf_counter()
        if pcfg.get("two_stage_roi", True):
            faces, _scene = detect_faces_two_stage_3mp(
                frame, engine, tctx, fallback_det_width=int(pcfg.get("fallback_det_width", 1280)),
                max_faces=max_faces, always_fallback_unmatched=bool(pcfg.get("always_fallback_unmatched", True)))
        else:
            faces = engine.detect(frame)[:max_faces]
            if tctx is not None and faces:
                tctx.detect(frame)
        total_det = (time.perf_counter() - t) * 1000.0
        st["context"] = tctx.elapsed_ms if tctx is not None else 0.0
        st["detect"] = max(0.0, total_det - st["context"])
        if simulate_faces and sim_crop is None and faces:
            x1, y1, x2, y2 = (int(v) for v in faces[0].bbox[:4])
            crop = frame[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
            if crop.size:
                sim_crop = crop.copy()
        aligned, live_faces = [], []
        for f in faces:
            t = time.perf_counter()
            pose = pose_est.estimate(frame, f)
            st["pose"] += (time.perf_counter() - t) * 1000.0
            t = time.perf_counter()
            q = assess(frame, f, cfg["quality"], pose=pose)
            st["quality"] += (time.perf_counter() - t) * 1000.0
            if q.ok:
                t = time.perf_counter()
                aligned.append(engine.align(frame, f, pose=pose, pose_group=pose.pose_group))
                st["align"] += (time.perf_counter() - t) * 1000.0
                if abs(pose.gate_yaw()) <= frontal_max:
                    live_faces.append(f)
        if aligned:
            t = time.perf_counter()
            engine.embed_batch(aligned)
            st["embed"] = (time.perf_counter() - t) * 1000.0
        if liveness is not None:
            t = time.perf_counter()
            for f in live_faces:
                liveness.real_prob(frame, f.bbox)
            st["liveness"] = (time.perf_counter() - t) * 1000.0
        if len(faces) < min_faces:
            continue
        faces_counts.append(len(faces))
        cycles.append(sum(st.values()))
        for s in STAGES:
            per_stage[s].append(st[s])
    cap.release()

    def _mean(v):
        return round(float(np.mean(v)), 2) if v else None

    return {
        "backend": getattr(engine, "name", backend), "model_version": getattr(engine, "model_version", ""),
        "ep": pcfg.get("execution_provider", "cpu"), "video": str(video), "n_frames": len(cycles),
        "n_frames_read": n_read, "min_faces": min_faces,
        "cycle_ms": {"mean": _mean(cycles), "p90": round(float(np.percentile(cycles, 90)), 2) if cycles else None},
        "per_stage_ms": {s: _mean(per_stage[s]) for s in STAGES},
        "faces_per_frame": {"mean": _mean(faces_counts), "max": int(max(faces_counts)) if faces_counts else 0},
        "simulated": simulated,
    }


def _print_full_pipeline(r: dict) -> None:
    print(f"[FULL PIPELINE] backend={r['backend']} ({r['model_version']}) ep={r['ep']} video={r['video']}")
    print(f"   frame tinh: {r['n_frames']}/{r['n_frames_read']} (>= {r['min_faces']} mat)"
          f"{' [MO PHONG ghep mat]' if r['simulated'] else ''}")
    if r["cycle_ms"]["mean"] is None:
        print("   KHONG co frame nao dat --min-faces (thu --simulate-faces N)")
        return
    print(f"   chu ky: TB {r['cycle_ms']['mean']} ms | p90 {r['cycle_ms']['p90']} ms (muc tieu K4 <= 200 ms o 10 mat)")
    print("   theo buoc: " + " | ".join(f"{k} {v}" for k, v in r["per_stage_ms"].items()))
    print(f"   mat/frame: TB {r['faces_per_frame']['mean']} | max {r['faces_per_frame']['max']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark FaceGate v4 (Tapo C310 3MP + Batch 10 mat)")
    ap.add_argument("video", nargs="?", default="", help="Duong dan file video can do")
    ap.add_argument("--ten-faces-bench", action="store_true",
                    help="Do RIENG buoc embed batch 10 mat (KHONG phai full pipeline, khong dung lam bang chung K4)")
    ap.add_argument("--full-pipeline", action="store_true",
                    help="Do tron chuoi decode->detect->context->pose->quality->align->embed->liveness (Hien phap III)")
    ap.add_argument("--backend", default="", help="Ghi de pipeline.backend (mac dinh theo config.yaml)")
    ap.add_argument("--min-faces", type=int, default=1, help="Chi thong ke frame co >= N mat (K4 dung 10)")
    ap.add_argument("--simulate-faces", type=int, default=0,
                    help="Ghep lap crop mat that de du N mat/frame khi video khong co (ket qua danh dau simulated)")
    ap.add_argument("--max-frames", type=int, default=300)
    ap.add_argument("--out", default="", help="Ghi ket qua full pipeline ra file JSON")
    ap.add_argument("--ep", choices=["cpu", "openvino"], default="cpu")
    args = ap.parse_args()
    cfg = load_config()
    apply_cv_threads(cfg)
    cfg["pipeline"]["execution_provider"] = args.ep
    if args.full_pipeline:
        if not args.video:
            ap.error("--full-pipeline can duong dan video")
        r = run_full_pipeline(cfg, args.video, backend=args.backend or None, min_faces=args.min_faces,
                              max_frames=args.max_frames, simulate_faces=args.simulate_faces)
        _print_full_pipeline(r)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"   -> {args.out}")
        return
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
    for b in ((args.backend,) if args.backend else ("opencv",)):
        run(b, frames, dec_ms, cfg)


if __name__ == "__main__":
    main()
