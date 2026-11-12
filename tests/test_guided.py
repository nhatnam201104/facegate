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
        # Mô phỏng đặc trưng cùng một người: góc nghiêng khác biệt nhưng vẫn chia sẻ thành phần danh tính (>= cross_pose_min_cosine)
        person = getattr(self.cam, "person", "A")
        yaw = self.cam.pose[0]
        if person == "B":
            if yaw < -40.0:
                v = np.array([0.0, 0.0, 0.0, 0.25, 1.0, 0.0, 0.0, 0.0], dtype=np.float32)
            elif yaw > 40.0:
                v = np.array([0.0, 0.0, 0.0, 0.25, 0.0, 1.0, 0.0, 0.0], dtype=np.float32)
            else:
                v = np.array([0.0, 0.0, 0.0, 1.0, 0.05, 0.05, 0.0, 0.0], dtype=np.float32)
        else:
            if yaw < -40.0:
                v = np.array([0.28, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
            elif yaw > 40.0:
                v = np.array([0.28, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
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
    assert gsup.snapshot()["total"] == 3
    sup_steps = [STEPS[0], *PROFILE_STEPS]
    for i, st in enumerate(sup_steps):
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


def test_supplement_only_verifies_frontal_identity_and_409_when_missing(env, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from app import main as app_main
    from app.guided import PROFILE_STEPS

    ctx, cam = env
    monkeypatch.setattr(app_main, "ctx", ctx)

    # 1. Nhân viên chưa tồn tại hoặc chưa có embedding frontal -> HTTP 409
    with pytest.raises(HTTPException) as exc_missing:
        asyncio.run(app_main.enroll_guided_start(code="NV_NO_FRONT", name="Chua Co Frontal", profile_90=True, supplement_only=True))
    assert exc_missing.value.status_code == 409

    # 2. Đăng ký nhân viên A đầy đủ và duyệt active
    cam.person = "A"
    g_a = GuidedSession(ctx, "NV_A", "Nhan Vien A", profile_90=True).start()
    for i, st in enumerate(STEPS + PROFILE_STEPS):
        cam.pose = pose_for(st)
        assert wait(lambda: g_a.step > i or g_a.state != "running"), g_a.snapshot()
    assert wait(lambda: g_a.state == "done")
    emp_a = ctx.db.get_employee_by_code("NV_A")
    ctx.db.set_status(emp_a["id"], "active")
    before_left = [
        r["vec"].copy()
        for r in ctx.db.load_gallery(ctx.engine.model_version, statuses=("active",))
        if r["code"] == "NV_A" and r["pose_group"] == "left_profile"
    ]
    assert len(before_left) == 1

    # 3. Người B mở phiên supplement_only với mã của A -> bị chặn ngay bước xác minh chính diện, dữ liệu A không đổi
    cam.person = "B"
    cam.pose = (0.0, 0.0)
    gsup_impostor = GuidedSession(ctx, "NV_A", "Nhan Vien A", profile_90=True, supplement_only=True).start()
    assert wait(lambda: gsup_impostor.state == "failed", timeout=4.0), gsup_impostor.snapshot()
    after_impostor = [
        r["vec"].copy()
        for r in ctx.db.load_gallery(ctx.engine.model_version, statuses=("active",))
        if r["code"] == "NV_A" and r["pose_group"] == "left_profile"
    ]
    assert np.allclose(before_left[0], after_impostor[0])
    assert ctx.db.get_employee_by_code("NV_A")["status"] == "active"

    # 4. Người A xác minh bước 0 đúng, nhưng ở bước nghiêng người B nhảy vào -> _commit chặn không cho sang bước tiếp
    cam.person = "A"
    cam.pose = (0.0, 0.0)
    gsup_swap = GuidedSession(ctx, "NV_A", "Nhan Vien A", profile_90=True, supplement_only=True).start()
    assert wait(lambda: gsup_swap.step == 1), gsup_swap.snapshot()
    cam.person = "B"
    cam.pose = pose_for(PROFILE_STEPS[0])
    assert wait(lambda: "không khớp" in gsup_swap.snapshot()["hint"].lower(), timeout=2.0), gsup_swap.snapshot()
    assert gsup_swap.step == 1
    # Đưa người A quay lại -> hoàn tất cả 2 góc nghiêng và giữ trạng thái active khi không có cảnh báo
    cam.person = "A"
    assert wait(lambda: gsup_swap.step == 2), gsup_swap.snapshot()
    cam.pose = pose_for(PROFILE_STEPS[1])
    assert wait(lambda: gsup_swap.state == "done"), gsup_swap.snapshot()
    assert ctx.db.get_employee_by_code("NV_A")["status"] == "active"

    # 5. Nếu có cảnh báo trùng mặt nhân viên khác ở góc nghiêng -> chuyển status về 'pending' để HR duyệt lại
    emp_other = ctx.db.create_employee("NV_DUP", "Nguoi Trung Goc Nghieng")
    ctx.db.set_status(emp_other, "active")
    v_dup_left = np.array([0.28, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
    v_dup_left /= np.linalg.norm(v_dup_left)
    ctx.db.add_embedding(emp_other, v_dup_left, ctx.engine.model_version, 0.9, "nghieng-sau-trai", None, pose_group="left_profile", yaw=-78.0)

    cam.person = "A"
    gsup_warn = GuidedSession(ctx, "NV_A", "Nhan Vien A", profile_90=True, supplement_only=True).start()
    for i, st in enumerate([STEPS[0], *PROFILE_STEPS]):
        cam.pose = pose_for(st)
        assert wait(lambda: gsup_warn.step > i or gsup_warn.state != "running"), gsup_warn.snapshot()
    assert wait(lambda: gsup_warn.state == "done"), gsup_warn.snapshot()
    assert gsup_warn.snapshot()["report"]["warnings"]
    assert ctx.db.get_employee_by_code("NV_A")["status"] == "pending"


def test_video_enrollment_enforces_cross_pose_consistency(env):
    from app.enrollment import EnrollError, process_enrollment

    ctx, cam = env
    frames = [cam.img.copy() for _ in range(6)]

    class SequenceEngine:
        model_version = "seq-v1"

        def __init__(self, seq):
            self.seq = list(seq)
            self.idx = 0
            self._cur = None

        def detect(self, img):
            yaw, person = self.seq[self.idx]
            self.idx += 1
            self._cur = (yaw, person)
            le, re = np.array([100.0, 100.0]), np.array([140.0, 100.0])
            nose = np.array([120.0 + yaw / 90.0 * 40.0, 122.0])
            kps = np.array([le, re, nose, [105, 140], [135, 140]], dtype=np.float32)
            return [Face(bbox=np.array([70.0, 60.0, 170.0, 180.0]), score=0.99, kps=kps)]

        def align(self, img, face, pose=None, pose_group=None):
            return img

        def embed_aligned(self, aligned):
            yaw, person = self._cur
            cam.pose = (yaw, 0.0)
            cam.person = person
            return FakeEngine(cam).embed_aligned(aligned)

    # Cùng người A ở cả frontal lẫn profile (kể cả nhóm profile chỉ có 1 frame) -> thành công
    ctx.engine = SequenceEngine([(0.0, "A"), (-15.0, "A"), (15.0, "A"), (0.0, "A"), (-78.0, "A"), (78.0, "A")])
    rep_ok = process_enrollment(frames, "NV_VID_OK", "Video Cung Nguoi", ctx)
    assert set(rep_ok["pose_groups"]) == {"frontal", "left_profile", "right_profile"}

    # 4 frame frontal của A + 2 frame đơn lẻ profile của B (33% bị loại < 35%, nhưng 2 frame của B phải bị loại sạch)
    ctx.engine = SequenceEngine([(0.0, "A"), (-15.0, "A"), (15.0, "A"), (0.0, "A"), (-78.0, "B"), (78.0, "B")])
    ctx.cfg["enrollment"]["min_frames"] = 4
    rep_filtered = process_enrollment(frames, "NV_VID_FILTER", "Video Loc Nguoi La", ctx)
    assert rep_filtered["pose_groups"] == ["frontal"]
    assert rep_filtered["outliers"] == 2

    # Tỉ lệ frame profile người lạ vượt ngưỡng 35% -> EnrollError
    ctx.engine = SequenceEngine([(0.0, "A"), (-15.0, "A"), (15.0, "A"), (-78.0, "B"), (-80.0, "B"), (78.0, "B")])
    with pytest.raises(EnrollError):
        process_enrollment(frames, "NV_VID_BAD", "Video Nhieu Nguoi", ctx)
