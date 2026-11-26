"""Kiem thu hoi quy cho kiem tra hop le 5 diem moc (R11, FR-012) va gioi han luong (FR-014)."""
from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from server.config import _apply_v4_defaults, load_config
from server.vision.decision import LLRConfig
from server.vision.engines import Face, OpenCVEngine, landmarks_valid
from server.vision.pipeline import Pipeline
from server.vision.tracker import IoUTracker, Track
from server.vision.worker_perception import PerceptionWorker

ROOT = Path(__file__).resolve().parents[2]
YUNET_MODEL = ROOT / "server" / "models" / "face_detection_yunet_2023mar.onnx"


def _valid_kps(x1=100.0, y1=100.0, x2=200.0, y2=220.0) -> np.ndarray:
    w, h = x2 - x1, y2 - y1
    return np.array([
        [x1 + 0.30 * w, y1 + 0.35 * h],
        [x1 + 0.70 * w, y1 + 0.35 * h],
        [x1 + 0.50 * w, y1 + 0.55 * h],
        [x1 + 0.35 * w, y1 + 0.75 * h],
        [x1 + 0.65 * w, y1 + 0.75 * h],
    ], dtype=np.float32)


def test_landmarks_valid_all_geometric_rules():
    bbox = np.array([100.0, 100.0, 200.0, 220.0], dtype=np.float32)
    kps = _valid_kps()
    assert landmarks_valid(bbox, kps) is True

    # Khong huu han
    k_inf = kps.copy()
    k_inf[0, 1] = np.inf
    assert landmarks_valid(bbox, k_inf) is False

    # Hop rong/am
    assert landmarks_valid(np.array([100.0, 100.0, 100.0, 200.0], dtype=np.float32), kps) is False

    # Diem ngoai hop mo rong 25% (bw=100 -> x > 225 bi loai)
    k_out = kps.copy()
    k_out[1, 0] = 226.0
    assert landmarks_valid(bbox, k_out) is False

    # Mat nam ngang hoac duoi mieng
    k_inv = kps.copy()
    k_inv[0, 1] = k_inv[1, 1] = k_inv[3, 1]
    assert landmarks_valid(bbox, k_inv) is False

    # Khoang cach hai mat < 25% bw
    k_narrow = kps.copy()
    k_narrow[0, 0], k_narrow[1, 0] = 140.0, 160.0  # 20 < 25
    assert landmarks_valid(bbox, k_narrow) is False


def test_pipeline_update_track_skips_pnp_align_embed_on_invalid_landmarks():
    """T007: Pipeline._update_track bo qua pose/align/embed khi landmarks_valid=False, gan last_reason."""
    calls = {"pose": 0, "align": 0, "embed": 0}

    class _SpyPose:
        def estimate(self, *a, **k):
            calls["pose"] += 1
            raise AssertionError("Khong duoc goi pose.estimate khi landmarks_valid=False")

    class _SpyEngine:
        def align(self, *a, **k):
            calls["align"] += 1
            raise AssertionError("Khong duoc goi engine.align khi landmarks_valid=False")

        def embed_aligned(self, *a, **k):
            calls["embed"] += 1
            raise AssertionError("Khong duoc goi engine.embed_aligned khi landmarks_valid=False")

    pipe = object.__new__(Pipeline)
    pipe.pose = _SpyPose()
    pipe.engine = _SpyEngine()
    pipe.viewport_grace = 0.80
    pipe.unattached_confirm_frames = 4
    pipe.qcfg = {}

    frame = np.full((480, 640, 3), 120, dtype=np.uint8)
    bbox = np.array([100.0, 100.0, 200.0, 220.0], dtype=np.float32)
    bad_face = Face(bbox, 0.9, _valid_kps(), landmarks_valid=False)
    track = Track(id=1, bbox=bbox, first_seen=1.0, last_seen=1.0)

    pipe._update_track(track, bad_face, frame, now=1.5, scene=None)
    assert track.last_reason == "landmarks_invalid"
    assert calls == {"pose": 0, "align": 0, "embed": 0}
    assert track.best_crop is not None


