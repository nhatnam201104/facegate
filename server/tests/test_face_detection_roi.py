"""Kiem thu hoi quy cho US1 (feature 006): tran ROI, dich bien, gop ROI chong lap, dem moc, va landmarks_valid."""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from server.config import load_config
from server.vision.context_detector import ContextScene
from server.vision.engines import Face, OpenCVEngine, landmarks_valid
from server.vision.worker_perception import compute_head_shoulder_rois_1to1, detect_faces_two_stage_3mp

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_IMG = ROOT / "data" / "enroll" / "NV002" / "00_thang-giua.jpg"
YUNET_MODEL = ROOT / "server" / "models" / "face_detection_yunet_2023mar.onnx"


def _make_face(x1: float, y1: float, x2: float, y2: float, score: float = 0.9) -> Face:
    w, h = x2 - x1, y2 - y1
    kps = np.array([
        [x1 + 0.30 * w, y1 + 0.35 * h],
        [x1 + 0.70 * w, y1 + 0.35 * h],
        [x1 + 0.50 * w, y1 + 0.55 * h],
        [x1 + 0.35 * w, y1 + 0.75 * h],
        [x1 + 0.65 * w, y1 + 0.75 * h],
    ], dtype=np.float32)
    return Face(np.array([x1, y1, x2, y2], dtype=np.float32), score, kps)


def test_roi_close_person_downscaled_to_max_side_and_coords_mapped_back():
    """(a) Hop nguoi gan cho side > 640 -> anh dua vao engine phai co canh <= 640 va toa do anh xa nguoc dung."""
    frame = np.zeros((1296, 2304, 3), dtype=np.uint8)
    close_pbox = np.array([700, 50, 1600, 1280], dtype=np.float32)  # side ~1125 > 640

    seen_roi_shapes: list[tuple[int, int]] = []

    class _Ctx:
        def detect(self, img):
            s = img.shape[1] / 2304.0
            return ContextScene(persons=[(close_pbox * s).astype(np.float32)], mediums=[])

    class _Eng:
        def detect(self, img):
            h, w = img.shape[:2]
            if w <= 1125 and h == w:
                seen_roi_shapes.append((h, w))
                return [_make_face(w * 0.25, h * 0.20, w * 0.75, h * 0.70, score=0.92)]
            return []

    faces, _ = detect_faces_two_stage_3mp(
        frame, _Eng(), _Ctx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=False,
        det_cfg={"max_roi_det_side": 640},
    )
    assert len(seen_roi_shapes) == 1
    assert max(seen_roi_shapes[0]) <= 640
    assert len(faces) == 1
    # Hop tra ve phai duoc anh xa nguoc ve he toa do khung 3MP (be rong ~ 0.5 * 1125 = 562.5 px)
    assert faces[0].w > 500.0


def test_roi_right_border_shifted_inward_and_square():
    """(b) Nguoi sat mep phai [2100, 200, 2300, 900] -> vung vuong va x2 == 2304."""
    pb = np.array([2100, 200, 2300, 900], dtype=np.float32)
    rois = compute_head_shoulder_rois_1to1((1296, 2304), [pb])
    assert len(rois) == 1
    x1, y1, x2, y2 = rois[0]
    assert x2 == 2304
    assert (x2 - x1) == (y2 - y1)
    assert x1 >= 0 and y1 >= 0 and y2 <= 1296


def test_roi_bottom_border_shifted_inward_and_square():
    """(c) Nguoi sat mep duoi -> vung vuong va y2 == 1296."""
    pb = np.array([1000, 1020, 1300, 1290], dtype=np.float32)
    rois = compute_head_shoulder_rois_1to1((1296, 2304), [pb])
    assert len(rois) == 1
    x1, y1, x2, y2 = rois[0]
    assert y2 == 1296
    assert (x2 - x1) == (y2 - y1)
    assert x1 >= 0 and y1 >= 0 and x2 <= 2304


def test_roi_overlapping_persons_merged_when_iou_above_threshold():
    """(d) Hai hop nguoi chong lap IoU > 0.60 -> gop thanh 1 vung; hai nguoi dung cach nhau giu 2 vung."""
    p1 = np.array([600, 200, 950, 1000], dtype=np.float32)
    p2 = np.array([620, 210, 970, 1010], dtype=np.float32)
    rois_merged = compute_head_shoulder_rois_1to1((1296, 2304), [p1, p2], merge_iou=0.60)
    assert len(rois_merged) == 1

    p_left = np.array([200, 200, 500, 1000], dtype=np.float32)
    p_right = np.array([1100, 200, 1400, 1000], dtype=np.float32)
    rois_sep = compute_head_shoulder_rois_1to1((1296, 2304), [p_left, p_right], merge_iou=0.60)
    assert len(rois_sep) == 2


