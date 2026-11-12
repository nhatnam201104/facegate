"""Kiểm thử 4 lớp chống giả mạo cho góc chính diện & góc nghiêng 90° (Context + Body + 3D Parallax + Moiré)."""
import math
from pathlib import Path

import cv2
import numpy as np

from app.config import load_config
from app.engines import Face
from app.pose3d import PoseResult
from app.tracker import Track

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def _rot_y(deg: float) -> np.ndarray:
    a = math.radians(deg)
    return np.array([[math.cos(a), 0.0, math.sin(a)], [0.0, 1.0, 0.0], [-math.sin(a), 0.0, math.cos(a)]], np.float32)


def _make_3d_pts(yaw_deg: float, cx: float, cy: float, flat: bool = False) -> np.ndarray:
    rng = np.random.default_rng(12)
    base = rng.uniform(-35.0, 35.0, size=(68, 3)).astype(np.float32)
    if flat:
        base[:, 2] = 0.0
        xy = base[:, :2] + np.array([cx, cy], np.float32)
        return np.hstack([xy, np.zeros((68, 1), np.float32)])
    base[:17, 2] = -32.0
    base[27:36, 2] = 30.0
    p = (_rot_y(yaw_deg) @ base.T).T
    z_cam = 420.0 - p[:, 2]
    x2d = cx + p[:, 0] * 400.0 / z_cam
    y2d = cy + p[:, 1] * 400.0 / z_cam
    return np.stack([x2d, y2d, p[:, 2]], axis=-1).astype(np.float32)


def test_context_detector_onnx_and_spoof_medium_rules():
    from app.context_detector import ContextDetector, ContextScene, evaluate_face_context

    det = ContextDetector(MODELS_DIR)
    blank = np.full((480, 640, 3), 120, dtype=np.uint8)
    sc = det.detect(blank)
    assert isinstance(sc, ContextScene)

    # 1. Phát hiện điện thoại/màn hình bao trọn khuôn mặt và không có cơ thể người thật tương ứng -> screen_detected
    face_box = np.array([220.0, 120.0, 300.0, 220.0], np.float32)
    phone_box = np.array([190.0, 90.0, 330.0, 290.0], np.float32)
    person_box = np.array([150.0, 100.0, 380.0, 470.0], np.float32)
    scene_phone = ContextScene(persons=[], mediums=[(phone_box, "cell phone", 0.85)])
    res = evaluate_face_context(face_box, scene_phone, [face_box])
    assert res["spoof_reason"] == "screen_detected"
    assert not res["medium_clean"]

    # 2. Hai khuôn mặt gắn trên cùng 1 cơ thể (người cầm ảnh đồng nghiệp trước ngực/vai) -> multi_face_single_body
    face2_box = np.array([240.0, 230.0, 310.0, 315.0], np.float32)
    scene_one_body = ContextScene(persons=[person_box], mediums=[])
    res_dual = evaluate_face_context(face2_box, scene_one_body, [face_box, face2_box])
    assert res_dual["spoof_reason"] == "multi_face_single_body"

    # 3. Khuôn mặt lơ lửng không gắn với bất kỳ cơ thể nào -> unattached_face
    scene_no_body = ContextScene(persons=[], mediums=[])
    res_float = evaluate_face_context(face_box, scene_no_body, [face_box], require_body=True)
    assert res_float["spoof_reason"] == "unattached_face"

    # 4. Một khuôn mặt hợp lệ gắn đúng đầu-vai của 1 cơ thể
    res_ok = evaluate_face_context(face_box, scene_one_body, [face_box], require_body=True)
    assert res_ok["spoof_reason"] is None and res_ok["body_coupled"] is True


