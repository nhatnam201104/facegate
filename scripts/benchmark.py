"""So sanh toc do cac backend tren cung mot video (footage that tu camera cong).

    python scripts/benchmark.py data/footage.mp4
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import load_config  # noqa: E402
from app.engines import create_engine  # noqa: E402
from app.quality import assess  # noqa: E402


def run(backend: str, frames: list, cfg: dict) -> None:
    cfg = {**cfg, "pipeline": {**cfg["pipeline"], "backend": backend}}
    try:
        eng = create_engine(cfg)
    except FileNotFoundError as e:
        print(f"[{backend}] bo qua: {e}")
        return
    det_t, emb_t, n_faces, n_good = [], [], 0, 0
    for fr in frames:
        h, w = fr.shape[:2]
        s = min(1.0, 960 / w)
        small = cv2.resize(fr, (int(w * s), int(h * s))) if s < 1 else fr
        t = time.perf_counter()
        faces = eng.detect(small)
        det_t.append((time.perf_counter() - t) * 1000)
        for f in faces:
            f.bbox, f.kps = f.bbox / s, f.kps / s
            n_faces += 1
            if assess(fr, f, cfg["quality"]).ok:
                n_good += 1
                t = time.perf_counter()
                eng.embed_aligned(eng.align(fr, f))
                emb_t.append((time.perf_counter() - t) * 1000)
    print(f"[{backend}] {eng.model_version}")
    print(f"   detect : TB {np.mean(det_t):.1f} ms | p90 {np.percentile(det_t, 90):.1f} ms ({len(frames)} frame)")
    if emb_t:
        print(f"   embed  : TB {np.mean(emb_t):.1f} ms / mat")
    print(f"   mat phat hien: {n_faces} | dat chat luong: {n_good}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cap = cv2.VideoCapture(sys.argv[1])
    frames = []
    while len(frames) < 300:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    print(f"Doc {len(frames)} frame tu {sys.argv[1]}\n")
    cfg = load_config()
    for b in ("opencv", "onnx"):
        run(b, frames, cfg)


if __name__ == "__main__":
    main()
