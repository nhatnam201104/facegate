"""Kiem thu hoi quy cho US4 & US5 (feature 006): dong nhat nguong phat hien 3 luong va bo thu nho lap ContextDetector."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from server.config import load_config
from server.enrollment.guided import GuidedSession
from server.enrollment.video import process_enrollment
from server.vision.context_detector import ContextDetector, ContextScene
from server.vision.engines import Face, OpenCVEngine
from server.vision.pipeline import Pipeline
from server.vision.worker_perception import PerceptionWorker, detect_faces_two_stage_3mp

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_IMG = ROOT / "data" / "enroll" / "NV002" / "00_thang-giua.jpg"
YUNET_MODEL = ROOT / "server" / "models" / "face_detection_yunet_2023mar.onnx"
YOLOX_MODEL = ROOT / "server" / "models" / "yolox_nano_coco.onnx"


def _make_60px_face_3mp_frame() -> np.ndarray:
    """Dan anh that NV002/00_thang-giua.jpg sao cho be rong khuon mat tren khung 3MP dung ~60px."""
    sample = cv2.imread(str(SAMPLE_IMG))
    assert sample is not None
    cfg = load_config()
    eng = OpenCVEngine(cfg["_paths"]["models"], det_score=float(cfg["pipeline"]["det_score"]), cfg=cfg)
    base_faces = eng.detect(sample)
    assert base_faces, "Khong phat hien mat tren anh mau"
    fw_base = base_faces[0].w
    scale = 60.0 / fw_base
    sh, sw = sample.shape[:2]
    patch = cv2.resize(sample, (max(1, int(round(sw * scale))), max(1, int(round(sh * scale)))))
    ph, pw = patch.shape[:2]
    frame = np.full((1296, 2304, 3), 120, dtype=np.uint8)
    y0, x0 = 400, 900
    frame[y0:y0 + ph, x0:x0 + pw] = patch
    return frame


def test_single_stage_detects_60px_face_on_3mp_real_image():
    """(a) Khuon mat rong 60 px tren khung 3MP duoc phat hien o nhanh mot tang (1280px)."""
    if not SAMPLE_IMG.exists() or not YUNET_MODEL.exists():
        pytest.skip("Thieu model YuNet hoac anh mau data/enroll/NV002/00_thang-giua.jpg")

    cfg = load_config()
    cfg["pipeline"]["two_stage_roi"] = False
    eng = OpenCVEngine(cfg["_paths"]["models"], det_score=float(cfg["pipeline"]["det_score"]), cfg=cfg)
    frame = _make_60px_face_3mp_frame()

    pipe = object.__new__(Pipeline)
    pipe.cfg = cfg
    pipe.pcfg = cfg["pipeline"]
    pipe.engine = eng
    pipe.ctx_det = None
    pipe.engine_lock = __import__("threading").Lock()
    pipe._cycle_index = 0
    pipe._last_scene = None

    faces = pipe._detect(frame)
    assert len(faces) >= 1, "Nhanh mot tang bo sot khuon mat 60px tren khung 3MP"
    assert 48.0 <= faces[0].w <= 75.0


def test_pipeline_and_worker_use_same_single_stage_detection_width():
    """(b) Pipeline._detect va nhanh mot tang cua PerceptionWorker nhan CUNG chieu rong (1280px) cho khung 3MP."""
    cfg = load_config()
    cfg["pipeline"]["two_stage_roi"] = False
    cfg["pipeline"]["fallback_det_width"] = 1280
    frame_3mp = np.zeros((1296, 2304, 3), dtype=np.uint8)

    pipe_widths: list[int] = []
    worker_widths: list[int] = []

    class _SpyEngine:
        model_version = "spy"

        def __init__(self, sink):
            self._sink = sink

        def detect(self, img):
            self._sink.append(img.shape[1])
            return []

        def embed_batch(self, aligned_list):
            return []

    pipe = object.__new__(Pipeline)
    pipe.cfg = cfg
    pipe.pcfg = cfg["pipeline"]
    pipe.engine = _SpyEngine(pipe_widths)
    pipe.ctx_det = None
    pipe.engine_lock = __import__("threading").Lock()
    pipe._cycle_index = 0
    pipe._last_scene = None
    pipe._detect(frame_3mp)

    worker = object.__new__(PerceptionWorker)
    worker.cfg = cfg
    worker.engine = _SpyEngine(worker_widths)
    worker.ctx_det = None
    worker.liveness = None
    worker.pose = SimpleNamespace(estimate=lambda *a, **k: None)
    worker.tracker = __import__("server.vision.tracker", fromlist=["IoUTracker"]).IoUTracker()
    worker.llr_cfg = __import__("server.vision.decision", fromlist=["LLRConfig"]).LLRConfig.from_cfg(cfg)
    worker.camera_id = "gate-1"
    worker.seq = 0
    worker._cycle_index = 0
    worker._chime_events = []
    worker.mode = "attendance"
    worker.process_frame(frame_3mp, ts_ns=1_000_000_000)

    assert pipe_widths == [1280]
    assert worker_widths == [1280]
    assert pipe_widths == worker_widths


def test_single_stage_never_upscales_small_camera_frame():
    """(c) Chieu rong phat hien khong vuot chieu rong khung hinh goc (khong phong to camera 720p/640p)."""
    cfg = load_config()
    cfg["pipeline"]["two_stage_roi"] = False
    cfg["pipeline"]["fallback_det_width"] = 1280
    frame_640 = np.zeros((480, 640, 3), dtype=np.uint8)
    seen: list[int] = []

    class _SpyEngine:
        def detect(self, img):
            seen.append(img.shape[1])
            return []

    pipe = object.__new__(Pipeline)
    pipe.cfg = cfg
    pipe.pcfg = cfg["pipeline"]
    pipe.engine = _SpyEngine()
    pipe.ctx_det = None
    pipe.engine_lock = __import__("threading").Lock()
    pipe._cycle_index = 0
    pipe._last_scene = None
    pipe._detect(frame_640)

    assert seen == [640]


def test_three_flows_attendance_video_guided_use_identical_width():
    """(d) Ca 3 luong cham cong, dang ky video, dang ky co huong dan dung CUNG nguong chieu rong."""
    cfg = load_config()
    cfg["pipeline"]["two_stage_roi"] = False
    cfg["pipeline"]["fallback_det_width"] = 1280
    frame_3mp = np.zeros((1296, 2304, 3), dtype=np.uint8)

    w_pipe: list[int] = []
    w_video: list[int] = []
    w_guided: list[int] = []

    class _SpyEngine:
        model_version = "spy"

        def __init__(self, sink):
            self._sink = sink

        def detect(self, img):
            self._sink.append(img.shape[1])
            return []

    # 1. Pipeline._detect
    pipe = object.__new__(Pipeline)
    pipe.cfg = cfg
    pipe.pcfg = cfg["pipeline"]
    pipe.engine = _SpyEngine(w_pipe)
    pipe.ctx_det = None
    pipe.engine_lock = __import__("threading").Lock()
    pipe._cycle_index = 0
    pipe._last_scene = None
    pipe._detect(frame_3mp)

    # 2. Enrollment video
    ctx_v = SimpleNamespace(cfg=cfg, db=None, engine=_SpyEngine(w_video), pose=None)
    with pytest.raises(Exception):
        # Khong co mat -> EnrollError, nhung da goi engine.detect tren anh da resize
        process_enrollment([frame_3mp], "NV999", "Test", ctx_v)

    # 3. Enrollment guided
    ctx_g = SimpleNamespace(cfg=cfg, db=None, engine=_SpyEngine(w_guided), pose=None)
    sess = GuidedSession(ctx_g, "NV999", "Test")
    sess._analyse(frame_3mp)

    assert w_pipe == [1280]
    assert w_video == [1280]
    assert w_guided == [1280]


def test_context_detector_receives_original_frame_single_resize():
    """T030 (US5): ContextDetector.detect nhan khung hinh goc (khong thu nho 640px truoc)."""
    frame_3mp = np.zeros((1296, 2304, 3), dtype=np.uint8)
    seen_ctx_shapes: list[tuple[int, int]] = []

    class _SpyCtx:
        def detect(self, img):
            seen_ctx_shapes.append(img.shape[:2])
            return ContextScene(persons=[], mediums=[])

    class _DummyEng:
        def detect(self, img):
            return []

    detect_faces_two_stage_3mp(
        frame_3mp, _DummyEng(), _SpyCtx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=False,
    )
    assert seen_ctx_shapes == [(1296, 2304)]


def test_context_detector_single_vs_double_resize_boxes_within_1px():
    """T030 (US5): Tren khung 3MP that, thu nho truc tiep 1 lan cho hop nguoi lech <= 1px so voi 2 lan."""
    if not SAMPLE_IMG.exists() or not YOLOX_MODEL.exists():
        pytest.skip("Thieu model YOLOX hoac anh mau")

    cfg = load_config()
    ctx_det = ContextDetector(cfg["_paths"]["models"], cfg=cfg)
    sample = cv2.imread(str(SAMPLE_IMG))
    assert sample is not None
    frame = np.full((1296, 2304, 3), 120, dtype=np.uint8)
    patch = cv2.resize(sample, (500, 500))
    frame[200:700, 900:1400] = patch

    # Duong moi: 1 lan thu nho truc tiep trong ContextDetector.detect
    scene_direct = ctx_det.detect(frame)

    # Duong cu: thu nho ve 640px truoc roi moi goi ContextDetector.detect + chia nguoc
    h, w = frame.shape[:2]
    s_ctx = min(1.0, 640.0 / max(w, 1))
    small = cv2.resize(frame, (int(w * s_ctx), int(h * s_ctx)))
    scene_double = ctx_det.detect(small)
    double_boxes = [(pb / s_ctx).astype(np.float32) for pb in scene_double.persons]

    # Kiem tra hinh hoc tuong duong tuyet doi va sai lech hop nguoi nho (trung binh <= 1px, IoU >= 0.99)
    assert abs(2304.0 * (640.0 / 2304.0) * (416.0 / 640.0) - 2304.0 * (416.0 / 2304.0)) < 1e-6
    assert len(scene_direct.persons) == len(double_boxes)
    from server.vision.tracker import iou
    for b1, b2 in zip(scene_direct.persons, double_boxes):
        assert float(np.mean(np.abs(b1 - b2))) <= 1.0
        assert iou(b1, b2) >= 0.99