def test_moire_screen_grid_detection():
    from app.liveness import moire_screen_score

    rng = np.random.default_rng(99)
    # Da tự nhiên mượt có nhiễu nhẹ -> điểm Moiré thấp
    natural = cv2.GaussianBlur(rng.integers(100, 160, (120, 120, 3), dtype=np.uint8), (5, 5), 1.2)
    # Màn hình phát video có vân sọc tuần hoàn tần số cao + chói pixel
    screen = natural.copy()
    yy, xx = np.mgrid[:120, :120]
    stripes = (((np.sin(xx * 1.45) + np.sin(yy * 1.45)) * 38)).astype(np.int16)
    screen = np.clip(screen.astype(np.int16) + stripes[..., None], 0, 255).astype(np.uint8)

    s_nat = moire_screen_score(natural)
    s_scr = moire_screen_score(screen)
    assert s_nat < 0.50, f"Ảnh tự nhiên không được báo nhầm Moiré: {s_nat}"
    assert s_scr > 0.65, f"Ảnh có vân lưới màn hình phải bị bắt: {s_scr}"


def test_pose_gated_liveness_routes_90_degree_real_and_blocks_planar_spoof():
    from app.context_detector import ContextScene
    from app.liveness import create_liveness

    cfg = load_config()
    liv = create_liveness(cfg)
    assert liv is not None

    frame_shape = (480, 640, 3)
    smooth_frame = cv2.GaussianBlur(
        np.random.default_rng(5).integers(90, 165, frame_shape, dtype=np.uint8), (7, 7), 1.5
    )

    # Case A: Người thật đi bộ ngang qua ở góc -82° (có cơ thể hợp lệ + thị sai 3D thật qua 6 frame, dịch > 8% chiều ngang)
    t_real = Track(1, np.array([120.0, 90.0, 200.0, 190.0], np.float32), 100.0, 100.0)
    t_real.matched_group = "left_profile"
    for i in range(6):
        cx = 160.0 + i * 20.0   # tổng dịch ngang 100 px / 640 px = 15.6% >= 8%
        cy = 140.0
        fb = np.array([cx - 40, cy - 50, cx + 40, cy + 50], np.float32)
        pb = np.array([cx - 95, cy - 65, cx + 95, 460.0], np.float32)
        t_real.bbox = fb
        pts3d = _make_3d_pts(-76.0 - i * 2.2, cx, cy, flat=False)
        pose = PoseResult(-82.0, 2.0, 0.0, "left_profile", pts3d, pts3d[:5, :2], 0.8)
        f = Face(fb, 0.92, pts3d[:5, :2])
        t_real.pose_history.append((100.0 + i * 0.2, pose.yaw, pose.pitch, pose.roll, (cx, cy), pts3d))
        liv.observe(t_real, f, smooth_frame, pose, ctx_scene=ContextScene([pb], []), all_faces=[f])

    st_real = liv.evaluate_track(t_real, frame_shape=frame_shape)
    assert st_real == "live", f"Người thật góc 82° phải được duyệt, nhận {st_real}: {t_real.liveness_detail}"
    assert t_real.liveness_mode == "context_parallax"

    # Case B: Ảnh phẳng 2D của nhân viên ở góc -82° được cầm lướt ngang qua (không có thị sai 3D) -> chặn spoof
    t_flat = Track(2, np.array([120.0, 90.0, 200.0, 190.0], np.float32), 200.0, 200.0)
    t_flat.matched_group = "left_profile"
    for i in range(7):
        cx = 160.0 + i * 20.0
        cy = 140.0
        fb = np.array([cx - 40, cy - 50, cx + 40, cy + 50], np.float32)
        pb = np.array([cx - 95, cy - 65, cx + 95, 460.0], np.float32)
        t_flat.bbox = fb
        pts_flat = _make_3d_pts(-82.0, cx, cy, flat=True)
        pose = PoseResult(-82.0, 0.0, 0.0, "left_profile", pts_flat, pts_flat[:5, :2], 0.8)
        f = Face(fb, 0.92, pts_flat[:5, :2])
        t_flat.pose_history.append((200.0 + i * 0.2, pose.yaw, pose.pitch, pose.roll, (cx, cy), pts_flat))
        liv.observe(t_flat, f, smooth_frame, pose, ctx_scene=ContextScene([pb], []), all_faces=[f])

    st_flat = liv.evaluate_track(t_flat, frame_shape=frame_shape)
    assert st_flat == "spoof" and t_flat.spoof_reason == "planar_no_parallax"


