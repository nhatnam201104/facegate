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


def test_reverification_on_current_pose_bucket_splits_when_profile_mismatches(tmp_path):
    from types import SimpleNamespace
    from app.config import load_config
    from app.pipeline import Pipeline
    from app.pose3d import PoseResult

    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "snapshots": tmp_path / "snapshots"}
    cfg["liveness"]["enabled"] = False
    db = DB(tmp_path / "reverify.db")
    mv = "edgeface_reverify_v1"

    emp_a = db.create_employee("NVA", "Nguyễn Văn A")
    db.set_status(emp_a, "active")
    emp_b = db.create_employee("NVB", "Trần Thị B")
    db.set_status(emp_b, "active")

    a_front = _unit([1.0, 0.0, 0.0, 0.0])
    a_left = _unit([0.0, 1.0, 0.0, 0.0])
    b_front = _unit([0.0, 0.0, 1.0, 0.0])
    b_left = _unit([0.0, 0.0, 0.0, 1.0])

    db.add_embedding(emp_a, a_front, mv, 0.9, "thang-giua", None, pose_group="frontal", yaw=0.0)
    db.add_embedding(emp_a, a_left, mv, 0.85, "nghieng-sau-trai", None, pose_group="left_profile", yaw=-80.0)
    db.add_embedding(emp_b, b_front, mv, 0.9, "thang-giua", None, pose_group="frontal", yaw=0.0)
    db.add_embedding(emp_b, b_left, mv, 0.85, "nghieng-sau-trai", None, pose_group="left_profile", yaw=-80.0)

    emp_c = db.create_employee("NVC", "Lê Văn C (chưa có góc nghiêng)")
    db.set_status(emp_c, "active")
    c_front = _unit([0.7, 0.7, 0.0, 0.0])
    db.add_embedding(emp_c, c_front, mv, 0.9, "thang-giua", None, pose_group="frontal", yaw=0.0)

    state = {"emb": a_front, "yaw": 0.0, "grp": "frontal"}
    face = Face(
        bbox=np.array([150.0, 80.0, 270.0, 220.0], np.float32),
        score=0.95,
        kps=np.array([[185, 130], [235, 130], [210, 165], [190, 195], [230, 195]], np.float32),
    )

    class ScriptedEngine:
        name = "edgeface"
        model_version = mv

        def detect(self, img):
            return [face]

        def align(self, img, f, pose=None, pose_group=None):
            return np.zeros((112, 112, 3), np.uint8)

        def embed_aligned(self, aligned):
            return state["emb"]

    class ScriptedPose:
        def estimate(self, frame, f, prev_group=None):
            pts = np.zeros((68, 3), np.float32)
            return PoseResult(state["yaw"], 0.0, 0.0, state["grp"], pts, face.kps, 0.85)

    gal = Gallery(db, mv)
    cam = SimpleNamespace(read=lambda: (0, None), idle=False, fps_in=15.0)
    hrm = SimpleNamespace(notify=lambda: None)
    pipe = Pipeline(
        cfg, db, ScriptedEngine(), gal, cam, hrm, lambda m: None,
        liveness=None, clips=None, pose_estimator=ScriptedPose(),
    )
    frame = np.random.default_rng(3).integers(60, 200, size=(360, 480, 3), dtype=np.uint8)

    # 1. Nhận diện nhân viên A bằng 3 frame frontal
    for i in range(3):
        pipe._process(frame, 10.0 + i * 0.2)
    t = next(iter(pipe.tracker.tracks.values()))
    orig_tid = t.id
    assert t.code == "NVA"

    # Thêm 4 frame left_profile của chính nhân viên A -> giữ nguyên danh tính NVA và không tách track
    state.update(emb=a_left, yaw=-80.0, grp="left_profile")
    for i in range(4):
        pipe._process(frame, 11.0 + i * 0.2)
    t = next(iter(pipe.tracker.tracks.values()))
    assert t.id == orig_tid and t.code == "NVA"

    # 2. Mô phỏng track mới xác nhận A bằng 3 frame frontal, sau đó ghép nhầm 4 frame left_profile của B -> phải tách track / mất danh tính A
    pipe.tracker.tracks.clear()
    state.update(emb=a_front, yaw=0.0, grp="frontal")
    for i in range(3):
        pipe._process(frame, 20.0 + i * 0.2)
    t_swap = next(iter(pipe.tracker.tracks.values()))
    swap_tid = t_swap.id
    assert t_swap.code == "NVA"

    state.update(emb=b_left, yaw=-80.0, grp="left_profile")
    for i in range(4):
        pipe._process(frame, 21.0 + i * 0.2)
    t_after = next(iter(pipe.tracker.tracks.values()))
    assert t_after.code != "NVA" or t_after.id != swap_tid

    # 3. Nhân viên C chưa có mẫu left_profile -> không tự ý hủy danh tính khi xoay sang left_profile
    pipe.tracker.tracks.clear()
    state.update(emb=c_front, yaw=0.0, grp="frontal")
    for i in range(3):
        pipe._process(frame, 30.0 + i * 0.2)
    t_c = next(iter(pipe.tracker.tracks.values()))
    assert t_c.code == "NVC"

    state.update(emb=_unit([0.2, 0.2, 0.2, 0.9]), yaw=-80.0, grp="left_profile")
    for i in range(4):
        pipe._process(frame, 31.0 + i * 0.2)
    t_c_after = next(iter(pipe.tracker.tracks.values()))
    assert t_c_after.code == "NVC"


