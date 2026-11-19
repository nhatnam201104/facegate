"""Cong cu do full pipeline (feature 002, T041) - chay voi stub, khong can model that."""
import cv2
import numpy as np
import pytest

from server.config import load_config
from server.scripts.benchmark import STAGES, _paste_faces, run_full_pipeline
from server.vision.engines import Face
from server.vision.pose3d import PoseResult


class StubEngine:
    name = "stub"
    model_version = "stub_v1"

    def __init__(self, n_faces=2):
        self.n_faces = n_faces

    def detect(self, img):
        out = []
        for i in range(self.n_faces):
            x = 20 + i * 70
            kps = np.array([[x + 15, 40], [x + 45, 40], [x + 30, 55], [x + 18, 70], [x + 42, 70]], np.float32)
            out.append(Face(np.array([x, 20, x + 60, 90], np.float32), 0.95, kps))
        return out

    def align(self, img, face, pose=None, pose_group=None):
        return np.zeros((112, 112, 3), np.uint8)

    def embed_batch(self, aligned):
        return [np.ones(128, np.float32) / np.sqrt(128) for _ in aligned]


class StubPose:
    def estimate(self, img, face, prev_group=None):
        return PoseResult(5.0, 0.0, 0.0, "frontal", np.zeros((68, 3), np.float32), face.kps, 1.0)


class StubLiveness:
    def __init__(self):
        self.calls = 0

    def real_prob(self, img, bbox):
        self.calls += 1
        return 0.9


class StubCtx:
    def __init__(self):
        self.calls = 0

    def detect(self, img):
        self.calls += 1
        return None


def _video(tmp_path, n=5, w=320, h=240):
    p = tmp_path / "v.avi"
    vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"MJPG"), 10, (w, h))
    rng = np.random.default_rng(0)
    for _ in range(n):
        vw.write(rng.integers(60, 200, (h, w, 3), dtype=np.uint8))
    vw.release()
    return p


@pytest.fixture
def cfg():
    c = load_config()
    c["pipeline"]["two_stage_roi"] = False
    c["quality"]["min_face"] = 10
    c["quality"]["min_blur"] = 0
    c["quality"]["min_brightness"] = 0
    c["quality"]["max_brightness"] = 255
    return c


def test_full_pipeline_reports_all_stages(tmp_path, cfg):
    liv, ctx = StubLiveness(), StubCtx()
    r = run_full_pipeline(cfg, str(_video(tmp_path)), engine=StubEngine(2), pose_est=StubPose(),
                          ctx_det=ctx, liveness=liv)
    for k in ("backend", "model_version", "ep", "video", "n_frames", "cycle_ms", "per_stage_ms",
              "faces_per_frame", "simulated"):
        assert k in r
    assert set(r["per_stage_ms"]) == set(STAGES) and len(STAGES) == 8
    assert r["n_frames"] == 5 and r["faces_per_frame"]["max"] == 2
    assert r["cycle_ms"]["mean"] is not None and r["cycle_ms"]["mean"] >= 0
    assert r["simulated"] is False
    assert ctx.calls == 5


def test_min_faces_filters_frames(tmp_path, cfg):
    r = run_full_pipeline(cfg, str(_video(tmp_path)), engine=StubEngine(2), pose_est=StubPose(),
                          ctx_det=StubCtx(), liveness=StubLiveness(), min_faces=10)
    assert r["n_frames"] == 0 and r["cycle_ms"]["mean"] is None
    assert r["n_frames_read"] == 5


def test_simulate_faces_marks_result(tmp_path, cfg):
    r = run_full_pipeline(cfg, str(_video(tmp_path)), engine=StubEngine(1), pose_est=StubPose(),
                          ctx_det=StubCtx(), liveness=StubLiveness(), simulate_faces=4)
    assert r["simulated"] is True


def test_paste_faces_fills_requested_count():
    frame = np.zeros((240, 320, 3), np.uint8)
    out = _paste_faces(frame, np.full((50, 50, 3), 255, np.uint8), 4)
    assert out.shape == frame.shape and out.sum() > 0 and frame.sum() == 0


def test_missing_video_raises(tmp_path, cfg):
    with pytest.raises(FileNotFoundError):
        run_full_pipeline(cfg, str(tmp_path / "none.mp4"), engine=StubEngine(), pose_est=StubPose(),
                          ctx_det=StubCtx(), liveness=StubLiveness())
