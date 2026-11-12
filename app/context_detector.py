"""Phát hiện ngữ cảnh cơ thể người & vật chứa giả mạo (điện thoại, tablet, màn hình, khung ảnh) bằng YOLOX-Nano ONNX.

Giấy phép mô hình gốc: Apache-2.0 (Megvii-BaseDetection/YOLOX).
Chạy 1 lần cho toàn khung hình (416x416) để phục vụ đồng thời:
  1. Liên kết hộp cơ thể person_bbox giữ ổn định ID trong Tracker khi nhân viên xoay đầu 0°..90°.
  2. Chặn vật chứa giả mạo (cell phone, laptop, tv, book) và hành vi 1 người cầm ảnh đồng nghiệp (2 mặt trên 1 cơ thể).
"""
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
    persons: list[np.ndarray] = field(default_factory=list)                      # [x1, y1, x2, y2]
    mediums: list[tuple[np.ndarray, str, float]] = field(default_factory=list)   # (bbox, label, score)


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


def _face_inside_person_torso_or_head(face_box: np.ndarray, person_box: np.ndarray) -> bool:
    fx = float(face_box[0] + face_box[2]) * 0.5
    fy = float(face_box[1] + face_box[3]) * 0.5
    px1, py1, px2, py2 = (float(v) for v in person_box[:4])
    pw, ph = max(px2 - px1, 1.0), max(py2 - py1, 1.0)
    return (px1 - 0.1 * pw <= fx <= px2 + 0.1 * pw) and (py1 - 0.15 * ph <= fy <= py2)


def _anatomical_head_match(face_box: np.ndarray, person_box: np.ndarray,
                           ratio_bounds: tuple[float, float] = (0.12, 0.65)) -> bool:
    fx = float(face_box[0] + face_box[2]) * 0.5
    fy = float(face_box[1] + face_box[3]) * 0.5
    fw = max(1.0, float(face_box[2] - face_box[0]))
    px1, py1, px2, py2 = (float(v) for v in person_box[:4])
    pw, ph = max(px2 - px1, 1.0), max(py2 - py1, 1.0)
    if not (px1 - 0.08 * pw <= fx <= px2 + 0.08 * pw):
        return False
    if not (py1 - 0.12 * ph <= fy <= py1 + 0.45 * ph):
        return False
    return ratio_bounds[0] <= (fw / pw) <= ratio_bounds[1]


def evaluate_face_context(
    face_box: np.ndarray,
    scene: ContextScene | None,
    all_faces: list | None = None,
    require_body: bool = True,
    ratio_bounds: tuple[float, float] = (0.12, 0.65),
    screen_face_overlap: float = 0.80,
) -> dict:
    """Đánh giá ngữ cảnh vật lý của một khuôn mặt trong khung hình."""
    if scene is None:
        return {
            "medium_clean": True,
            "medium_nearby": False,
            "body_coupled": True,
            "spoof_reason": None,
            "medium_label": None,
        }

    # 1. Kiểm tra gắn kết Đầu – Cơ thể và phát hiện 2 mặt trên cùng 1 người trước
    raw_faces = [f.bbox if hasattr(f, "bbox") else np.asarray(f, np.float32) for f in (all_faces or [face_box])]
    coupled_person = None
    for p_box in scene.persons:
        faces_on_this_person = [fb for fb in raw_faces if _face_inside_person_torso_or_head(fb, p_box)]
        if any(np.allclose(fb, face_box, atol=1.0) for fb in faces_on_this_person):
            if len(faces_on_this_person) >= 2:
                # Nếu có >= 2 mặt rơi vào cùng 1 khung cơ thể mà chỉ có 1 cơ thể (không có cơ thể thứ 2 cạnh đó)
                other_persons = [op for op in scene.persons if not np.allclose(op, p_box, atol=1.0)]
                explained_by_other = any(
                    any(_anatomical_head_match(fb, op, ratio_bounds) for op in other_persons)
                    for fb in faces_on_this_person if not np.allclose(fb, face_box, atol=1.0)
                )
                if not explained_by_other:
                    return {
                        "medium_clean": True,
                        "medium_nearby": False,
                        "body_coupled": False,
                        "spoof_reason": "multi_face_single_body",
                        "medium_label": None,
                    }
            if _anatomical_head_match(face_box, p_box, ratio_bounds):
                coupled_person = p_box
                break

    # 2. Quét thiết bị màn hình / khung sách báo: chỉ coi là mặt trong màn hình khi hộp mặt nằm phần lớn
    #    bên trong thiết bị (>= screen_face_overlap) VÀ không gắn với thân người nằm ngoài thiết bị.
    fw = float(face_box[2] - face_box[0])
    fh = float(face_box[3] - face_box[1])
    cx = float(face_box[0] + face_box[2]) * 0.5
    cy = float(face_box[1] + face_box[3]) * 0.5
    exp_box = np.array([cx - 1.25 * fw, cy - 1.25 * fh, cx + 1.25 * fw, cy + 1.25 * fh], np.float32)

    medium_nearby = False
    nearby_label = None
    for m_box, m_label, _ in scene.mediums:
        face_in_med = _face_overlap_ratio(face_box, m_box)
        if face_in_med >= 0.15 or _face_overlap_ratio(m_box, exp_box) >= 0.35:
            medium_nearby = True
            nearby_label = nearby_label or m_label
        if face_in_med >= screen_face_overlap:
            body_outside_medium = (
                coupled_person is not None
                and _face_overlap_ratio(coupled_person, m_box) < 0.75
            )
            if not body_outside_medium:
                return {
                    "medium_clean": False,
                    "medium_nearby": True,
                    "body_coupled": False,
                    "spoof_reason": "screen_detected",
                    "medium_label": m_label,
                }

    if coupled_person is None and require_body:
        return {
            "medium_clean": True,
            "medium_nearby": medium_nearby,
            "body_coupled": False,
            "spoof_reason": "unattached_face",
            "medium_label": nearby_label,
        }

    return {
        "medium_clean": True,
        "medium_nearby": medium_nearby,
        "body_coupled": coupled_person is not None or not require_body,
        "spoof_reason": None,
        "medium_label": nearby_label,
    }


