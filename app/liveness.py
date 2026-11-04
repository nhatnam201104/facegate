"""Chong gia mao (anh in, anh/video tren man hinh) bang MiniFASNet - passive, 1 camera RGB.

Model: Minivision Silent-Face-Anti-Spoofing (Apache-2.0), ban ONNX tu yakhyo/face-anti-spoofing.
Gop 2 model (V2 crop x2.7, V1SE crop x4.0) giong cach lam goc: trung binh softmax, lop 1 = nguoi that.
Chi giam rui ro: video man hinh chat luong cao van co the lot -> cham cong tinh luong nen dung thiet bi IR.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("liveness")

MODELS = (("MiniFASNetV2.onnx", 2.7), ("MiniFASNetV1SE.onnx", 4.0))
INPUT = 80


def _crop(img: np.ndarray, bbox: np.ndarray, scale: float) -> np.ndarray:
    h_img, w_img = img.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in bbox)
    bw, bh = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
    s = min((h_img - 1) / bh, (w_img - 1) / bw, scale)
    cx, cy, nw, nh = x1 + bw / 2, y1 + bh / 2, bw * s, bh * s
    a, b = max(0, int(cx - nw / 2)), max(0, int(cy - nh / 2))
    c, d = min(w_img - 1, int(cx + nw / 2)), min(h_img - 1, int(cy + nh / 2))
    return cv2.resize(img[b:d + 1, a:c + 1], (INPUT, INPUT))


class Liveness:
    def __init__(self, models_dir: Path):
        import onnxruntime as ort

        missing = [n for n, _ in MODELS if not (models_dir / n).exists()]
        if missing:
            raise FileNotFoundError(f"Thieu model chong gia mao {missing}. Chay: python scripts/download_models.py")
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.sessions = [(ort.InferenceSession(str(models_dir / n), so, providers=["CPUExecutionProvider"]), sc)
                         for n, sc in MODELS]

    def real_prob(self, img: np.ndarray, bbox: np.ndarray) -> float:
        """Xac suat khuon mat la nguoi that (0..1). img la frame BGR goc (chua resize)."""
        p = np.zeros(3, np.float64)
        for sess, scale in self.sessions:
            x = _crop(img, bbox, scale).astype(np.float32).transpose(2, 0, 1)[None]
            z = sess.run(None, {sess.get_inputs()[0].name: x})[0][0].astype(np.float64)
            e = np.exp(z - z.max())
            p += e / e.sum()
        return float(p[1] / len(self.sessions))


def create_liveness(cfg: dict) -> Liveness | None:
    lc = cfg.get("liveness", {})
    if not lc.get("enabled", False):
        log.warning("Chong gia mao dang TAT (liveness.enabled=false): anh/man hinh dien thoai co the duoc cham cong")
        return None
    return Liveness(cfg["_paths"]["models"])