def test_worker_process_frame_skips_pnp_align_embed_but_keeps_track_on_invalid_landmarks():
    """T008: PerceptionWorker.process_frame bo qua pose/align/embed khi landmarks_valid=False, van theo doi track."""
    cfg = load_config()
    cfg["pipeline"]["two_stage_roi"] = False
    calls = {"pose": 0, "align": 0, "embed": 0}
    bbox = np.array([100.0, 100.0, 200.0, 220.0], dtype=np.float32)
    bad_face = Face(bbox, 0.9, _valid_kps(), landmarks_valid=False)

    class _SpyPose:
        def estimate(self, *a, **k):
            calls["pose"] += 1
            raise AssertionError("Khong duoc goi pose.estimate khi landmarks_valid=False")

    class _SpyEngine:
        def detect(self, img):
            return [bad_face]

        def align(self, *a, **k):
            calls["align"] += 1
            raise AssertionError("Khong duoc goi engine.align khi landmarks_valid=False")

        def embed_batch(self, aligned_list):
            calls["embed"] += len(aligned_list)
            return []

    worker = object.__new__(PerceptionWorker)
    worker.cfg = cfg
    worker.engine = _SpyEngine()
    worker.ctx_det = None
    worker.liveness = None
    worker.pose = _SpyPose()
    worker.tracker = IoUTracker()
    worker.llr_cfg = LLRConfig.from_cfg(cfg)
    worker.viewport_grace = 0.80
    worker.unattached_confirm_frames = 4
    worker.camera_id = "gate-1"
    worker.seq = 0
    worker._cycle_index = 0
    worker._chime_events = []
    worker.mode = "attendance"

    frame = np.full((480, 640, 3), 120, dtype=np.uint8)
    pkt, evs = worker.process_frame(frame, ts_ns=1_000_000_000)

    assert len(pkt["objects"]) == 1
    assert len(worker.tracker.tracks) == 1
    t = next(iter(worker.tracker.tracks.values()))
    assert t.last_reason == "landmarks_invalid"
    assert calls == {"pose": 0, "align": 0, "embed": 0}
    assert evs == []


def test_opencv_engine_padding_buckets_and_threads():
    """T004 & T009: OpenCVEngine dem len moc roi_det_buckets khong noi suy va dat cv2.setNumThreads."""
    if not YUNET_MODEL.exists():
        pytest.skip("Thieu model YuNet")
    cfg = load_config()
    cfg["pipeline"]["ort_threads"] = 2
    eng = OpenCVEngine(cfg["_paths"]["models"], det_score=0.55, cfg=cfg)
    assert cv2.getNumThreads() == 2

    # Anh 350x350 -> dem len moc 480x480
    img350 = np.full((350, 350, 3), 114, dtype=np.uint8)
    eng.detect(img350)
    assert eng._size == (480, 480)

    # Anh 400x400 -> cung moc 480x480 (khong doi input size)
    img400 = np.full((400, 400, 3), 114, dtype=np.uint8)
    eng.detect(img400)
    assert eng._size == (480, 480)

    # Anh 1280x720 (>= 640) -> giu nguyen ban, khong dem
    img1280 = np.full((720, 1280, 3), 114, dtype=np.uint8)
    eng.detect(img1280)
    assert eng._size == (1280, 720)


def test_config_backcompat_and_invalid_clamping_with_warnings(caplog):
    """FR-015: cau hinh cu khong co khoa moi khoi dong duoc; cau hinh sai bi kep + log warning."""
    # Cau hinh cu (khong co 9 khoa moi)
    old_cfg = _apply_v4_defaults({"pipeline": {"backend": "opencv"}})
    assert old_cfg["pipeline"]["max_roi_det_side"] == 640
    assert old_cfg["pipeline"]["roi_det_buckets"] == [320, 480, 640]
    assert old_cfg["pipeline"]["fallback_every_n_cycles"] == 4
    assert old_cfg["pipeline"]["det_nms"] == 0.40
    assert old_cfg["pipeline"]["ort_threads"] == 2

    # Cau hinh sai gia tri -> kep + warning, khong nem ngoai le
    with caplog.at_level(logging.WARNING, logger="config"):
        bad_cfg = _apply_v4_defaults({
            "pipeline": {
                "roi_det_buckets": [],
                "fallback_every_n_cycles": 999,
            }
        })
    assert bad_cfg["pipeline"]["roi_det_buckets"] == [320, 480, 640]
    assert bad_cfg["pipeline"]["fallback_every_n_cycles"] == 30
    assert any("roi_det_buckets" in r.message for r in caplog.records)
    assert any("fallback_every_n_cycles" in r.message for r in caplog.records)


