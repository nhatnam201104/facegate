"""Kiểm thử ước lượng tư thế 3D (3DDFA_V2), phân nhóm góc có hysteresis và sai số homography phẳng."""
import math
from pathlib import Path

import numpy as np

from app.engines import Face
from app.pose3d import (
    POSE_GROUPS,
    PoseEstimator3D,
    classify_pose_group,
    euler_from_relative_rotation,
    planar_parallax_score,
)

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def _rot_y(deg: float) -> np.ndarray:
    a = math.radians(deg)
    return np.array([[math.cos(a), 0.0, math.sin(a)], [0.0, 1.0, 0.0], [-math.sin(a), 0.0, math.cos(a)]], np.float32)


def _rot_x(deg: float) -> np.ndarray:
    a = math.radians(deg)
    return np.array([[1.0, 0.0, 0.0], [0.0, math.cos(a), -math.sin(a)], [0.0, math.sin(a), math.cos(a)]], np.float32)


def test_euler_angles_cover_full_90_degree_range():
    for target_yaw in (-88.0, -60.0, -25.0, 0.0, 30.0, 75.0, 89.0):
        yaw, pitch, roll = euler_from_relative_rotation(_rot_y(target_yaw))
        assert abs(yaw - target_yaw) < 1.0
        assert abs(pitch) < 1.0
        assert abs(roll) < 1.0

    _, pitch_down, _ = euler_from_relative_rotation(_rot_x(22.0))
    assert abs(pitch_down - 22.0) < 1.0


def test_euler_pitch_roll_stability_under_camera_tilt_and_large_yaw():
    import pytest

    for tilt in (0.0, 8.0, 15.0):
        for target_yaw in (0.0, 45.0, 70.0, 85.0, 88.0):
            for sign in (-1.0, 1.0):
                y = sign * target_yaw
                for R in (_rot_x(tilt) @ _rot_y(y), _rot_y(y) @ _rot_x(tilt)):
                    yaw, pitch, roll = euler_from_relative_rotation(R)
                    assert abs(yaw - y) <= 1.5, f"tilt={tilt}, y={y} -> yaw={yaw}"
                    assert max(abs(pitch), abs(roll)) <= tilt + 1.0, (
                        f"tilt={tilt}, y={y} -> pitch={pitch:.2f}, roll={roll:.2f}"
                    )


def test_pose_group_hysteresis_around_40_degrees():
    assert set(POSE_GROUPS) == {"frontal", "left_profile", "right_profile"}
    assert classify_pose_group(0.0) == "frontal"
    assert classify_pose_group(-39.5) == "frontal"
    assert classify_pose_group(-41.0) == "left_profile"
    assert classify_pose_group(+41.0) == "right_profile"

    # Hysteresis: đang ở frontal thì giữ đến 45° mới nhảy sang profile
    assert classify_pose_group(-43.0, prev_group="frontal", frontal_max=40.0, hysteresis=5.0) == "frontal"
    assert classify_pose_group(-46.0, prev_group="frontal", frontal_max=40.0, hysteresis=5.0) == "left_profile"

    # Đang ở left_profile thì phải quay về dưới 35° mới về lại frontal
    assert classify_pose_group(-37.0, prev_group="left_profile", frontal_max=40.0, hysteresis=5.0) == "left_profile"
    assert classify_pose_group(-34.0, prev_group="left_profile", frontal_max=40.0, hysteresis=5.0) == "frontal"


