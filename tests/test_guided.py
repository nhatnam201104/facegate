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
    cam.pose = (0.0, 0.0)
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


def test_backfill_skips_no_face_validates_consistency_and_runs_in_background(env, monkeypatch):
    import asyncio
    import cv2
    from app import main as app_main
    from app.enrollment import backfill_legacy_embeddings

    ctx, cam = env
    enroll_root = ctx.cfg["_paths"]["enroll"]

    # 1. Nhân viên có 1 ảnh có mặt hợp lệ + 1 ảnh trắng không có mặt -> chỉ lưu đúng 1 embedding với điểm chất lượng thật (!= 0.75 cứng)
    emp1 = ctx.db.create_employee("NV_BF1", "Backfill Mot Mat")
    dir1 = enroll_root / "NV_BF1"
    dir1.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(dir1 / "00_thang-giua.jpg"), cam.img)
    blank_white = np.full((180, 180, 3), 255, dtype=np.uint8)
    cv2.imwrite(str(dir1 / "01_trai-giua.jpg"), blank_white)

    # 2. Nhân viên có ảnh không nhất quán (1 ảnh thẳng người A + 2 ảnh nghiêng người B) -> không lưu và ghi cảnh báo cần đăng ký lại
    emp2 = ctx.db.create_employee("NV_BF2", "Backfill Khong Nhat Quan")
    dir2 = enroll_root / "NV_BF2"
    dir2.mkdir(parents=True, exist_ok=True)
    img_a = cam.img.copy()
    img_b = cam.img.copy()
    img_b[:16, :16] = 10   # dấu nhận biết người B bền vững qua nén JPEG và đệm viền
    cv2.imwrite(str(dir2 / "00_thang-giua.jpg"), img_a)
    cv2.imwrite(str(dir2 / "01_nghieng-sau-trai.jpg"), img_b)
    cv2.imwrite(str(dir2 / "02_nghieng-sau-phai.jpg"), img_b)

    class BackfillEngine:
        name = "edgeface"
        model_version = "bf-model-v2"

        def detect(self, img):
            if float(img.mean()) > 250.0:
                return []
            is_b = float(img[:8, :8].mean()) < 25.0
            yaw = -78.0 if is_b else 0.0
            self._cur = (yaw, "B" if is_b else "A")
            h, w = img.shape[:2]
            cx, cy = w * 0.5, h * 0.5
            le, re = np.array([cx - 22.0, cy - 20.0]), np.array([cx + 22.0, cy - 20.0])
            nose = np.array([cx + yaw / 90.0 * 28.0, cy + 4.0])
            kps = np.array([le, re, nose, [cx - 16.0, cy + 26.0], [cx + 16.0, cy + 26.0]], dtype=np.float32)
            return [Face(bbox=np.array([cx - 48.0, cy - 55.0, cx + 48.0, cy + 55.0]), score=0.98, kps=kps)]

        def align(self, img, face, pose=None, pose_group=None):
            return img

        def embed_aligned(self, aligned):
            yaw, person = self._cur
            cam.pose = (yaw, 0.0)
            cam.person = person
            return FakeEngine(cam).embed_aligned(aligned)

    ctx.engine = BackfillEngine()
    migrated = backfill_legacy_embeddings(ctx)
    assert migrated == 1
    rows = {r["code"]: r for r in ctx.db.list_employees("bf-model-v2")}
    assert rows["NV_BF1"]["n_emb"] == 1
    q_stored = ctx.db.q("SELECT quality FROM embeddings WHERE employee_id=?", (emp1,))[0]["quality"]
    assert q_stored != 0.75

    assert rows["NV_BF2"]["n_emb"] == 0
    rep2 = ctx.db.q("SELECT report FROM enrollments WHERE employee_id=? ORDER BY id DESC LIMIT 1", (emp2,))
    assert rep2 and "đăng ký lại" in rep2[0]["report"].lower()

    # 3. Kiểm tra lifespan trả quyền ngay trước khi tác vụ backfill nền hoàn tất
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_backfill(c):
        started.set()
        release.wait(timeout=3.0)
        finished.set()
        return 1

    class DummyLifecycleObj:
        def start(self):
            return self
        def stop(self):
            pass
        def close(self):
            pass

    dummy_pipe = SimpleNamespace(
        start=lambda: dummy_pipe,
        stop=lambda: None,
        set_motion_zone=lambda z: None,
        engine_lock=threading.Lock(),
        threshold=0.4,
    )
    monkeypatch.setattr(app_main, "load_config", lambda: ctx.cfg)
    monkeypatch.setattr(app_main, "DB", lambda p: ctx.db)
    monkeypatch.setattr(app_main, "create_engine", lambda c: ctx.engine)
    monkeypatch.setattr(app_main, "PoseEstimator3D", lambda *a, **k: None)
    monkeypatch.setattr(app_main, "create_context_detector", lambda c: None)
    monkeypatch.setattr(app_main, "backfill_legacy_embeddings", slow_backfill)
    monkeypatch.setattr(app_main, "Gallery", lambda db, mv: SimpleNamespace(reload=lambda: None, version=1))
    monkeypatch.setattr(app_main, "Camera", lambda *a, **k: DummyLifecycleObj())
    monkeypatch.setattr(app_main, "HRMWorker", lambda *a, **k: DummyLifecycleObj())
    monkeypatch.setattr(app_main, "ClipRecorder", lambda c: DummyLifecycleObj())
    monkeypatch.setattr(app_main, "create_liveness", lambda c: None)
    monkeypatch.setattr(app_main, "Pipeline", lambda *a, **k: dummy_pipe)

    async def _check_lifespan_nonblocking():
        async with app_main.lifespan(app_main.app):
            assert not finished.is_set(), "lifespan bị chặn đồng bộ bởi backfill_legacy_embeddings"
            release.set()
            await asyncio.to_thread(finished.wait, 2.0)
            assert finished.is_set()

    asyncio.run(_check_lifespan_nonblocking())


