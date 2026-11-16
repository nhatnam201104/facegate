"""Kiem thu luong dang ky co huong dan voi camera + engine gia (khong can model/webcam that)."""
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from app.config import load_config
from app.db import DB
from app.engines import Face
from app import guided
from app.guided import STEPS, GuidedSession, evaluate

P = dict(guided.DEFAULTS)


# ------------------------------------------------------------------ ham thuan
def test_five_cross_steps():
    assert len(STEPS) == 5
    assert {(s.h, s.v) for s in STEPS} == {(0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)}
    assert STEPS[0].bin == "thang-giua"


def test_front_needs_center():
    s = STEPS[0]
    assert evaluate(s, 0, 0, P)[0]
    assert not evaluate(s, 20, 0, P)[0]
    assert not evaluate(s, 0, 20, P)[0]


def test_side_step_requires_correct_direction_and_amount():
    s = STEPS[1]  # trai-giua: yaw am
    assert not evaluate(s, 0, 0, P)[0]            # chua quay
    assert not evaluate(s, -8, 0, P)[0]           # quay chua du
    assert not evaluate(s, +20, 0, P)[0]          # quay nguoc
    assert evaluate(s, -20, 0, P)[0]
    assert not evaluate(s, -20, 15, P)[0]         # dang cui trong khi buoc chi yeu cau giua
    assert not evaluate(s, -60, 0, P)[0]          # qua nhieu


def test_vertical_steps():
    up, down = STEPS[3], STEPS[4]
    assert not evaluate(up, 0, 0, P)[0] and not evaluate(up, 0, 15, P)[0]
    assert evaluate(up, 0, -15, P)[0] and evaluate(down, 0, 15, P)[0]
    assert not evaluate(up, 20, -15, P)[0]        # dang quay ngang trong khi chi yeu cau ngang len


# ------------------------------------------------------------------ mo phong ca phien
class FakeCamera:
    kind, connected = "webcam", True

    def __init__(self):
        self.pose = (0.0, 0.0)       # yaw, pitch cua "nguoi"
        self.faces = 1
        self.fid = 0
        rng = np.random.default_rng(0)
        self.img = rng.integers(60, 200, (240, 320, 3), dtype=np.uint8)

    def read(self):
        self.fid += 1
        time.sleep(0.01)
        return self.fid, self.img.copy()


class FakeEngine:
    model_version = "fake-1"

    def __init__(self, cam):
        self.cam = cam

    def detect(self, img):
        yaw, pitch = self.cam.pose
        le, re = np.array([100.0, 100.0]), np.array([140.0, 100.0])
        nose = np.array([120.0 + yaw / 90.0 * 40.0, 100.0 + (pitch / 100.0 + 0.55) * 40.0])
        kps = np.array([le, re, nose, [105, 140], [135, 140]], dtype=np.float32)
        f = Face(bbox=np.array([70.0, 60.0, 170.0, 180.0]), score=0.99, kps=kps)
        faces = [f]
        if self.cam.faces > 1:
            faces.append(Face(bbox=np.array([200.0, 60.0, 300.0, 180.0]), score=0.9, kps=kps.copy()))
        return faces

    def align(self, img, face):
        return img

    def embed_aligned(self, aligned):
        v = np.ones(8, dtype=np.float32)
        return v / np.linalg.norm(v)


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "enroll": tmp_path / "enroll"}
    cam = FakeCamera()
    ctx = SimpleNamespace(cfg=cfg, db=DB(tmp_path / "t.db"), camera=cam, engine=FakeEngine(cam),
                          engine_lock=threading.Lock(), threshold=0.4,
                          gallery=SimpleNamespace(reload=lambda: None))
    # nhanh hon trong test
    monkeypatch.setitem(guided.DEFAULTS, "hold_seconds", 0.15)
    monkeypatch.setitem(guided.DEFAULTS, "pause_seconds", 0.05)
    monkeypatch.setitem(guided.DEFAULTS, "poll_interval", 0.01)
    return ctx, cam


def wait(cond, timeout=8.0):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def pose_for(step, front=(0.0, 0.0)):
    return (front[0] + step.h * 22.0, front[1] + step.v * 16.0)


