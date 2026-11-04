"""Face engines: detect + embed. Hai backend co cung interface de benchmark.

- OpenCVEngine : YuNet + SFace (OpenCV Zoo, Apache-2.0)
- OnnxEngine   : SCRFD-500M + ArcFace MobileFaceNet (onnxruntime)

Landmark 5 diem luon theo thu tu anh: mat trai-anh, mat phai-anh, mui, mieng trai-anh, mieng phai-anh.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

ARCFACE_TEMPLATE = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)


@dataclass
class Face:
    bbox: np.ndarray      # x1, y1, x2, y2 (float)
    score: float
    kps: np.ndarray       # (5, 2)

    @property
    def w(self) -> float:
        return float(self.bbox[2] - self.bbox[0])

    @property
    def h(self) -> float:
        return float(self.bbox[3] - self.bbox[1])


def _order_kps(kps: np.ndarray) -> np.ndarray:
    """Dam bao mat/mieng xep theo x tang dan (trai-anh truoc)."""
    k = kps.copy()
    if k[0, 0] > k[1, 0]:
        k[[0, 1]] = k[[1, 0]]
    if k[3, 0] > k[4, 0]:
        k[[3, 4]] = k[[4, 3]]
    return k


def align_face(img: np.ndarray, kps: np.ndarray, size: int = 112) -> np.ndarray:
    M, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), ARCFACE_TEMPLATE, method=cv2.LMEDS)
    if M is None:
        M = cv2.getAffineTransform(kps[:3].astype(np.float32), ARCFACE_TEMPLATE[:3])
    return cv2.warpAffine(img, M, (size, size), borderValue=0)


def _l2n(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.float32).ravel()
    return v / (np.linalg.norm(v) + 1e-9)


class OpenCVEngine:
    name = "opencv"
    model_version = "yunet2023mar+sface2021dec"

    def __init__(self, models_dir: Path, det_score: float = 0.6):
        det = models_dir / "face_detection_yunet_2023mar.onnx"
        rec = models_dir / "face_recognition_sface_2021dec.onnx"
        for p in (det, rec):
            if not p.exists():
                raise FileNotFoundError(f"Thieu model {p.name}. Chay: python scripts/download_models.py")
        self.detector = cv2.FaceDetectorYN.create(str(det), "", (320, 320), det_score, 0.3, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(rec), "")
        self._size = (0, 0)

    def detect(self, img: np.ndarray) -> list[Face]:
        h, w = img.shape[:2]
        if (w, h) != self._size:
            self.detector.setInputSize((w, h))
            self._size = (w, h)
        _, rows = self.detector.detect(img)
        faces = []
        if rows is None:
            return faces
        for r in rows:
            x, y, bw, bh = r[:4]
            kps = _order_kps(r[4:14].reshape(5, 2))
            faces.append(Face(np.array([x, y, x + bw, y + bh], np.float32), float(r[14]), kps))
        return faces

    def align(self, img: np.ndarray, face: Face) -> np.ndarray:
        # Dung alignCrop cua SFace (dung template ma model duoc train)
        x1, y1, x2, y2 = face.bbox
        row = np.concatenate([[x1, y1, x2 - x1, y2 - y1], face.kps.ravel(), [face.score]]).astype(np.float32)
        return self.recognizer.alignCrop(img, row)

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        return _l2n(self.recognizer.feature(aligned))


class OnnxEngine:
    name = "onnx"
    model_version = "scrfd500m+w600k_mbf"

    def __init__(self, models_dir: Path, det_score: float = 0.6, input_size: int = 640):
        import onnxruntime as ort

        det = models_dir / "scrfd_500m_kps.onnx"
        rec = models_dir / "w600k_mbf.onnx"
        for p in (det, rec):
            if not p.exists():
                raise FileNotFoundError(f"Thieu model {p.name}. Chay: python scripts/download_models.py --onnx")
        so = ort.SessionOptions()
        so.log_severity_level = 3
        prov = ["CPUExecutionProvider"]
        self.det = ort.InferenceSession(str(det), so, providers=prov)
        self.rec = ort.InferenceSession(str(rec), so, providers=prov)
        self.det_in = self.det.get_inputs()[0].name
        self.rec_in = self.rec.get_inputs()[0].name
        self.det_score = det_score
        self.size = input_size
        self.strides = [8, 16, 32]
        self.num_anchors = 2
        self._anchor_cache: dict = {}

    # --- SCRFD ---
    def _anchors(self, h: int, w: int, stride: int) -> np.ndarray:
        key = (h, w, stride)
        if key not in self._anchor_cache:
            ys, xs = np.mgrid[:h, :w]
            centers = (np.stack([xs, ys], -1).astype(np.float32) * stride).reshape(-1, 2)
            centers = np.repeat(centers, self.num_anchors, axis=0)
            self._anchor_cache[key] = centers
        return self._anchor_cache[key]

    def detect(self, img: np.ndarray) -> list[Face]:
        h0, w0 = img.shape[:2]
        scale = self.size / max(h0, w0)
        nh, nw = int(round(h0 * scale)), int(round(w0 * scale))
        canvas = np.zeros((self.size, self.size, 3), np.uint8)
        canvas[:nh, :nw] = cv2.resize(img, (nw, nh))
        blob = cv2.dnn.blobFromImage(canvas, 1 / 128.0, (self.size, self.size),
                                     (127.5, 127.5, 127.5), swapRB=True)
        outs = self.det.run(None, {self.det_in: blob})
        outs = [o[0] if o.ndim == 3 else o for o in outs]
        fmc = len(self.strides)
        boxes, scores, kpss = [], [], []
        for i, s in enumerate(self.strides):
            sc = outs[i].reshape(-1)
            bb = outs[i + fmc].reshape(-1, 4) * s
            kp = outs[i + 2 * fmc].reshape(-1, 10) * s
            fh, fw = self.size // s, self.size // s
            anc = self._anchors(fh, fw, s)
            keep = np.where(sc >= self.det_score)[0]
            if keep.size == 0:
                continue
            a = anc[keep]
            b = bb[keep]
            boxes.append(np.stack([a[:, 0] - b[:, 0], a[:, 1] - b[:, 1],
                                   a[:, 0] + b[:, 2], a[:, 1] + b[:, 3]], -1))
            k = kp[keep].reshape(-1, 5, 2) + a[:, None, :]
            kpss.append(k)
            scores.append(sc[keep])
        if not boxes:
            return []
        boxes = np.concatenate(boxes) / scale
        scores = np.concatenate(scores)
        kpss = np.concatenate(kpss) / scale
        xywh = [[float(b[0]), float(b[1]), float(b[2] - b[0]), float(b[3] - b[1])] for b in boxes]
        idx = cv2.dnn.NMSBoxes(xywh, scores.tolist(), self.det_score, 0.4)
        idx = np.array(idx).reshape(-1)
        return [Face(boxes[i].astype(np.float32), float(scores[i]), _order_kps(kpss[i])) for i in idx]

    # --- ArcFace ---
    def align(self, img: np.ndarray, face: Face) -> np.ndarray:
        return align_face(img, face.kps)

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        blob = cv2.dnn.blobFromImage(aligned, 1 / 127.5, (112, 112), (127.5, 127.5, 127.5), swapRB=True)
        return _l2n(self.rec.run(None, {self.rec_in: blob})[0])


def create_engine(cfg: dict):
    backend = cfg["pipeline"]["backend"]
    models = cfg["_paths"]["models"]
    score = cfg["pipeline"]["det_score"]
    if backend == "opencv":
        return OpenCVEngine(models, score)
    if backend == "onnx":
        return OnnxEngine(models, score)
    raise ValueError(f"backend khong hop le: {backend}")