def test_reverify_preserves_best_shot_prevents_pingpong_and_splits_legacy_impostor(tmp_path):
    from types import SimpleNamespace
    from app.config import load_config
    from app.pipeline import Pipeline
    from app.pose3d import PoseResult

    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "snapshots": tmp_path / "snapshots"}
    db = DB(tmp_path / "reverify_r2.db")
    mv = "ef_reverify_r2"

    emp_a = db.create_employee("NVA", "Nguyễn Văn A")
    db.set_status(emp_a, "active")
    emp_b = db.create_employee("NVB", "Trần Thị B")
    db.set_status(emp_b, "active")
    emp_c = db.create_employee("NVC", "Lê Văn C (chỉ có frontal)")
    db.set_status(emp_c, "active")

    a_front = _unit([1.0, 0.0, 0.0, 0.0])
    a_left = _unit([0.0, 0.85, 0.5268, 0.0])
    b_front = _unit([0.0, 0.0, 0.0, 1.0])
    b_left = _unit([0.0, 0.55, 0.835, 0.0])
    c_front = _unit([0.8, 0.6, 0.0, 0.0])

    db.add_embedding(emp_a, a_front, mv, 0.9, "thang-giua", None, pose_group="frontal", yaw=0.0)
    db.add_embedding(emp_a, a_left, mv, 0.9, "nghieng-sau-trai", None, pose_group="left_profile", yaw=-80.0)
    db.add_embedding(emp_b, b_front, mv, 0.9, "thang-giua", None, pose_group="frontal", yaw=0.0)
    db.add_embedding(emp_b, b_left, mv, 0.9, "nghieng-sau-trai", None, pose_group="left_profile", yaw=-80.0)
    db.add_embedding(emp_c, c_front, mv, 0.9, "thang-giua", None, pose_group="frontal", yaw=0.0)

    state = {"emb": a_front, "yaw": 0.0, "grp": "frontal", "live_ret": "checking"}
    face = Face(
        bbox=np.array([150.0, 80.0, 270.0, 220.0], np.float32),
        score=0.95,
        kps=np.array([[185, 130], [235, 130], [210, 165], [190, 195], [230, 195]], np.float32),
    )

    class ScriptedEngine:
        name = "edgeface"
        model_version = mv

        def detect(self, img):
            return [face]

        def align(self, img, f, pose=None, pose_group=None):
            return np.zeros((112, 112, 3), np.uint8)

        def embed_aligned(self, aligned):
            return state["emb"]

    class ScriptedPose:
        def estimate(self, frame, f, prev_group=None):
            return PoseResult(state["yaw"], 0.0, 0.0, state["grp"], np.zeros((68, 3), np.float32), face.kps, 0.85)

    liv = SimpleNamespace(
        observe=lambda *a, **k: None,
        evaluate_track=lambda tr, frame_shape=None: state["live_ret"],
    )
    msgs = []
    pipe = Pipeline(
        cfg, db, ScriptedEngine(), Gallery(db, mv),
        SimpleNamespace(read=lambda: (0, None), idle=False, fps_in=15.0),
        SimpleNamespace(notify=lambda: None), msgs.append,
        liveness=liv, clips=None, pose_estimator=ScriptedPose(),
    )
    frame = np.random.default_rng(7).integers(60, 200, size=(360, 480, 3), dtype=np.uint8)

    # (a) NVA khớp frontal (liveness đang checking), quay đầu sang góc nghiêng có điểm yếu (0.20) nhưng không khớp người khác -> KHÔNG cắt đứt Best-shot
    for i in range(2):
        pipe._process(frame, 10.0 + i * 0.2)
    t_a = next(iter(pipe.tracker.tracks.values()))
    tid_a = t_a.id
    assert t_a.code == "NVA"
    weak_noisy_prof = _unit([0.95, 0.20, 0.0, 0.23])  # không khớp NVB, sim với a_left thấp
    state.update(emb=weak_noisy_prof, yaw=-84.0, grp="left_profile")
    for i in range(4):
        pipe._process(frame, 10.6 + i * 0.2)
    t_a_after = next(iter(pipe.tracker.tracks.values()))
    assert t_a_after.id == tid_a and t_a_after.code == "NVA" and t_a_after.matched_group == "frontal"

    # (b) Chặn ping-pong danh tính: NVA đã check-in ở frontal, sang left_profile khớp accepted với NVB -> phải tách track, không được bắn lặp greet cho NVA
    pipe.tracker.tracks.clear()
    msgs.clear()
    state.update(emb=a_front, yaw=0.0, grp="frontal", live_ret="live")
    for i in range(2):
        pipe._process(frame, 20.0 + i * 0.2)
    probe_b_prof = _unit([0.0, 0.30, 0.9539, 0.0])  # sim(a_left)=0.757 >= 0.41 nhưng khớp accepted với NVB (0.962)
    state.update(emb=probe_b_prof, yaw=-80.0, grp="left_profile", live_ret="live")
    for i in range(7):
        pipe._process(frame, 20.6 + i * 0.2)
    greet_msgs = [m for m in msgs if m.get("type") == "greet" and m.get("code") == "NVA"]
    assert len(greet_msgs) == 0, f"Bị ping-pong bắn lặp greet cho NVA: {greet_msgs}"

    # (c) Nhân viên cũ NVC (chưa có mẫu left_profile -> emp_sim is None) đang checking, bị ghép nhầm sang 4 frame left_profile của NVB -> phải tách khỏi NVC
    pipe.tracker.tracks.clear()
    state.update(emb=c_front, yaw=0.0, grp="frontal", live_ret="checking")
    for i in range(2):
        pipe._process(frame, 30.0 + i * 0.2)
    assert next(iter(pipe.tracker.tracks.values())).code == "NVC"
    state.update(emb=b_left, yaw=-80.0, grp="left_profile", live_ret="live")
    for i in range(4):
        pipe._process(frame, 30.6 + i * 0.2)
    t_end = next(iter(pipe.tracker.tracks.values()))
    assert t_end.code == "NVB", f"Phải tách sang NVB thay vì giữ NVC khi ngăn profile khớp NVB: {t_end.code}"
