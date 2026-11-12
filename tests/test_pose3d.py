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


def test_planar_homography_with_nonzero_3ddfa_z_still_scores_below_spoof_threshold():
    import cv2
    from app.config import load_config
    from app.liveness import create_liveness
    from app.tracker import Track

    cfg = load_config()
    min_depth = float(cfg["liveness"]["parallax"]["min_depth_score"])
    spoof_cut = min_depth * 0.65

    rng = np.random.default_rng(77)
    # Mô phỏng 3DDFA luôn tái dựng z != 0 (sống mũi cao 28, viền hàm -35) ngay cả trên ảnh in phẳng
    pts3d = rng.uniform(-40.0, 40.0, size=(68, 3)).astype(np.float32)
    pts3d[:17, 2] = -35.0
    pts3d[27:36, 2] = 28.0
    base_2d = (pts3d[:, :2] + np.array([220.0, 180.0], np.float32)).astype(np.float32)

    # Chuỗi 2D biến đổi thuần túy bằng phép chiếu homography phẳng (ảnh in nghiêng/quét ngang trước ống kính), kèm z != 0 của 3DDFA
    src_quad = np.array([[180, 140], [260, 140], [260, 220], [180, 220]], np.float32)
    homography_seq = []
    for i in range(7):
        dx = i * 22.0
        dst_quad = np.array([
            [180 + dx, 140 + i * 0.8],
            [260 + dx - i * 1.5, 142 + i * 1.2],
            [260 + dx - i * 1.2, 222 - i * 0.9],
            [180 + dx + i * 0.5, 220 + i * 0.6],
        ], np.float32)
        H = cv2.getPerspectiveTransform(src_quad, dst_quad)
        warped_xy = cv2.perspectiveTransform(base_2d[None, :, :], H)[0]
        # Thêm dao động sub-pixel nhỏ (~0.15 px) của detector thực tế trên ảnh phẳng
        jitter = rng.normal(0.0, 0.12, size=warped_xy.shape).astype(np.float32)
        homography_seq.append(np.column_stack([warped_xy + jitter, pts3d[:, 2].copy()]).astype(np.float32))

    score_planar_with_z = planar_parallax_score(homography_seq)
    assert score_planar_with_z < spoof_cut, (
        f"Ảnh phẳng biến đổi homography dù có z!=0 từ 3DDFA vẫn phải < {spoof_cut:.3f}, nhận {score_planar_with_z}"
    )

    # Nhánh dự phòng 5 điểm (không có model 3DDFA ONNX) không có dữ liệu thị sai 68 điểm thật -> Nhánh B phải trả 'checking', không duyệt 'live'
    est_fallback = PoseEstimator3D(models_dir=None)
    liv = create_liveness(cfg)
    tr_fb = Track(99, np.array([120.0, 90.0, 200.0, 190.0], np.float32), 50.0, 50.0)
    tr_fb.matched_group = "left_profile"
    blank = np.full((360, 640, 3), 130, dtype=np.uint8)
    for i in range(7):
        cx = 140.0 + i * 20.0
        fb = np.array([cx - 40, 90.0, cx + 40, 190.0], np.float32)
        kps = np.array([[cx - 18, 125], [cx + 18, 125], [cx - 32, 148], [cx - 14, 168], [cx + 12, 168]], np.float32)
        f = Face(fb, 0.95, kps)
        p_res = est_fallback.estimate(blank, f)
        tr_fb.bbox = fb
        tr_fb.pose_history.append((50.0 + i * 0.2, p_res.yaw, p_res.pitch, p_res.roll, (cx, 140.0), p_res.landmarks3d))
        tr_fb.context_history.append({
            "yaw": p_res.yaw,
            "medium_clean": True,
            "body_coupled": True,
            "spoof_reason": None,
            "moire_score": 0.05,
        })
    assert liv.evaluate_track(tr_fb, frame_shape=blank.shape) == "checking"


def test_pure_translation_under_3ddfa_orthographic_projection_stays_checking_not_permanent_spoof():
    from app.config import load_config
    from app.liveness import create_liveness
    from app.tracker import Track

    cfg = load_config()
    liv = create_liveness(cfg)
    u_base = np.fromfile(MODELS_DIR / "tddfa_u_base.bin", np.float32).reshape(3, 68, order="F").T
    u_centered = u_base - u_base.mean(axis=0)
    u_centered *= 85.0 / float(u_centered[:, 1].max() - u_centered[:, 1].min())

    # Người thật đi ngang qua khung hình nhưng giữ góc đầu ổn định (Δyaw = 0.4° < 4° trên đầu ra trực giao cục bộ của 3DDFA, không có màn hình/Moiré)
    tr = Track(105, np.array([120.0, 90.0, 200.0, 190.0], np.float32), 10.0, 10.0)
    tr.matched_group = "left_profile"
    for i in range(6):
        yaw_i = -80.0 + i * 0.08
        p = (_rot_y(yaw_i) @ u_centered.T).T
        cx = 150.0 + i * 20.0
        p[:, 0] += cx
        p[:, 1] += 160.0
        tr.bbox = np.array([cx - 40, 110.0, cx + 40, 210.0], np.float32)
        tr.pose_history.append((10.0 + i * 0.2, yaw_i, 0.0, 0.0, (cx, 160.0), p.astype(np.float32)))
        tr.context_history.append({
            "yaw": yaw_i,
            "medium_clean": True,
            "medium_nearby": False,
            "body_coupled": True,
            "spoof_reason": None,
            "moire_score": 0.04,
        })

    st = liv.evaluate_track(tr, frame_shape=(480, 640, 3))
    assert st == "checking" and not tr.spoof, f"Người thật đi thẳng Δyaw nhỏ không được bị khóa spoof vĩnh viễn: {st}, {tr.spoof_reason}"


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
