"""Uoc luong tu the PnP 5 diem thay 3DDFA_V2 (feature 002, T010 + T020)."""
import cv2
import numpy as np
import pytest

from server.vision.engines import Face
from server.vision.pose3d import (
    FACE5_3D, PoseEstimator3D, camera_matrix, classify_pose_group, create_pose_estimator,
)

W, H = 1280, 720
IMG = np.zeros((H, W, 3), np.uint8)


def _face(yaw: float, pitch: float = 0.0, dist: float = 600.0) -> Face:
    """Chieu FACE5_3D voi tu the theo QUY UOC HE THONG (yaw > 0: mui lech sang phai-anh)."""
    ry, rx = np.deg2rad(-yaw), np.deg2rad(pitch)
    Ry = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]])
    rvec, _ = cv2.Rodrigues(Ry @ Rx)
    pts, _ = cv2.projectPoints(FACE5_3D, rvec, np.array([[0.0], [0.0], [dist]]), camera_matrix(W, H), None)
    pts = pts.reshape(5, 2).astype(np.float32)
    x1, y1 = pts.min(0) - 30
    x2, y2 = pts.max(0) + 30
    return Face(np.array([x1, y1, x2, y2], np.float32), 0.9, pts)


@pytest.mark.parametrize("yaw", [-60, -30, 0, 30, 60])
@pytest.mark.parametrize("pitch", [-20, 0, 20])
def test_pnp_recovers_pose(yaw, pitch):
    f = _face(yaw, pitch)
    r = PoseEstimator3D(models_dir=None).estimate(IMG, f)
    assert abs(r.yaw - yaw) <= 8.0
    assert abs(r.pitch - pitch) <= 10.0
    assert r.pose_group == classify_pose_group(r.yaw, None, 40.0, 5.0)
    np.testing.assert_allclose(r.kps5, f.kps)
    assert r.landmarks3d.shape == (68, 3)


def test_yaw_sign_matches_nose_offset():
    r = PoseEstimator3D().estimate(IMG, _face(30))
    le, re, nose = r.kps5[0], r.kps5[1], r.kps5[2]
    assert r.yaw > 0 and nose[0] > (le[0] + re[0]) / 2.0


def test_large_yaw_is_unreliable_and_low_visibility():
    r = PoseEstimator3D(max_reliable_yaw=60.0).estimate(IMG, _face(75))
    assert r.pose_reliable is False
    assert r.visibility <= 0.3
    assert PoseEstimator3D(max_reliable_yaw=60.0).estimate(IMG, _face(30)).pose_reliable is True


def test_degenerate_keypoints_fall_back_unreliable():
    kps = np.array([[100, 100], [100, 100], [100, 100], [100, 100], [100, 100]], np.float32)
    f = Face(np.array([80, 80, 120, 120], np.float32), 0.9, kps)
    r = PoseEstimator3D().estimate(IMG, f)
    assert r.pose_reliable is False
    assert np.isfinite(r.yaw) and np.isfinite(r.pitch)


def test_ema_smooths_by_seconds_not_frames():
    est = PoseEstimator3D(ema_tau_sec=0.3)
    est.estimate(IMG, _face(0), track_id=1, ts=10.0)
    r = est.estimate(IMG, _face(20), track_id=1, ts=10.1)
    assert 0.0 < r.yaw < 20.0
    # Cach nhau rat lau -> gan nhu bam theo gia tri moi
    r2 = est.estimate(IMG, _face(20), track_id=1, ts=20.0)
    assert abs(r2.yaw - 20.0) < 1.0
    # Khong truyen track_id -> khong lam muot
    assert abs(est.estimate(IMG, _face(20)).yaw - 20.0) < 1.0


def test_ema_is_per_track():
    est = PoseEstimator3D(ema_tau_sec=0.3)
    est.estimate(IMG, _face(0), track_id=1, ts=1.0)
    r = est.estimate(IMG, _face(30), track_id=2, ts=1.05)
    assert abs(r.yaw - 30.0) < 1.5


def test_create_pose_estimator_from_cfg():
    est = create_pose_estimator({"recognition": {"frontal_max_yaw": 35, "pose_hysteresis": 4},
                                 "pose": {"engine": "pnp5", "max_reliable_yaw": 55, "ema_tau_sec": 0.2}})
    assert est.frontal_max == 35 and est.max_reliable_yaw == 55 and est.ema_tau_sec == 0.2
    with pytest.raises(ValueError):
        create_pose_estimator({"pose": {"engine": "mediapipe"}})


def test_no_bfm_or_3ddfa_dependency():
    import server.vision.pose3d as m
    src = open(m.__file__, encoding="utf-8").read()
    assert "tddfa_" not in src and "InferenceSession" not in src