def test_full_five_angles_in_order(env):
    ctx, cam = env
    g = GuidedSession(ctx, "NV001", "Nguyen Van A").start()
    for i, st in enumerate(STEPS):
        cam.pose = pose_for(st, front=(1.0, 3.0))   # nguoi co pitch nhin thang lech 3 do
        assert wait(lambda: g.step > i or g.state != "running"), f"ket o buoc {i}: {g.snapshot()['hint']}"
    assert wait(lambda: g.state == "done"), g.snapshot()
    rep = g.snapshot()["report"]
    assert rep["frames_selected"] == 5 and rep["guided"] is True
    assert sorted(rep["bins"]) == sorted(s.bin for s in STEPS)
    assert all(rep["bins"][s.bin] == 1 for s in STEPS)


def test_does_not_advance_until_angle_reached(env):
    ctx, cam = env
    g = GuidedSession(ctx, "NV002", "B").start()
    assert wait(lambda: g.step == 1)               # buoc 1 xong khi nhin thang
    cam.pose = (0.0, 0.0)                          # van nhin thang, buoc 2 yeu cau quay
    time.sleep(0.6)
    assert g.step == 1 and not g.snapshot()["ok"]
    cam.pose = (-8.0, 0.0)                         # quay chua du
    time.sleep(0.6)
    assert g.step == 1
    cam.pose = (+25.0, 0.0)                        # quay nguoc
    time.sleep(0.6)
    assert g.step == 1
    cam.pose = (-25.0, 0.0)                        # quay dung huong
    assert wait(lambda: g.step == 2)
    g.cancel()


def test_two_faces_blocks_progress(env):
    ctx, cam = env
    cam.faces = 2
    g = GuidedSession(ctx, "NV003", "C").start()
    time.sleep(0.6)
    snap = g.snapshot()
    assert snap["step"] == 0 and "một người" in snap["hint"]
    cam.faces = 1
    assert wait(lambda: g.step == 1)
    g.cancel()


def test_cancel(env):
    ctx, cam = env
    g = GuidedSession(ctx, "NV004", "D").start()
    g.cancel()
    assert wait(lambda: g.state == "cancelled")


def test_seven_angles_with_profile_90_and_supplement_and_isolated_engine(env):
    from app.enrollment import EnrollError, try_auto_enroll_from_gate
    ctx, cam = env
    # Tao enroll_engine rieng va khoa gate engine_lock de chung minh GuidedSession KHONG cham vao gate lock (P6, T026)
    ctx.enroll_engine = FakeEngine(cam)
    ctx.enroll_lock = threading.Lock()
    ctx.engine_lock.acquire()
    try:
        g = GuidedSession(ctx, "NV005", "Hoàng Văn E", profile_90=True).start()
        assert g.snapshot()["total"] == 7
        for i, st in enumerate(g.steps):
            if abs(st.h) == 2:
                cam.pose = (-75.0 if st.h < 0 else 75.0, 0.0)
            else:
                cam.pose = pose_for(st, front=(0.0, 0.0))
            assert wait(lambda: g.step > i or g.state != "running"), f"ket o buoc {i}: {g.snapshot()['hint']}"
        assert wait(lambda: g.state == "done"), g.snapshot()
        rep = g.snapshot()["report"]
        assert rep["frames_selected"] == 7

        # Kiem tra supplement_only (1 buoc xac minh thang + 2 buoc nghieng suon = 3 buoc, chi luu 2 mau nghieng moi)
        gs = GuidedSession(ctx, "NV005", "Hoàng Văn E", supplement_only=True).start()
        assert gs.snapshot()["total"] == 3
        for i, st in enumerate(gs.steps):
            cam.pose = (0.0, 0.0) if st.verify_only else (-76.0 if st.h < 0 else 76.0, 0.0)
            assert wait(lambda: gs.step > i or gs.state != "running")
        assert wait(lambda: gs.state == "done")
        assert gs.snapshot()["report"]["supplement_only"] is True
        assert gs.snapshot()["report"]["frames_selected"] == 2
    finally:
        ctx.engine_lock.release()

    # Kiem tra cam tuyet doi auto_enroll_online (H17)
    with pytest.raises(EnrollError):
        try_auto_enroll_from_gate(ctx)


