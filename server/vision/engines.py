"""Face engine: detect + embed.

- OpenCVEngine : YuNet (OpenCV Zoo, MIT) + SFace (OpenCV Zoo, Apache-2.0)

Backend EdgeFace-XS (CC BY-NC-SA) va InsightFace SCRFD/ArcFace (phi thuong mai) da bi go (feature 002).
ARCFACE_TEMPLATE ben duoi chi la toa do hinh hoc 5 diem can chinh 112x112, khong phai trong so model.

Landmark 5 diem luon theo thu tu anh: mat trai-anh, mat phai-anh, mui, mieng trai-anh, mieng phai-anh.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .license_guard import LicenseError, require

ARCFACE_TEMPLATE = np.array(
    [[38.2946, 51.6963], [73.5318, 51.5014], [56.0252, 71.7366],
     [41.5493, 92.3655], [70.7299, 92.2041]], dtype=np.float32)

TEMPLATE_FRONTAL = ARCFACE_TEMPLATE
TEMPLATE_LEFT_PROFILE = np.array(
    [[48.0, 48.0], [88.0, 52.0], [24.0, 68.0],
     [40.0, 90.0], [78.0, 94.0]], dtype=np.float32)
TEMPLATE_RIGHT_PROFILE = np.array(
    [[24.0, 52.0], [64.0, 48.0], [88.0, 68.0],
     [34.0, 94.0], [72.0, 90.0]], dtype=np.float32)


@dataclass
class Face:
    bbox: np.ndarray      # x1, y1, x2, y2 (float)
    score: float
    kps: np.ndarray       # (5, 2)
    landmarks_valid: bool = True

    @property
    def w(self) -> float:
        return float(self.bbox[2] - self.bbox[0])

    @property
    def h(self) -> float:
        return float(self.bbox[3] - self.bbox[1])


def landmarks_valid(bbox: np.ndarray, kps: np.ndarray, margin: float = 0.25) -> bool:
    """Kiem tra hop le hinh hoc 5 diem moc: huu han, trong hop mo rong 25%, mat tren mieng, kc hai mat >= 25% rong hop."""
    b = np.asarray(bbox, dtype=np.float64)
    k = np.asarray(kps, dtype=np.float64)
    if b.shape != (4,) or k.shape != (5, 2):
        return False
    if not (np.all(np.isfinite(b)) and np.all(np.isfinite(k))):
        return False
    x1, y1, x2, y2 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
    bw, bh = x2 - x1, y2 - y1
    if bw <= 0.0 or bh <= 0.0:
        return False
    mx, my = margin * bw, margin * bh
    if np.any(k[:, 0] < x1 - mx) or np.any(k[:, 0] > x2 + mx):
        return False
    if np.any(k[:, 1] < y1 - my) or np.any(k[:, 1] > y2 + my):
        return False
    eye_mid_y = 0.5 * (k[0, 1] + k[1, 1])
    mouth_mid_y = 0.5 * (k[3, 1] + k[4, 1])
    if not (eye_mid_y < mouth_mid_y):
        return False
    eye_dist = float(np.hypot(k[1, 0] - k[0, 0], k[1, 1] - k[0, 1]))
    if eye_dist < 0.25 * bw:
        return False
    return True


def _order_kps(kps: np.ndarray) -> np.ndarray:
    """Dam bao mat/mieng xep theo x tang dan (trai-anh truoc)."""
    k = kps.copy()
    if k[0, 0] > k[1, 0]:
        k[[0, 1]] = k[[1, 0]]
    if k[3, 0] > k[4, 0]:
        k[[3, 4]] = k[[4, 3]]
    return k


def align_face(img: np.ndarray, kps: np.ndarray, size: int = 112,
               template: np.ndarray = TEMPLATE_FRONTAL) -> np.ndarray:
    dst = template * (size / 112.0)
    M, _ = cv2.estimateAffinePartial2D(kps.astype(np.float32), dst, method=cv2.LMEDS)
    if M is None:
        M = cv2.getAffineTransform(kps[:3].astype(np.float32), dst[:3])
    return cv2.warpAffine(img, M, (size, size), borderValue=0)


def align_by_pose(img: np.ndarray, face: Face, pose=None,
                  pose_group: str | None = None, size: int = 112) -> np.ndarray:
    """Luon can chinh ve TEMPLATE_FRONTAL chuan cua model; dung kps5 cua bo uoc luong tu the khi co."""
    if pose is not None and getattr(pose, "kps5", None) is not None and len(pose.kps5) == 5:
        return align_face(img, pose.kps5, size=size, template=TEMPLATE_FRONTAL)
    return align_face(img, face.kps, size=size, template=TEMPLATE_FRONTAL)


def _l2n(v: np.ndarray) -> np.ndarray:
    v = v.astype(np.float32).ravel()
    return v / (np.linalg.norm(v) + 1e-9)


class OpenCVEngine:
    name = "opencv"
    model_version = "yunet2023mar+sface2021dec"

    def __init__(self, models_dir: Path, det_score: float = 0.6, cfg: dict | None = None):
        models_dir = Path(models_dir)
        det = models_dir / "face_detection_yunet_2023mar.onnx"
        rec = models_dir / "face_recognition_sface_2021dec.onnx"
        for p in (det, rec):
            if not p.exists():
                raise FileNotFoundError(f"Thieu model {p.name}. Chay: python server/scripts/download_models.py")
            require(cfg, p.name, models_dir)
        pcfg = (cfg or {}).get("pipeline", {})
        ort_threads = int(pcfg.get("ort_threads", 2))
        cv2.setNumThreads(ort_threads)
        det_nms = float(pcfg.get("det_nms", 0.40))
        buckets = pcfg.get("roi_det_buckets", [320, 480, 640])
        self._roi_det_buckets = sorted(int(b) for b in buckets) if buckets else [320, 480, 640]
        self.detector = cv2.FaceDetectorYN.create(str(det), "", (320, 320), det_score, det_nms, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(rec), "")
        self._size = (0, 0)

    def detect(self, img: np.ndarray) -> list[Face]:
        h, w = img.shape[:2]
        det_img = img
        max_dim = max(h, w)
        if self._roi_det_buckets and max_dim < self._roi_det_buckets[-1]:
            target = next(b for b in self._roi_det_buckets if b >= max_dim)
            if h != target or w != target:
                det_img = cv2.copyMakeBorder(
                    img, 0, target - h, 0, target - w,
                    cv2.BORDER_CONSTANT, value=(114, 114, 114),
                )
        dh, dw = det_img.shape[:2]
        if (dw, dh) != self._size:
            self.detector.setInputSize((dw, dh))
            self._size = (dw, dh)
        _, rows = self.detector.detect(det_img)
        faces = []
        if rows is None:
            return faces
        for r in rows:
            x, y, bw, bh = r[:4]
            bbox = np.array([x, y, x + bw, y + bh], np.float32)
            kps = _order_kps(r[4:14].reshape(5, 2))
            faces.append(Face(bbox, float(r[14]), kps, landmarks_valid=landmarks_valid(bbox, kps)))
        return faces

    def align(self, img: np.ndarray, face: Face, pose=None, pose_group: str | None = None) -> np.ndarray:
        grp = pose_group or (pose.pose_group if pose is not None else "frontal")
        if grp != "frontal":
            return align_by_pose(img, face, pose=pose, pose_group=grp, size=112)
        x1, y1, x2, y2 = face.bbox
        kps = pose.kps5 if pose is not None else face.kps
        row = np.concatenate([[x1, y1, x2 - x1, y2 - y1], kps.ravel(), [face.score]]).astype(np.float32)
        return self.recognizer.alignCrop(img, row)

    def embed_aligned(self, aligned: np.ndarray) -> np.ndarray:
        return _l2n(self.recognizer.feature(aligned))

    def embed_batch(self, aligned_list: list[np.ndarray]) -> list[np.ndarray]:
        return [self.embed_aligned(a) for a in aligned_list]


REMOVED_BACKENDS = ("edgeface", "onnx")


def create_engine(cfg: dict):
    """Chi con backend 'opencv' (YuNet MIT + SFace Apache-2.0). Moi model duoc kiem giay phep truoc khi nap."""
    backend = cfg["pipeline"]["backend"]
    models = cfg["_paths"]["models"]
    score = float(cfg["pipeline"]["det_score"])
    if backend in REMOVED_BACKENDS:
        raise LicenseError(
            f"LICENSE: backend '{backend}' đã bị gỡ do giấy phép phi thương mại; dùng 'opencv' "
            "(xem specs/002-license-compliant-models)")
    if backend == "opencv":
        return OpenCVEngine(models, score, cfg=cfg)
    raise ValueError(f"backend khong hop le: {backend}")