def test_invalid_landmarks_do_not_trigger_unattached_face_spoof():
    """Hoi quy bug ceiling-sprinkler-false-spoof: landmarks_valid=False khong duoc tang unattached_streak hay khoa spoof."""
    from server.vision.context_detector import ContextScene

    bbox = np.array([100.0, 60.0, 200.0, 180.0], dtype=np.float32)
    bad_face = Face(bbox, 0.92, _valid_kps(100.0, 60.0, 200.0, 180.0), landmarks_valid=False)
    frame = np.full((480, 640, 3), 120, dtype=np.uint8)
    empty_scene = ContextScene(persons=[], mediums=[])

    # 1. Pipeline._update_track
    pipe = object.__new__(Pipeline)
    pipe.pose = None
    pipe.engine = None
    pipe.viewport_grace = 0.80
    pipe.unattached_confirm_frames = 4
    pipe.qcfg = {"min_face": 60}
    track = Track(id=1, bbox=bbox, first_seen=1.0, last_seen=1.0)
    for k in range(6):
        pipe._update_track(track, bad_face, frame, now=1.0 + k * 0.25, scene=empty_scene)
    assert track.unattached_streak == 0
    assert track.spoof is False
    assert track.spoof_reason is None

    # 2. PerceptionWorker.process_frame
    cfg = load_config()
    cfg["pipeline"]["two_stage_roi"] = False
    cfg["tracking"]["unattached_confirm_frames"] = 4

    class _Eng:
        def detect(self, img):
            return [bad_face]

        def embed_batch(self, aligned_list):
            return []

    class _Ctx:
        def detect(self, img):
            return empty_scene

    worker = object.__new__(PerceptionWorker)
    worker.cfg = cfg
    worker.engine = _Eng()
    worker.ctx_det = _Ctx()
    worker.liveness = None
    worker.pose = None
    worker.tracker = IoUTracker()
    worker.llr_cfg = LLRConfig.from_cfg(cfg)
    worker.viewport_grace = 0.80
    worker.unattached_confirm_frames = 4
    worker.camera_id = "gate-1"
    worker.seq = 0
    worker._cycle_index = 0
    worker._chime_events = []
    worker.mode = "attendance"

    all_evs = []
    for k in range(6):
        _, evs = worker.process_frame(frame, ts_ns=int((1.0 + k * 0.25) * 1e9))
        all_evs.extend(evs)
    t = next(iter(worker.tracker.tracks.values()))
    assert t.unattached_streak == 0
    assert t.spoof is False
    assert t.spoof_reason is None
    assert not any(e.get("event_kind") == "spoof" for e in all_evs)


def test_pipeline_emits_screen_detected_spoof_event_even_when_landmarks_invalid(tmp_path):
    """Hoi quy: Pipeline._update_track van phat su kien spoof + chuong khi screen_detected xay ra tren frame landmarks_valid=False."""
    from server.enrollment.gallery import Gallery
    from server.store.db import DB
    from server.vision.context_detector import ContextScene

    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "enroll": tmp_path / "enroll", "snapshots": tmp_path / "snap"}
    cfg["pipeline"]["two_stage_roi"] = True
    db = DB(tmp_path / "screen_inv.db")
    gal = Gallery(db, "dummy_screen")
    broadcasted = []

    bbox = np.array([200.0, 80.0, 320.0, 220.0], dtype=np.float32)
    bad_face = Face(bbox, 0.92, _valid_kps(200.0, 80.0, 320.0, 220.0), landmarks_valid=False)
    frame = np.full((480, 640, 3), 120, dtype=np.uint8)

    class _Cam:
        def read(self):
            return 0, None

    class _HRM:
        def notify(self):
            pass

    class _Eng:
        name = "opencv"
        model_version = "dummy_screen"

        def detect(self, img):
            return [bad_face]

    class _Ctx:
        def detect(self, img):
            return ContextScene(
                persons=[],
                mediums=[(np.array([180.0, 60.0, 340.0, 240.0], dtype=np.float32), "cell phone", 0.9)],
            )

    pl = Pipeline(
        cfg, db, _Eng(), gal, _Cam(), _HRM(),
        broadcast=broadcasted.append, liveness=None, clips=None,
        recognize=True, pose=None, context_detector=_Ctx(),
    )
    for k in range(3):
        pl._process(frame, now=1.0 + k * 0.25)

    evs = db.list_events(10)
    assert len(evs) == 1
    assert evs[0]["kind"] == "spoof"
    pl._end_all_tracks()
    apps = db.list_appearances(0.0, 9999999999.0, kind="spoof")
    assert len(apps) == 1
    assert apps[0]["spoof_reason"] == "screen_detected"
    assert apps[0]["event_id"] == evs[0]["id"]