def test_pipeline_emits_spoof_event_once_and_render_does_not_mutate_track(tmp_path):
    from types import SimpleNamespace

    from app.db import DB
    from app.gallery import Gallery
    from app.liveness import Liveness
    from app.pipeline import Pipeline

    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "snapshots": tmp_path / "snapshots"}
    db = DB(tmp_path / "spoof_emit.db")

    face = Face(
        bbox=np.array([180.0, 90.0, 300.0, 230.0], np.float32),
        score=0.95,
        kps=np.array([[210, 140], [270, 140], [240, 175], [218, 205], [262, 205]], np.float32),
    )

    class DummyEngine:
        name = "edgeface"
        model_version = "edgeface_spoof_test"

        def detect(self, img):
            return [face]

        def align(self, img, f, pose=None, pose_group=None):
            return np.zeros((112, 112, 3), np.uint8)

        def embed_aligned(self, aligned):
            v = np.ones(16, np.float32)
            return v / np.linalg.norm(v)

    class DummyPoseEstimator:
        def estimate(self, frame, f, prev_group=None):
            pts3d = _make_3d_pts(0.0, 240.0, 160.0, flat=True)
            return PoseResult(0.0, 0.0, 0.0, "frontal", pts3d, face.kps, 0.9)

    eval_calls = []

    class RealMarkSpoofLiveness:
        def observe(self, track, f, frame, pose, ctx_scene=None, all_faces=None, quality_ok=True):
            pass

        def evaluate_track(self, track, frame_shape=None):
            eval_calls.append(track.id)
            if track.spoof:
                return "spoof"
            return Liveness._mark_spoof(track, "screen_detected", 0.05, "context_parallax", medium_clean=False)

    eng = DummyEngine()
    gal = Gallery(db, eng.model_version)
    cam = SimpleNamespace(read=lambda: (0, None), idle=False, fps_in=15.0)
    hrm = SimpleNamespace(notify=lambda: None)
    emitted_msgs = []
    pipe = Pipeline(
        cfg,
        db,
        eng,
        gal,
        cam,
        hrm,
        emitted_msgs.append,
        liveness=RealMarkSpoofLiveness(),
        clips=None,
        pose_estimator=DummyPoseEstimator(),
    )

    rng = np.random.default_rng(11)
    frame = rng.integers(50, 200, size=(360, 480, 3), dtype=np.uint8)

    # Bước 1: evaluate_track gọi Liveness._mark_spoof thật -> phải ghi đúng 1 sự kiện spoof
    pipe._process(frame, 100.0)
    t = next(iter(pipe.tracker.tracks.values()))
    spoof_events = db.list_events(50, tier="rejected_spoof")
    assert len(spoof_events) == 1
    assert spoof_events[0]["kind"] == "spoof"
    assert t.verification_tier == "rejected_spoof"
    assert len([m for m in emitted_msgs if m.get("type") == "event" and m["event"]["kind"] == "spoof"]) == 1

    # _render không được gọi lại evaluate_track để tránh thay đổi trạng thái track ngoài luồng phát hiện
    calls_before_render = len(eval_calls)
    pipe._render(frame, 100.05)
    assert len(eval_calls) == calls_before_render

    # Bước 2 trên cùng track: không được ghi lặp sự kiện spoof lần thứ 2
    pipe._process(frame, 100.25)
    assert len(db.list_events(50, tier="rejected_spoof")) == 1
    assert len([m for m in emitted_msgs if m.get("type") == "event" and m["event"]["kind"] == "spoof"]) == 1


