"""Kiểm thử PoseGallery tách 3 ma trận tư thế, Pose-Bucketed Tracker giữ ID khi xoay đầu và ưu tiên Best-shot."""
import numpy as np

from app.db import DB
from app.engines import Face
from app.gallery import Gallery
from app.tracker import IoUTracker


def _unit(vec: list[float]) -> np.ndarray:
    v = np.array(vec, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def test_pose_gallery_separates_matrices_and_stricter_profile_thresholds(tmp_path):
    db = DB(tmp_path / "g.db")
    mv = "edgeface_test_v1"

    emp_a = db.create_employee("NV001", "Nguyễn Văn A")
    db.set_status(emp_a, "active")
    emp_b = db.create_employee("NV002", "Trần Thị B")
    db.set_status(emp_b, "active")

    # Vector chính diện và nghiêng sâu của cùng 1 người gần trực giao nhau
    a_front = _unit([1.0, 0.0, 0.0, 0.0])
    a_left = _unit([0.0, 1.0, 0.0, 0.0])
    b_front = _unit([0.0, 0.0, 1.0, 0.0])
    b_left = _unit([0.0, 0.85, 0.52, 0.0])  # khá gần a_left để test profile_margin

    db.add_embedding(emp_a, a_front, mv, 0.9, "thang-giua", None, pose_group="frontal", yaw=0.0)
    db.add_embedding(emp_a, a_left, mv, 0.8, "nghieng-sau-trai", None, pose_group="left_profile", yaw=-80.0)
    db.add_embedding(emp_b, b_front, mv, 0.9, "thang-giua", None, pose_group="frontal", yaw=2.0)
    db.add_embedding(emp_b, b_left, mv, 0.8, "nghieng-sau-trai", None, pose_group="left_profile", yaw=-78.0)

    gal = Gallery(db, mv)
    assert gal.size == 2
    # Cả 2 chưa có right_profile nên profile_ready_count == 0
    assert gal.profile_ready_count == 0

    # Không bao giờ khớp nhầm vector left_profile vào ngăn frontal
    m_wrong_group = gal.match(a_left, threshold=0.40, margin=0.06, pose_group="frontal")
    assert not m_wrong_group.accepted and m_wrong_group.score < 0.1

    # Khớp đúng ngăn left_profile với ngưỡng chặt hơn
    q_ambiguous = _unit([0.0, 0.96, 0.28, 0.0])
    m_prof_reject = gal.match(
        q_ambiguous,
        threshold=0.40,
        margin=0.06,
        pose_group="left_profile",
        profile_threshold=0.48,
        profile_margin=0.12,
    )
    assert not m_prof_reject.accepted  # bị chặn bởi profile_margin chặt hơn

    m_prof_ok = gal.match(
        a_left,
        threshold=0.40,
        margin=0.06,
        pose_group="left_profile",
        profile_threshold=0.48,
        profile_margin=0.08,
    )
    assert m_prof_ok.accepted and m_prof_ok.code == "NV001"


def test_tracker_keeps_single_id_across_0_to_90_rotation_and_separates_buckets():
    tr = IoUTracker(max_age=2.0)
    v_front = _unit([1.0, 0.0, 0.0, 0.0])
    v_left = _unit([0.0, 1.0, 0.0, 0.0])   # trực giao hoàn toàn với v_front (cos = 0.0 < split_cos 0.25)
    v_right = _unit([0.0, 0.0, 1.0, 0.0])

    # Mô phỏng 1 người đi qua và xoay đầu liên tục 0° -> -82° -> +82° (có cả person_boxes tùy chọn)
    poses = [
        ("frontal", v_front, 0.0, [100, 80, 180, 180]),
        ("frontal", v_front, -5.0, [115, 80, 195, 180]),
        ("left_profile", v_left, -80.0, [130, 82, 205, 182]),
        ("left_profile", v_left, -84.0, [145, 82, 220, 182]),
        ("right_profile", v_right, +82.0, [160, 82, 235, 182]),
    ]

    track_ids = set()
    for i, (grp, emb, yaw, bb) in enumerate(poses):
        f = Face(bbox=np.array(bb, np.float32), score=0.95, kps=np.zeros((5, 2), np.float32))
        person_boxes = [np.array([bb[0] - 40, bb[1] - 20, bb[2] + 40, bb[3] + 220], np.float32)]
        matched, _ = tr.update([f], now=100.0 + i * 0.2, person_boxes=person_boxes)
        assert len(matched) == 1
        t, _ = matched[0]
        track_ids.add(t.id)
        assert not t.should_split(emb, grp, split_cos=0.25)
        t.add_embedding(emb, weight=0.8, pose_group=grp, yaw=yaw, max_embs=10)

    # Giữ đúng 1 ID duy nhất, không bị cắt track khi xoay đầu
    assert len(track_ids) == 1
    t = next(iter(tr.tracks.values()))
    assert t.person_bbox is not None
    assert len(t.buckets["frontal"]) == 2
    assert len(t.buckets["left_profile"]) == 2
    assert len(t.buckets["right_profile"]) == 1

    # Không trộn lẫn vector giữa các ngăn
    assert float(t.agg_embedding("frontal") @ v_front) > 0.99
    assert float(t.agg_embedding("left_profile") @ v_left) > 0.99
    assert abs(float(t.agg_embedding("frontal") @ t.agg_embedding("left_profile"))) < 1e-5

    # Ưu tiên Best-shot: dù có frame profile mới hơn, select_best_bucket vẫn chọn 'frontal' vì đủ >= 2 frame
    best_grp, best_vec = t.select_best_bucket(min_frontal=2, min_profile=2)
    assert best_grp == "frontal"
    assert float(best_vec @ v_front) > 0.99


def test_tracker_falls_back_to_profile_bucket_when_frontal_insufficient():
    tr = IoUTracker(max_age=2.0)
    v_front = _unit([1.0, 0.0, 0.0, 0.0])
    v_left = _unit([0.0, 1.0, 0.0, 0.0])

    f = Face(bbox=np.array([100, 80, 180, 180], np.float32), score=0.9, kps=np.zeros((5, 2), np.float32))
    matched, _ = tr.update([f], now=10.0)
    t, _ = matched[0]

    # Chỉ có 1 frame frontal thoáng qua (< min_frontal=2), nhưng có đủ 4 frame left_profile
    t.add_embedding(v_front, 0.6, "frontal", yaw=-15.0)
    for i in range(4):
        t.add_embedding(v_left, 0.75, "left_profile", yaw=-82.0)

    grp, vec = t.select_best_bucket(min_frontal=2, min_profile=4)
    assert grp == "left_profile"
    assert float(vec @ v_left) > 0.99