class ContextDetector:
    MODEL_FILE = "yolox_nano_coco.onnx"

    def __init__(self, models_dir: Path, person_conf: float = 0.45, medium_conf: float = 0.35):
        import onnxruntime as ort

        model_path = models_dir / self.MODEL_FILE
        if not model_path.exists():
            raise FileNotFoundError(f"Thieu model {self.MODEL_FILE}. Chay: python scripts/download_models.py")
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.session = ort.InferenceSession(str(model_path), so, providers=["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        self.in_name = inp.name
        shape = inp.shape
        self.in_size = (int(shape[2]) if isinstance(shape[2], int) else 416,
                        int(shape[3]) if isinstance(shape[3], int) else 416)
        self.person_conf = float(person_conf)
        self.medium_conf = float(medium_conf)
        self._grids, self._strides = self._build_grids(self.in_size)

    @staticmethod
    def _build_grids(in_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
        grids, strides = [], []
        for s in (8, 16, 32):
            hs, ws = in_size[0] // s, in_size[1] // s
            xv, yv = np.meshgrid(np.arange(ws), np.arange(hs))
            g = np.stack((xv, yv), 2).reshape(1, -1, 2).astype(np.float32)
            grids.append(g)
            strides.append(np.full((*g.shape[:2], 1), s, dtype=np.float32))
        return np.concatenate(grids, 1), np.concatenate(strides, 1)

    def detect(self, frame: np.ndarray) -> ContextScene:
        ih, iw = self.in_size
        h0, w0 = frame.shape[:2]
        r = min(ih / h0, iw / w0)
        nh, nw = int(h0 * r), int(w0 * r)
        padded = np.full((ih, iw, 3), 114, dtype=np.uint8)
        padded[:nh, :nw] = cv2.resize(frame, (nw, nh), interpolation=cv2.INTER_LINEAR)
        blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)

        pred = self.session.run(None, {self.in_name: blob})[0].copy()
        pred[..., :2] = (pred[..., :2] + self._grids) * self._strides
        pred[..., 2:4] = np.exp(pred[..., 2:4]) * self._strides
        p = pred[0]

        cx, cy, bw, bh = p[:, 0], p[:, 1], p[:, 2], p[:, 3]
        boxes = np.stack([cx - bw * 0.5, cy - bh * 0.5, cx + bw * 0.5, cy + bh * 0.5], axis=-1) / r
        obj_conf = p[:, 4:5]
        cls_probs = p[:, 5:]
        scores_all = obj_conf * cls_probs

        # Trích xuất person (class 0)
        p_scores = scores_all[:, COCO_PERSON]
        p_mask = p_scores >= self.person_conf
        persons = []
        if np.any(p_mask):
            pb, ps = boxes[p_mask], p_scores[p_mask]
            persons = [pb[i].astype(np.float32) for i in _nms_boxes(pb, ps, 0.45)]

        # Trích xuất các vật chứa giả mạo (điện thoại, laptop, màn hình, khung sách/giấy)
        mediums: list[tuple[np.ndarray, str, float]] = []
        for cid, cname in SPOOF_MEDIUM_CLASSES.items():
            m_scores = scores_all[:, cid]
            m_mask = m_scores >= self.medium_conf
            if np.any(m_mask):
                mb, ms = boxes[m_mask], m_scores[m_mask]
                for idx in _nms_boxes(mb, ms, 0.45):
                    mediums.append((mb[idx].astype(np.float32), cname, float(ms[idx])))

        return ContextScene(persons=persons, mediums=mediums)


def create_context_detector(cfg: dict) -> ContextDetector | None:
    lc = cfg.get("liveness", {})
    if not lc.get("enabled", True):
        return None
    cc = lc.get("context", {})
    if not cc.get("enabled", True):
        return None
    models_dir = cfg["_paths"]["models"]
    if not (models_dir / ContextDetector.MODEL_FILE).exists():
        log.warning("Chua co %s -> tat kiem tra vat chua/co the YOLOX-Nano", ContextDetector.MODEL_FILE)
        return None
    return ContextDetector(
        models_dir,
        person_conf=float(cc.get("person_conf", 0.45)),
        medium_conf=float(cc.get("medium_conf", 0.35)),
    )