def test_deep_profile_step_passes_when_profile_template_shifts_embedding_space(env):
    """Mô phỏng thực tế EdgeFace-XS: align(pose_group='right_profile') dùng TEMPLATE_RIGHT_PROFILE
    nên vector đầu ra gần trực giao với front_emb (~0.09 < cross_pose_min_cosine), trong khi
    vector căn chỉnh về cùng hệ quy chiếu 'frontal' giữ được đặc trưng danh tính của cùng 1 người."""
    from app.guided import PROFILE_STEPS

    ctx, cam = env

    class TemplateAwareEngine:
        name = "edgeface"
        model_version = "tpl-shift-v1"

        def detect(self, img):
            yaw, pitch = cam.pose
            le, re = np.array([100.0, 100.0]), np.array([140.0, 100.0])
            nose = np.array([120.0 + yaw / 90.0 * 40.0, 100.0 + (pitch / 100.0 + 0.55) * 40.0])
            kps = np.array([le, re, nose, [105, 140], [135, 140]], dtype=np.float32)
            return [Face(bbox=np.array([70.0, 60.0, 170.0, 180.0]), score=0.99, kps=kps)]

        def align(self, img, face, pose=None, pose_group=None):
            grp = pose_group or "frontal"
            tag = np.zeros((112, 112, 3), dtype=np.uint8)
            tag[0, 0, 0] = {"frontal": 1, "left_profile": 2, "right_profile": 3}[grp]
            return tag

        def embed_aligned(self, aligned):
            tpl_id = int(aligned[0, 0, 0])
            person = getattr(cam, "person", "A")
            if person == "B":
                # Người B khác hoàn toàn ở mọi hệ quy chiếu
                v = np.array([0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
                if tpl_id == 2:
                    v = np.array([0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0], dtype=np.float32)
                elif tpl_id == 3:
                    v = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0], dtype=np.float32)
            else:
                if tpl_id == 1:
                    # Cùng hệ quy chiếu TEMPLATE_FRONTAL: ở góc ~80° của cùng người A vẫn đạt ~0.48
                    yaw = abs(cam.pose[0])
                    v = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32) if yaw < 45 else np.array([0.48, 0.87, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
                elif tpl_id == 2:
                    # Hệ quy chiếu TEMPLATE_LEFT_PROFILE chỉ đạt cosine ~0.09 với front_emb
                    v = np.array([0.09, 0.995, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
                else:
                    # Hệ quy chiếu TEMPLATE_RIGHT_PROFILE (bước 7/7) chỉ đạt cosine ~0.08 với front_emb
                    v = np.array([0.08, 0.0, 0.996, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
            return v / np.linalg.norm(v)

    ctx.engine = TemplateAwareEngine()
    cam.person = "A"
    g7 = GuidedSession(ctx, "NV_REAL7", "Nguoi That Quay 7 Buoc", profile_90=True).start()
    for i, st in enumerate(STEPS + PROFILE_STEPS):
        cam.pose = pose_for(st)
        assert wait(lambda: g7.step > i or g7.state != "running", timeout=3.0), (
            f"Bị kẹt ở bước {i + 1}/7 ({st.bin}): {g7.snapshot()}"
        )
    assert wait(lambda: g7.state == "done", timeout=3.0), g7.snapshot()
    # Vector lưu vào gallery ở nhóm profile vẫn phải là vector của TEMPLATE_PROFILE (không lưu nhầm vector frontal)
    prof_rows = [
        r for r in ctx.db.load_gallery(ctx.engine.model_version, statuses=("pending", "active"))
        if r["code"] == "NV_REAL7" and r["pose_group"] == "right_profile"
    ]
    assert len(prof_rows) == 1 and float(prof_rows[0]["vec"][2]) > 0.95

    # Đồng thời kẻ mạo danh B nhảy vào bước 7/7 vẫn phải bị chặn
    cam.person = "A"
    g_imp = GuidedSession(ctx, "NV_IMP7", "Chan Nguoi La Buoc 7", profile_90=True).start()
    for i, st in enumerate(STEPS + [PROFILE_STEPS[0]]):
        cam.pose = pose_for(st)
        assert wait(lambda: g_imp.step > i or g_imp.state != "running", timeout=3.0)
    cam.person = "B"
    cam.pose = pose_for(PROFILE_STEPS[1])
    assert wait(lambda: "không khớp" in g_imp.snapshot()["hint"].lower(), timeout=2.0), g_imp.snapshot()
    assert g_imp.step == 6
    g_imp.cancel()
    assert wait(lambda: g_imp.state == "cancelled")


def test_supplement_cleans_old_profile_crops_keeps_active_name_and_backfill_idempotent_with_stop_event(env):
    import cv2
    from app.enrollment import backfill_legacy_embeddings, store_enrollment
    from app.quality import Quality

    ctx, _ = env
    mv = ctx.engine.model_version
    u = lambda v: np.array(v, np.float32) / np.linalg.norm(v)
    q_f = Quality(90, 120, 120, 0.0, 0.0, True, "ok", 0.9, 0.0, "frontal")
    q_l = Quality(90, 120, 120, -80.0, 0.0, True, "ok", 0.82, 0.0, "left_profile")
    crop_img = np.full((80, 80, 3), 120, dtype=np.uint8)

    # 1. Đăng ký nhân viên NV_SUP, duyệt active rồi chạy supplement_only 2 lần liên tiếp với tên khác
    store_enrollment(
        [{"emb": u([1, 0, 0, 0, 0, 0, 0, 0]), "q": q_f, "bin": "thang-giua", "pose_group": "frontal", "crop": crop_img}],
        "NV_SUP", "Tên Chính Thức HR Duyệt", ctx, {"frames_total": 1, "no_face": 0, "multi_face": 0, "rejected": {}},
        n_good=1, n_outliers=0, bin_names=["thang-giua"], guided=True, supplement_only=False,
    )
    emp = ctx.db.get_employee_by_code("NV_SUP")
    ctx.db.set_status(emp["id"], "active")

    for k in range(2):
        store_enrollment(
            [{"emb": u([0, 1, 0.1 * k, 0, 0, 0, 0, 0]), "q": q_l, "bin": "nghieng-sau-trai", "pose_group": "left_profile", "crop": crop_img}],
            "NV_SUP", f"Tên Tùy Ý Lần {k}", ctx, {"frames_total": 1, "no_face": 0, "multi_face": 0, "rejected": {}},
            n_good=1, n_outliers=0, bin_names=["nghieng-sau-trai"], guided=True, supplement_only=True,
        )

    emp_after = ctx.db.get_employee_by_code("NV_SUP")
    assert emp_after["status"] == "active"
    assert emp_after["name"] == "Tên Chính Thức HR Duyệt", "supplement_only không được ghi đè tên nhân viên đang active"

    emp_dir = ctx.cfg["_paths"]["enroll"] / "NV_SUP"
    disk_files = sorted(f"enroll/NV_SUP/{p.name}" for p in emp_dir.glob("*.jpg"))
    db_files = sorted(r["crop_path"] for r in ctx.db.q("SELECT crop_path FROM embeddings WHERE employee_id=?", (emp["id"],)))
    assert disk_files == db_files, f"Còn sót file ảnh .jpg mồ côi trên đĩa: disk={disk_files} vs db={db_files}"

    # 2. Backfill thất bại không được chèn lặp cảnh báo mỗi lần restart và không xóa trắng crops của báo cáo gốc
    emp_err = ctx.db.create_employee("NV_BF_FAIL", "Nhân Viên Ảnh Lỗi")
    ctx.db.add_enrollment(emp_err, {
        "frames_total": 5, "frames_good": 5, "frames_selected": 5,
        "bins": {"thang-giua": 1}, "crops": ["enroll/NV_BF_FAIL/00_thang-giua.jpg"],
        "warnings": [], "model_version": "old-model-v0",
    })
    err_dir = ctx.cfg["_paths"]["enroll"] / "NV_BF_FAIL"
    err_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(err_dir / "00_thang-giua.jpg"), np.full((90, 90, 3), 255, dtype=np.uint8))

    class NoFaceEngine:
        name = "edgeface"
        model_version = "new-model-v3"

        def detect(self, img):
            return []

    ctx.engine = NoFaceEngine()
    backfill_legacy_embeddings(ctx)
    cnt_after_first = len(ctx.db.q("SELECT id FROM enrollments WHERE employee_id=?", (emp_err,)))
    latest_rep = [r for r in ctx.db.list_employees("new-model-v3") if r["code"] == "NV_BF_FAIL"][0]["report"]
    import json
    rep_obj = json.loads(latest_rep) if isinstance(latest_rep, str) else latest_rep
    assert rep_obj["crops"] == ["enroll/NV_BF_FAIL/00_thang-giua.jpg"], "Không được làm mất danh sách crops của báo cáo gốc"

    # Gọi lần 2 (mô phỏng restart server): không được chạy lặp và không phình bảng enrollments
    backfill_legacy_embeddings(ctx)
    cnt_after_second = len(ctx.db.q("SELECT id FROM enrollments WHERE employee_id=?", (emp_err,)))
    assert cnt_after_second == cnt_after_first, "Backfill thất bại bị ghi lặp thêm dòng vào enrollments mỗi lần restart"

    # 3. Tôn trọng stop_event khi tắt server
    stop_ev = threading.Event()
    stop_ev.set()
    assert backfill_legacy_embeddings(ctx, stop_event=stop_ev) == 0