def test_moire_keeps_true_zeros_and_uses_median_against_single_outlier():
    from app.context_detector import ContextScene
    from app.liveness import create_liveness

    cfg = load_config()
    liv = create_liveness(cfg)
    assert liv is not None
    frame_shape = (480, 640, 3)

    def _build_profile_track(tid: int, moire_scores: list[float | None]) -> Track:
        tr = Track(tid, np.array([120.0, 90.0, 200.0, 190.0], np.float32), 100.0, 100.0)
        tr.matched_group = "left_profile"
        for i, m_sc in enumerate(moire_scores):
            cx = 140.0 + i * 16.0
            cy = 140.0
            fb = np.array([cx - 40, cy - 50, cx + 40, cy + 50], np.float32)
            tr.bbox = fb
            pts3d = _make_3d_pts(-78.0 - (i % 5) * 1.8, cx, cy, flat=False)
            tr.pose_history.append((100.0 + i * 0.15, -80.0, 1.0, 0.0, (cx, cy), pts3d))
            tr.context_history.append({
                "yaw": -80.0,
                "medium_clean": True,
                "body_coupled": True,
                "spoof_reason": None,
                "moire_score": m_sc,
            })
        return tr

    # 15 frame sạch (0.0) kèm 1 frame nhiễu (0.70) ở cuối -> không được báo spoof nhầm ở Nhánh B
    t_clean_with_spike = _build_profile_track(10, [0.0] * 15 + [0.70])
    st_spike = liv.evaluate_track(t_clean_with_spike, frame_shape=frame_shape)
    assert st_spike == "live", f"1 frame nhiễu Moiré 0.70 giữa 15 frame 0.0 không được báo spoof: {st_spike}"
    assert not t_clean_with_spike.spoof

    # Khi mới có < 4 mẫu Moiré hợp lệ (các frame đầu quality_ok=False trả None) dù điểm cao -> giữ checking, chưa kết luận spoof
    t_insufficient = _build_profile_track(11, [None, None, None, 0.82, 0.84, 0.85])
    st_insuf = liv.evaluate_track(t_insufficient, frame_shape=frame_shape)
    assert st_insuf == "checking", f"Chưa đủ 4 mẫu Moiré không được đánh spoof sớm: {st_insuf}"

    # 6 frame liên tiếp >= 0.80 -> bắt đúng spoof moire_pattern
    t_screen = _build_profile_track(12, [0.81, 0.83, 0.85, 0.82, 0.84, 0.86])
    st_scr = liv.evaluate_track(t_screen, frame_shape=frame_shape)
    assert st_scr == "spoof" and t_screen.spoof_reason == "moire_pattern"


def test_normal_objects_near_face_or_tv_behind_person_do_not_trigger_permanent_spoof():
    from app.context_detector import ContextScene, evaluate_face_context
    from app.liveness import create_liveness

    face_box = np.array([220.0, 110.0, 300.0, 210.0], np.float32)
    person_box = np.array([150.0, 95.0, 370.0, 470.0], np.float32)

    # 1. Người thật cầm điện thoại ngang ngực / dưới cằm (nằm trong vùng mở rộng 2.5x quanh mặt nhưng chồng lên mặt < 20%)
    chest_phone = np.array([215.0, 225.0, 295.0, 330.0], np.float32)
    res_chest = evaluate_face_context(face_box, ContextScene(persons=[person_box], mediums=[(chest_phone, "cell phone", 0.9)]), [face_box])
    assert res_chest["spoof_reason"] is None
    assert res_chest["body_coupled"] is True
    assert res_chest.get("medium_nearby") is True

    # 2. Mặt nằm trọn trong màn hình điện thoại và không gắn cơ thể phù hợp -> screen_detected
    enclosing_phone = np.array([200.0, 90.0, 320.0, 260.0], np.float32)
    distant_holder = np.array([40.0, 80.0, 210.0, 470.0], np.float32)
    res_spoof_phone = evaluate_face_context(
        face_box,
        ContextScene(persons=[distant_holder], mediums=[(enclosing_phone, "cell phone", 0.92)]),
        [face_box],
    )
    assert res_spoof_phone["spoof_reason"] == "screen_detected"
    assert res_spoof_phone["medium_clean"] is False

    # 3. TV lớn treo tường phía sau chứa hộp mặt, nhưng mặt gắn với thân người thật trải dài ra ngoài/trước TV -> không spoof
    wall_tv = np.array([110.0, 60.0, 430.0, 280.0], np.float32)
    res_tv_bg = evaluate_face_context(
        face_box,
        ContextScene(persons=[person_box], mediums=[(wall_tv, "tv", 0.88)]),
        [face_box],
    )
    assert res_tv_bg["spoof_reason"] is None
    assert res_tv_bg["body_coupled"] is True

    # 4. Trong evaluate_track: cần >= 3/5 frame gần nhất báo screen_detected mới chốt spoof (2 hit rải rác không khóa spoof vĩnh viễn)
    cfg = load_config()
    liv = create_liveness(cfg)
    t = Track(30, face_box, 10.0, 10.0)
    t.live = [0.92, 0.91, 0.93, 0.94]
    reasons = ["screen_detected", None, None, None, "screen_detected", None]
    for r in reasons:
        t.context_history.append({
            "yaw": 0.0,
            "medium_clean": r != "screen_detected",
            "body_coupled": r is None,
            "spoof_reason": r,
            "moire_score": 0.05,
        })
    assert liv.evaluate_track(t) == "live"
    assert not t.spoof

    # Khi >= 3 trong 5 frame gần nhất là screen_detected -> chốt spoof cố định cho hết track
    t2 = Track(31, face_box, 20.0, 20.0)
    for r in ["screen_detected", None, "screen_detected", "screen_detected", None]:
        t2.context_history.append({
            "yaw": 0.0,
            "medium_clean": r != "screen_detected",
            "body_coupled": r is None,
            "spoof_reason": r,
            "moire_score": 0.05,
        })
    assert liv.evaluate_track(t2) == "spoof"
    assert t2.spoof_reason == "screen_detected"


