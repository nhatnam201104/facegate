"""Phat hien ngu canh co the & vat chua gia mao bang YOLOX-Nano ONNX (Apache-2.0) kem quy tac Viewport-Aware (H3)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("context")

COCO_PERSON = 0
SPOOF_MEDIUM_CLASSES = {
    62: "tv",
    63: "laptop",
    67: "cell phone",
    73: "book",
}


@dataclass
class ContextScene:
    persons: list[np.ndarray] = field(default_factory=list)
    mediums: list[tuple[np.ndarray, str, float]] = field(default_factory=list)


def _nms_boxes(boxes: np.ndarray, scores: np.ndarray, iou_thr: float = 0.45) -> list[int]:
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1) + 1e-6
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        ovr = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
        order = order[np.where(ovr <= iou_thr)[0] + 1]
    return keep


def _face_overlap_ratio(face_box: np.ndarray, other_box: np.ndarray) -> float:
    x1, y1 = max(float(face_box[0]), float(other_box[0])), max(float(face_box[1]), float(other_box[1]))
    x2, y2 = min(float(face_box[2]), float(other_box[2])), min(float(face_box[3]), float(other_box[3]))
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    f_area = max(1.0, float(face_box[2] - face_box[0]) * float(face_box[3] - face_box[1]))
    return float(inter / f_area)


def _anatomical_head_match(face_box: np.ndarray, person_box: np.ndarray,
                           ratio_bounds: tuple[float, float] = (0.10, 0.70)) -> bool:
    fx = float(face_box[0] + face_box[2]) * 0.5
    fy = float(face_box[1] + face_box[3]) * 0.5
    fw = max(1.0, float(face_box[2] - face_box[0]))
    px1, py1, px2, py2 = (float(v) for v in person_box[:4])
    pw, ph = max(px2 - px1, 1.0), max(py2 - py1, 1.0)
    if not (px1 - 0.1 * pw <= fx <= px2 + 0.1 * pw):
        return False
    if not (py1 - 0.15 * ph <= fy <= py1 + 0.50 * ph):
        return False
    return ratio_bounds[0] <= (fw / pw) <= ratio_bounds[1]


def evaluate_face_context(
    face_box: np.ndarray,
    scene: ContextScene | None,
    frame_height: int | None = None,
    all_faces: list | None = None,
    require_body: bool = True,
    viewport_bottom_grace: float = 0.80,
    screen_face_overlap: float = 0.80,
) -> dict:
    """Danh gia ngu canh co the kem quy tac Viewport-Aware (mien tru khi cat khung day anh hoac che boi nguoi truoc)."""
    if scene is None:
        return {"medium_clean": True, "body_coupled": True, "viewport_truncated": False, "spoof_reason": None}

    coupled_person = None
    for p_box in scene.persons:
        if _anatomical_head_match(face_box, p_box):
            coupled_person = p_box
            break

    fw = float(face_box[2] - face_box[0])
    for m_box, m_label, _ in scene.mediums:
        if _face_overlap_ratio(face_box, m_box) >= screen_face_overlap:
            mw, mh = float(m_box[2] - m_box[0]), float(m_box[3] - m_box[1])
            bg_tv = coupled_person is not None and m_label == "tv" and mw >= 1.15 * mh and mw >= 1.5 * fw
            if not bg_tv:
                return {
                    "medium_clean": False,
                    "body_coupled": False,
                    "viewport_truncated": False,
                    "spoof_reason": "screen_detected",
                    "medium_label": m_label,
                }

    # Quy tac Viewport-Aware (H3): neu day hop mat cham sat bien duoi anh (y2 >= viewport_bottom_grace * H)
    # hoac bi nguoi dung truoc (tien canh) che mat phan vai/than -> mien tru unattached_face!
    fy2 = float(face_box[3])
    bottom_truncated = frame_height is not None and fy2 >= viewport_bottom_grace * float(frame_height)
    foreground_occluded = any(
        float(p[1]) <= fy2 <= float(p[3]) and (float(p[0]) <= float(face_box[0] + face_box[2]) * 0.5 <= float(p[2]))
        for p in scene.persons
    )
    if coupled_person is None and require_body and not (bottom_truncated or foreground_occluded):
        return {
            "medium_clean": True,
            "body_coupled": False,
            "viewport_truncated": False,
            "spoof_reason": "unattached_face",
        }

    return {
        "medium_clean": True,
        "body_coupled": True,
        "viewport_truncated": bool(bottom_truncated or foreground_occluded),
        "spoof_reason": None,
    }


class ContextDetector:
    MODEL_FILE = "yolox_nano_coco.onnx"

    def __init__(self, models_dir: Path, person_conf: float = 0.45, medium_conf: float = 0.35,
                 providers: list[str] | None = None):
        import onnxruntime as ort
        model_path = models_dir / self.MODEL_FILE
        if not model_path.exists():
            raise FileNotFoundError(f"Thieu model {self.MODEL_FILE}")
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.session = ort.InferenceSession(str(model_path), so, providers=providers or ["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        self.in_name = inp.name
        shape = inp.shape
        self.in_size = (int(shape[2]) if isinstance(shape[2], int) else 416,
                        int(shape[3]) if isinstance(shape[3], int) else 416)
        self.person_conf = person_conf
        self.medium_conf = medium_conf
        self._grids, self._strides = self._build_grids(self.in_size)

    @staticmethod
    def _build_grids(in_size: tuple[int, int]):
        hs, ws = in_size
        grids, strides = [], []
        for s in (8, 16, 32):
            gh, gw = hs // s, ws // s
            yv, xv = np.meshgrid(np.arange(gh), np.arange(gw), indexing="ij")
            g = np.stack((xv, yv), axis=2).reshape(1, -1, 2).astype(np.float32)
            grids.append(g)
            strides.append(np.full((1, g.shape[1], 1), s, dtype=np.float32))
        return np.concatenate(grids, axis=1), np.concatenate(strides, axis=1)

    def detect(self, img: np.ndarray) -> ContextScene:
        h0, w0 = img.shape[:2]
        ih, iw = self.in_size
        r = min(ih / h0, iw / w0)
        nh, nw = int(round(h0 * r)), int(round(w0 * r))
        padded = np.full((ih, iw, 3), 114, dtype=np.uint8)
        padded[:nh, :nw] = cv2.resize(img, (nw, nh))
        blob = padded.transpose(2, 0, 1)[None].astype(np.float32)
        pred = self.session.run(None, {self.in_name: blob})[0]
        pred[..., :2] = (pred[..., :2] + self._grids) * self._strides
        pred[..., 2:4] = np.exp(pred[..., 2:4]) * self._strides
        p = pred[0]
        obj = p[:, 4:5]
        cls_scores = obj * p[:, 5:]
        cls_ids = np.argmax(cls_scores, axis=1)
        scores = cls_scores[np.arange(len(p)), cls_ids]

        cx, cy, bw, bh = p[:, 0], p[:, 1], p[:, 2], p[:, 3]
        boxes = np.stack([cx - bw * 0.5, cy - bh * 0.5, cx + bw * 0.5, cy + bh * 0.5], axis=1) / r

        scene = ContextScene()
        p_mask = (cls_ids == COCO_PERSON) & (scores >= self.person_conf)
        if np.any(p_mask):
            keep = _nms_boxes(boxes[p_mask], scores[p_mask])
            scene.persons = [boxes[p_mask][k].astype(np.float32) for k in keep]

        for cid, label in SPOOF_MEDIUM_CLASSES.items():
            m_mask = (cls_ids == cid) & (scores >= self.medium_conf)
            if np.any(m_mask):
                keep = _nms_boxes(boxes[m_mask], scores[m_mask])
                for k in keep:
                    scene.mediums.append((boxes[m_mask][k].astype(np.float32), label, float(scores[m_mask][k])))
        return scene


def create_context_detector(cfg: dict) -> ContextDetector | None:
    lc = cfg.get("liveness", {}).get("context", {})
    if not lc.get("enabled", True):
        return None
    models_dir = cfg["_paths"]["models"]
    if not (models_dir / ContextDetector.MODEL_FILE).exists():
        return None
    return ContextDetector(
        models_dir,
        person_conf=float(lc.get("person_conf", 0.45)),
        medium_conf=float(lc.get("medium_conf", 0.35)),
    )
