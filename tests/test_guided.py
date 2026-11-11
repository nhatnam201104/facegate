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

    def align(self, img, face, pose=None, pose_group=None):
        return img

    def embed_aligned(self, aligned):
        # Mô phỏng đặc trưng góc nghiêng khác biệt với góc thẳng (nhưng nhất quán trong cùng nhóm góc)
        yaw = self.cam.pose[0]
        if yaw < -40.0:
            v = np.array([0.1, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
        elif yaw > 40.0:
            v = np.array([0.1, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
        else:
            v = np.array([1.0, 0.05, 0.05, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
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
    if abs(step.h) == 2:
        return (front[0] + (-76.0 if step.h < 0 else 76.0), front[1])
    return (front[0] + step.h * 22.0, front[1] + step.v * 16.0)


def test_full_five_angles_in_order(env):
    ctx, cam = env
    g = GuidedSession(ctx, "NV001", "Nguyen Van A", profile_90=False).start()
    for i, st in enumerate(STEPS):
        cam.pose = pose_for(st, front=(1.0, 3.0))   # nguoi co pitch nhin thang lech 3 do
        assert wait(lambda: g.step > i or g.state != "running"), f"ket o buoc {i}: {g.snapshot()['hint']}"
    assert wait(lambda: g.state == "done"), g.snapshot()
    rep = g.snapshot()["report"]
    assert rep["frames_selected"] == 5 and rep["guided"] is True
    assert sorted(rep["bins"]) == sorted(s.bin for s in STEPS)
    assert all(rep["bins"][s.bin] == 1 for s in STEPS)


def test_full_seven_angles_with_deep_profile_90_and_supplement(env):
    from app.enrollment import backfill_legacy_embeddings
    from app.guided import PROFILE_STEPS

    ctx, cam = env
    # 1. Nhân viên cũ đăng ký 5 góc trước, sau đó quay bổ sung riêng 2 góc nghiêng sâu (supplement_only=True)
    g5 = GuidedSession(ctx, "NV010", "Le Thi Cu", profile_90=False).start()
    for i, st in enumerate(STEPS):
        cam.pose = pose_for(st)
        assert wait(lambda: g5.step > i or g5.state != "running")
    assert wait(lambda: g5.state == "done")
    emp_rows = ctx.db.list_employees(ctx.engine.model_version)
    assert emp_rows[0]["pose_coverage"]["ready_90"] is False

    gsup = GuidedSession(ctx, "NV010", "Le Thi Cu", profile_90=True, supplement_only=True).start()
    assert gsup.snapshot()["total"] == 2
    for i, st in enumerate(PROFILE_STEPS):
        cam.pose = pose_for(st)
        assert wait(lambda: gsup.step > i or gsup.state != "running"), gsup.snapshot()
    assert wait(lambda: gsup.state == "done"), gsup.snapshot()
    emp_rows = ctx.db.list_employees(ctx.engine.model_version)
    assert emp_rows[0]["pose_coverage"]["ready_90"] is True
    assert emp_rows[0]["n_emb"] == 7

    # 2. Nhân viên mới quay trọn bộ 7 bước liên tiếp (không bị loại nhầm bước ±76° dù trực giao với bước 0°)
    cam.pose = (0.0, 0.0)
    g7 = GuidedSession(ctx, "NV011", "Pham Van Moi", profile_90=True).start()
    assert g7.snapshot()["total"] == 7
    all_steps = STEPS + PROFILE_STEPS
    for i, st in enumerate(all_steps):
        cam.pose = pose_for(st)
        assert wait(lambda: g7.step > i or g7.state != "running"), f"ket buoc {i}: {g7.snapshot()}"
    assert wait(lambda: g7.state == "done"), g7.snapshot()
    assert g7.snapshot()["report"]["frames_selected"] == 7

    # 3. Đổi sang model_version mới -> backfill_legacy_embeddings tự động trích xuất lại từ ảnh lưu trong enroll/
    ctx.engine.model_version = "edgeface-upgraded-v2"
    assert ctx.db.list_employees("edgeface-upgraded-v2")[0]["n_emb"] == 0
    n_backfilled = backfill_legacy_embeddings(ctx)
    assert n_backfilled >= 2
    for row in ctx.db.list_employees("edgeface-upgraded-v2"):
        assert row["n_emb"] > 0


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