def test_t035_enrollment_uses_frontal_mean_config_outliers_and_backfills_new_version(tmp_path):
    from app.enrollment import backfill_legacy_embeddings, process_enrollment

    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "enroll": tmp_path / "enroll"}
    cfg["enrollment"]["min_frames"] = 3
    cfg["enrollment"]["outlier_min_cosine"] = 0.42
    cfg["enrollment"]["cross_pose_min_cosine"] = 0.20
    db = DB(tmp_path / "e.db")

    # Gia lap 5 frame khac nhau nhe de khong bi dedup 0.95, nhung profile cos ~0.28 voi frontal (<0.45 nhung >=0.20!)
    # Neu code cu dung mean gop + hardcode 0.45 -> bi loai thanh outlier (>30% -> EnrollError).
    # Code moi dung frontal mean + cross_pose_min_cosine(0.20) -> giu lai thanh cong ca 5 mau!
    vecs_by_idx = [
        np.array([1.00, 0.00, 0.00, 0.00], dtype=np.float32),
        np.array([0.92, 0.39, 0.00, 0.00], dtype=np.float32),
        np.array([0.92, -0.39, 0.00, 0.00], dtype=np.float32),
        np.array([0.28, 0.00, 0.96, 0.00], dtype=np.float32),
        np.array([0.28, 0.00, -0.96, 0.00], dtype=np.float32),
    ]
    vecs_by_idx = [v / np.linalg.norm(v) for v in vecs_by_idx]
    rng = np.random.default_rng(42)
    base_img = rng.integers(80, 180, (240, 320, 3), dtype=np.uint8)
    frames = [base_img.copy() for _ in range(5)]
    pose_seq = [0.0, -5.0, 5.0, -75.0, 75.0]
    call_idx = {"i": 0}

    class _EnrollEngine:
        model_version = "edgeface_test_v41"

        def detect(self, img):
            yaw = pose_seq[min(call_idx["i"], len(pose_seq) - 1)]
            le, re = np.array([100.0, 80.0]), np.array([160.0, 80.0])
            nose = np.array([130.0 + yaw / 90.0 * 40.0, 115.0])
            kps = np.array([le, re, nose, [110.0, 150.0], [150.0, 150.0]], dtype=np.float32)
            return [Face(np.array([70.0, 40.0, 190.0, 180.0], dtype=np.float32), 0.95, kps)]

        def align(self, img, face, pose=None, pose_group=None):
            idx = call_idx["i"]
            call_idx["i"] += 1
            return idx

        def embed_aligned(self, aligned):
            return vecs_by_idx[int(aligned) % len(vecs_by_idx)].copy()

    class _EnrollPose:
        def estimate(self, img, face, prev_group=None):
            from app.pose3d import PoseResult
            yaw = pose_seq[min(call_idx["i"], len(pose_seq) - 1)]
            grp = "left_profile" if yaw < -40 else ("right_profile" if yaw > 40 else "frontal")
            return PoseResult(yaw, 0.0, 0.0, grp, np.zeros((68, 3), np.float32), face.kps, 0.8)

    ctx = SimpleNamespace(
        cfg=cfg,
        db=db,
        engine=_EnrollEngine(),
        pose=_EnrollPose(),
        engine_lock=threading.Lock(),
        threshold=0.40,
    )
    rep = process_enrollment(frames, "NV099", "Tran Van Test", ctx)
    assert rep["outliers"] == 0
    assert rep["frames_selected"] == 5

    # Kiem tra backfill khi doi model_version sang _v41 moi tu dong tai trich xuat anh tu data/enroll/
    ctx.engine.model_version = "edgeface_test_v41_next"
    call_idx["i"] = 0
    migrated = backfill_legacy_embeddings(ctx)
    assert migrated == 1
    rows = db.q("SELECT COUNT(*) AS n FROM embeddings WHERE employee_id=? AND model_version=?",
                (rep["employee_id"], "edgeface_test_v41_next"))
    assert rows[0]["n"] == 5