def test_roi_invariants_bounds_and_squareness():
    """(e) Moi vung thoa x1>=0, y1>=0, x2<=w, y2<=h va vuong (tru khung nho hon side)."""
    boxes = [
        np.array([0, 0, 180, 500], dtype=np.float32),
        np.array([2150, 950, 2304, 1296], dtype=np.float32),
        np.array([900, 100, 1500, 1200], dtype=np.float32),
    ]
    rois = compute_head_shoulder_rois_1to1((1296, 2304), boxes)
    assert 1 <= len(rois) <= len(boxes)
    for x1, y1, x2, y2 in rois:
        assert x1 >= 0 and y1 >= 0 and x2 <= 2304 and y2 <= 1296
        assert (x2 - x1) == (y2 - y1)

    # Khung nho hon 320x320 -> tra dung khung hinh
    small_rois = compute_head_shoulder_rois_1to1((200, 260), [np.array([20, 20, 180, 190], dtype=np.float32)])
    assert small_rois == [(0, 0, 260, 200)]


def test_landmarks_valid_geometry_checks():
    """Kiem tra ham thuan landmarks_valid() theo R11/FR-012."""
    bbox = np.array([100.0, 100.0, 200.0, 220.0], dtype=np.float32)
    good_kps = np.array([
        [130.0, 140.0], [170.0, 140.0], [150.0, 165.0], [135.0, 190.0], [165.0, 190.0]
    ], dtype=np.float32)
    assert landmarks_valid(bbox, good_kps) is True

    # NaN / Inf
    bad_nan = good_kps.copy()
    bad_nan[2, 0] = np.nan
    assert landmarks_valid(bbox, bad_nan) is False

    # Mat duoi mieng
    bad_flip = good_kps.copy()
    bad_flip[0, 1] = bad_flip[1, 1] = 205.0
    assert landmarks_valid(bbox, bad_flip) is False

    # Khoang cach hai mat < 25% bw (bw=100 -> < 25)
    bad_eyes = good_kps.copy()
    bad_eyes[0, 0], bad_eyes[1, 0] = 145.0, 155.0
    assert landmarks_valid(bbox, bad_eyes) is False

    # Diem nam ngoai hop mo rong 25%
    bad_out = good_kps.copy()
    bad_out[4, 0] = 260.0
    assert landmarks_valid(bbox, bad_out) is False


def test_real_close_range_person_single_face_and_fast():
    """T013: Anh that 00_thang-giua.jpg tren khung 3MP mo phong nguoi dung 0.7-0.9m -> dung 1 mat va <= 50ms."""
    if not SAMPLE_IMG.exists() or not YUNET_MODEL.exists():
        pytest.skip("Thieu model YuNet hoac anh mau data/enroll/NV002/00_thang-giua.jpg")

    cfg = load_config()
    eng = OpenCVEngine(cfg["_paths"]["models"], det_score=float(cfg["pipeline"]["det_score"]), cfg=cfg)
    sample = cv2.imread(str(SAMPLE_IMG))
    assert sample is not None

    # Dung khung 3MP 2304x1296: dan anh mau vao vung dau-vai cua hop nguoi gan [700, 50, 1600, 1280]
    frame = np.full((1296, 2304, 3), 120, dtype=np.uint8)
    close_pb = np.array([700, 50, 1600, 1280], dtype=np.float32)
    sh, sw = sample.shape[:2]
    target_side = 1000
    scale = target_side / max(sh, sw)
    resized = cv2.resize(sample, (int(sw * scale), int(sh * scale)))
    rh, rw = resized.shape[:2]
    x0 = 1150 - rw // 2
    y0 = 40
    frame[y0:y0 + rh, x0:x0 + rw] = resized

    class _FixedCtx:
        def detect(self, img):
            s = img.shape[1] / 2304.0
            return ContextScene(persons=[(close_pb * s).astype(np.float32)], mediums=[])

    # Warm-up
    detect_faces_two_stage_3mp(
        frame, eng, _FixedCtx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=False,
    )

    times_ms = []
    faces_out = []
    for _ in range(5):
        t0 = time.perf_counter()
        faces_out, _ = detect_faces_two_stage_3mp(
            frame, eng, _FixedCtx(),
            fallback_det_width=1280, max_faces=10, always_fallback_unmatched=False,
        )
        times_ms.append((time.perf_counter() - t0) * 1000.0)

    assert len(faces_out) == 1, f"Ky vong 1 khuon mat cho nguoi dung gan, nhan {len(faces_out)}"
    assert min(times_ms) <= 50.0, f"Thoi gian vung dau-vai qua cham: {times_ms}"