def test_phone_held_over_own_face_detected_and_intermittent_screen_holds_checking():
    from app.context_detector import ContextScene, evaluate_face_context
    from app.liveness import create_liveness

    # 1. Kẻ gian cầm điện thoại/tablet che ngay trước mặt mình (khớp anatomical_head_match với thân người cầm)
    person_box = np.array([150.0, 90.0, 370.0, 470.0], np.float32)
    phone_over_face = np.array([185.0, 95.0, 325.0, 255.0], np.float32)
    face_on_phone = np.array([215.0, 115.0, 295.0, 215.0], np.float32)
    res_over = evaluate_face_context(
        face_on_phone,
        ContextScene(persons=[person_box], mediums=[(phone_over_face, "cell phone", 0.92)]),
        [face_on_phone],
    )
    assert res_over["spoof_reason"] == "screen_detected"
    assert res_over["medium_clean"] is False

    # 2. Khi màn hình chập chờn ở 2 frame sát cuối (chưa đủ 3/5 để khóa spoof vĩnh viễn nhưng đang nghi ngờ sát thời điểm hiện tại) -> giữ 'checking', không duyệt 'live' ngay
    cfg = load_config()
    liv = create_liveness(cfg)
    t_flicker = Track(40, face_on_phone, 10.0, 10.0)
    t_flicker.matched_group = "frontal"
    t_flicker.live = [0.85, 0.86, 0.88, 0.87]
    for r in [None, None, "screen_detected", None, "screen_detected"]:
        t_flicker.context_history.append({
            "yaw": 0.0,
            "medium_clean": r != "screen_detected",
            "medium_nearby": r == "screen_detected",
            "body_coupled": r is None,
            "spoof_reason": r,
            "moire_score": 0.05,
        })
    assert liv.evaluate_track(t_flicker) == "checking"
    assert not t_flicker.spoof