def test_planar_vs_3d_parallax_discrimination():
    rng = np.random.default_rng(42)
    # Khối mặt 3D thật có chiều sâu z khác biệt rõ (mũi nhô cao hơn tai/má)
    pts3d = rng.uniform(-40.0, 40.0, size=(68, 3)).astype(np.float32)
    pts3d[:17, 2] = -35.0   # viền hàm/tai ở sâu
    pts3d[27:36, 2] = 28.0  # sống mũi nhô cao

    # Quỹ đạo 1: Người thật 3D bước ngang và xoay nhẹ theo góc nhìn phối cảnh
    real_seq = []
    for i in range(7):
        ang = -72.0 + i * 3.5
        R = _rot_y(ang)
        p = (R @ pts3d.T).T
        # Chiếu phối cảnh thực tế với z camera = 400
        z_cam = 400.0 - p[:, 2]
        x2d = 200.0 + i * 24.0 + (p[:, 0] * 380.0 / z_cam)
        y2d = 180.0 + (p[:, 1] * 380.0 / z_cam)
        real_seq.append(np.stack([x2d, y2d, p[:, 2]], axis=-1).astype(np.float32))

    # Quỹ đạo 2: Ảnh phẳng 2D trên điện thoại cầm quét ngang qua camera (mọi điểm z=0 phẳng cứng)
    flat_base = real_seq[0][:, :2].copy()
    planar_seq = []
    for i in range(7):
        theta = math.radians(i * 1.2)
        A = np.array([[math.cos(theta) * 1.01, -math.sin(theta)], [math.sin(theta), math.cos(theta)]], np.float32)
        xy = (flat_base - flat_base.mean(0)) @ A.T + flat_base.mean(0) + np.array([i * 24.0, i * 1.5], np.float32)
        z_fake = np.zeros((68, 1), np.float32)
        planar_seq.append(np.hstack([xy, z_fake]))

    score_real = planar_parallax_score(real_seq)
    score_flat = planar_parallax_score(planar_seq)
    assert score_real >= 0.55, f"Người thật 3D phải đạt điểm parallax cao, nhận: {score_real}"
    assert score_flat < 0.35, f"Ảnh phẳng 2D phải bị phát hiện thiếu thị sai 3D, nhận: {score_flat}"


def test_real_3ddfa_onnx_inference_shapes():
    est = PoseEstimator3D(MODELS_DIR)
    img = np.full((360, 480, 3), 140, dtype=np.uint8)
    face = Face(
        bbox=np.array([160.0, 80.0, 300.0, 250.0], np.float32),
        score=0.92,
        kps=np.array([[195, 135], [265, 135], [230, 175], [205, 210], [255, 210]], np.float32),
    )
    res = est.estimate(img, face)
    assert res.landmarks3d.shape == (68, 3)
    assert res.kps5.shape == (5, 2)
    assert res.pose_group in POSE_GROUPS
    assert -100.0 <= res.yaw <= 100.0


def test_three_pose_alignment_templates_and_edgeface_engine():
    from app.engines import (
        EdgeFaceEngine,
        TEMPLATE_FRONTAL,
        TEMPLATE_LEFT_PROFILE,
        TEMPLATE_RIGHT_PROFILE,
        align_by_pose,
    )

    assert TEMPLATE_FRONTAL.shape == (5, 2)
    assert TEMPLATE_LEFT_PROFILE.shape == (5, 2)
    assert TEMPLATE_RIGHT_PROFILE.shape == (5, 2)
    # Left profile dồn điểm mũi sang rìa trái của khung 112x112 để dành trọn vùng trung tâm cho má phải nhìn thấy
    assert TEMPLATE_LEFT_PROFILE[2, 0] < TEMPLATE_FRONTAL[2, 0] < TEMPLATE_RIGHT_PROFILE[2, 0]

    est = PoseEstimator3D(MODELS_DIR)
    eng = EdgeFaceEngine(MODELS_DIR, det_score=0.55)
    rng = np.random.default_rng(7)
    img = rng.integers(40, 210, size=(360, 480, 3), dtype=np.uint8)
    face = Face(
        bbox=np.array([150.0, 70.0, 310.0, 260.0], np.float32),
        score=0.95,
        kps=np.array([[190, 130], [270, 130], [230, 175], [200, 215], [260, 215]], np.float32),
    )
    pose = est.estimate(img, face)
    for grp in ("frontal", "left_profile", "right_profile"):
        aligned = align_by_pose(img, face, pose=pose, pose_group=grp)
        assert aligned.shape == (112, 112, 3)
        emb = eng.embed_aligned(aligned)
        assert emb.shape == (512,)
        assert abs(float(np.linalg.norm(emb)) - 1.0) < 1e-4