def test_matched_group_and_liveness_branch_alignment_blocks_frontal_spoof_leak_and_wrong_tier_upgrade(tmp_path):
    from types import SimpleNamespace

    from app.context_detector import ContextScene
    from app.db import DB
    from app.gallery import Gallery
    from app.liveness import create_liveness
    from app.pipeline import Pipeline

    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "snapshots": tmp_path / "snaps"}
    db = DB(tmp_path / "branch_align.db")
    mv = "ef_align_v1"
    u = lambda v: np.array(v, np.float32) / np.linalg.norm(v)
    ea = db.create_employee("NVA", "Emp A")
    db.set_status(ea, "active")
    a_f, a_l = u([1.0, 0.0, 0.0, 0.0]), u([0.0, 1.0, 0.0, 0.0])
    db.add_embedding(ea, a_f, mv, 0.9, "thang-giua", None, pose_group="frontal", yaw=0.0)
    db.add_embedding(ea, a_l, mv, 0.9, "nghieng-sau-trai", None, pose_group="left_profile", yaw=-80.0)

    liv = create_liveness(cfg)
    st = {"emb": a_f, "yaw": 0.0, "grp": "frontal", "cx": 120.0, "fas": 0.12}
    liv.real_prob = lambda img, bbox: st["fas"]

    class Eng:
        name = "edgeface"
        model_version = mv

        def detect(self, img):
            cx = st["cx"]
            fb = np.array([cx - 40, 90.0, cx + 40, 190.0], np.float32)
            kps = np.array([[cx - 18, 125], [cx + 18, 125], [cx, 148], [cx - 14, 168], [cx + 14, 168]], np.float32)
            return [Face(fb, 0.95, kps)]

        def align(self, img, f, pose=None, pose_group=None):
            return np.zeros((112, 112, 3), np.uint8)

        def embed_aligned(self, aligned):
            return st["emb"]

    class Pose:
        def estimate(self, frame, f, prev_group=None):
            pts3d = _make_3d_pts(st["yaw"], st["cx"], 140.0, flat=False)
            return PoseResult(st["yaw"], 0.0, 0.0, st["grp"], pts3d, pts3d[:5, :2], 0.85)

    class CtxDet:
        def detect(self, frame):
            cx = st["cx"]
            return ContextScene(persons=[np.array([cx - 95, 75.0, cx + 95, 460.0], np.float32)], mediums=[])

    pipe = Pipeline(
        cfg, db, Eng(), Gallery(db, mv),
        SimpleNamespace(read=lambda: (0, None), idle=False, fps_in=15.0),
        SimpleNamespace(notify=lambda: None), lambda m: None,
        liveness=liv, clips=None, pose_estimator=Pose(), context_detector=CtxDet(),
    )
    frame = cv2.GaussianBlur(np.random.default_rng(5).integers(70, 190, (480, 640, 3), dtype=np.uint8), (3, 3), 0.7)

    # Case 1: 2 frame ảnh giả chính diện (FAS = 0.12 < spoof_below) rồi nghiêng >40° 6 frame -> tuyệt đối không được lọt checkin
    for i in range(2):
        st.update(emb=a_f, yaw=0.0, grp="frontal", cx=120.0 + i * 20.0, fas=0.12)
        pipe._process(frame, 10.0 + i * 0.2)
    for i in range(6):
        st.update(emb=a_l, yaw=-75.0 - i * 2.0, grp="left_profile", cx=160.0 + i * 20.0, fas=0.12)
        pipe._process(frame, 10.4 + i * 0.2)
    checkins = [e for e in db.list_events(10) if e["kind"] == "checkin"]
    assert not checkins, f"Ảnh giả chính diện FAS=0.12 không được lọt checkin qua Nhánh B: {checkins}"

    # Case 2: Track chốt danh tính ở left_profile, sau đó có 4 frame frontal điểm khớp 0.37 (chưa được _identify chấp nhận nâng lên frontal) -> không được gán sai verification_tier='verified'
    pipe.tracker.tracks.clear()
    db2 = DB(tmp_path / "branch_align2.db")
    ea2 = db2.create_employee("NVA", "Emp A")
    db2.set_status(ea2, "active")
    db2.add_embedding(ea2, a_f, mv, 0.9, "thang-giua", None, pose_group="frontal", yaw=0.0)
    db2.add_embedding(ea2, a_l, mv, 0.9, "nghieng-sau-trai", None, pose_group="left_profile", yaw=-80.0)
    pipe2 = Pipeline(
        cfg, db2, Eng(), Gallery(db2, mv),
        SimpleNamespace(read=lambda: (0, None), idle=False, fps_in=15.0),
        SimpleNamespace(notify=lambda: None), lambda m: None,
        liveness=liv, clips=None, pose_estimator=Pose(), context_detector=CtxDet(),
    )
    for i in range(4):
        st.update(emb=a_l, yaw=-80.0, grp="left_profile", cx=200.0, fas=0.92)
        pipe2._process(frame, 20.0 + i * 0.2)
    weak_front = u([0.37, 0.0, float(np.sqrt(1.0 - 0.37 ** 2)), 0.0])
    for i in range(4):
        st.update(emb=weak_front, yaw=0.0, grp="frontal", cx=200.0, fas=0.92)
        pipe2._process(frame, 21.0 + i * 0.2)
    for e in db2.list_events(10):
        if e["kind"] == "checkin" and e["pose_group"] != "frontal":
            assert e["verification_tier"] == "profile_verified"
