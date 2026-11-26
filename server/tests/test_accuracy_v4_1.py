"""Bo kiem thu hoi quy v4.1 (T033, T034): khac phuc 10 van de do chinh xac nhan dien & san sang ERP."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from server.config import load_config
from server.vision.context_detector import ContextScene
from server.vision.decision import LLRConfig, TrackDecisionAccumulator
from server.vision.engines import Face, OpenCVEngine, TEMPLATE_FRONTAL, align_face
from server.vision.pose3d import PoseResult, create_pose_estimator
from server.vision.quality import assess
from server.vision.tracker import IoUTracker
from server.vision.worker_perception import PerceptionWorker, detect_faces_two_stage_3mp


def _face(x1: float, y1: float, x2: float, y2: float) -> Face:
    kps = np.array(
        [
            [x1 + 18, y1 + 25],
            [x2 - 18, y1 + 25],
            [(x1 + x2) / 2, (y1 + y2) / 2 + 4],
            [x1 + 20, y2 - 20],
            [x2 - 20, y2 - 20],
        ],
        dtype=np.float32,
    )
    return Face(np.array([x1, y1, x2, y2], dtype=np.float32), 0.92, kps)


NV002_DIR = Path("data/enroll/NV002")
NV002_FILES = ("00_thang-giua.jpg", "01_trai-giua.jpg", "02_phai-giua.jpg", "03_thang-ngang.jpg",
               "04_thang-cui.jpg", "05_nghieng-sau-trai.jpg", "06_nghieng-sau-phai.jpg")


def _nv002_embedder():
    cfg = load_config()
    models_dir = cfg["_paths"]["models"]
    if not (models_dir / "face_recognition_sface_2021dec.onnx").exists() or not all(
            (NV002_DIR / f).exists() for f in NV002_FILES):
        pytest.skip("Thieu model hoac du lieu mau NV002")
    eng = OpenCVEngine(models_dir, det_score=0.55, cfg=cfg)
    pose_est = create_pose_estimator(cfg)

    def emb(fname):
        img = cv2.imread(str(NV002_DIR / fname))
        f = max(eng.detect(img), key=lambda x: x.w * x.h)
        p = pose_est.estimate(img, f)
        return eng.embed_aligned(eng.align(img, f, pose=p, pose_group=p.pose_group)), p

    return emb


def test_t033_align_by_pose_on_real_models_sface_pnp():
    """Feature 002: SFace + PnP 5 diem tren mau dang ky that NV002.

    - Cac buoc quay nhe / ngang (toi ~45 do): cos voi mau thang-giua phai >= 0.50 (cung nguong cu).
    - Mau cui dau (04_thang-cui, cui ~30-40 do): KHONG khang dinh nguong cos. So do 2026-11-19: cos 0.442, giong het
      khi bo pose (frontal dung recognizer.alignCrop tren kps YuNet) -> gioi han cua SFace voi pitch lon, khong phai
      loi can chinh. Tieu chi that cho goc kho: SC-006 (frr_by_yaw_band) tren replay K0.
    - Mau nghieng sau: so do 2026-11-18 cos 0.478 / 0.405 (EdgeFace cu dat >= 0.50). Runtime so mau nghieng voi
      gallery CUNG pose_group, khong voi mau chinh dien.
    """
    emb = _nv002_embedder()
    ef, pf = emb("00_thang-giua.jpg")
    assert pf.pose_group == "frontal" and pf.pose_reliable
    for fname in ("01_trai-giua.jpg", "02_phai-giua.jpg", "03_thang-ngang.jpg"):
        # Cac buoc quay/ngang duoc phep toi max_turn (~45 do) nen khong khang dinh |yaw| <= 40 o day
        e, _ = emb(fname)
        cos = float(e @ ef)
        assert cos >= 0.50, f"{fname} cos={cos:.3f} < 0.50"
    e, _ = emb("04_thang-cui.jpg")
    assert np.isfinite(float(e @ ef))
    e, p = emb("05_nghieng-sau-trai.jpg")
    assert p.pose_group == "left_profile", f"yaw={p.yaw:.1f}"
    assert np.isfinite(float(e @ ef))


@pytest.mark.xfail(strict=True, reason="PnP 5 diem uoc thieu yaw khi nghieng kem ngang dau: 06_nghieng-sau-phai "
                                       "(~45-55 do bang mat) chi ra 28.6 do -> nhom frontal (do 2026-11-19)")
def test_pnp_classifies_right_profile_sample_nv002():
    _, p = _nv002_embedder()("06_nghieng-sau-phai.jpg")
    assert p.pose_group == "right_profile", f"yaw={p.yaw:.1f}"


@pytest.mark.xfail(strict=True, reason="PnP 5 diem uoc thieu pitch: 04_thang-cui (cui ~30-40 do bang mat) "
                                       "chi ra ~4 do (do 2026-11-19)")
def test_pnp_detects_pitch_down_sample_nv002():
    _, p = _nv002_embedder()("04_thang-cui.jpg")
    assert abs(p.pitch) >= 15.0, f"pitch={p.pitch:.1f}"


def test_t033_single_gallery_baseline_and_unlock_before_commit():
    # Gallery 1 nhan vien: second_val dung single_gallery_baseline_llr (0.0) thay vi b_bias (-4.8)
    cfg = LLRConfig(
        a_cos=12.0,
        b_bias=-4.8,
        c_abs_yaw=-0.015,
        d_quality=0.60,
        tau_threshold=0.45,
        margin_base=0.70,
        margin_yaw_slope=0.30,
        min_samples=2,
        single_gallery_baseline_llr=0.0,
    )
    acc = TrackDecisionAccumulator(cfg=cfg)
    # cos=0.42 -> LLR ~ 0.78 >= 0.45 va margin = 0.78 - 0.0 >= 0.70 -> dat
    acc.observe(1000.0, yaw_deg=0.0, quality_score=0.9, sims_by_employee={1: 0.42})
    acc.observe(1300.0, yaw_deg=0.0, quality_score=0.9, sims_by_employee={1: 0.42})
    assert acc.locked_employee_id == 1

    # cos=0.40 -> LLR ~ 0.54 >= 0.45 NHUNG margin = 0.54 - 0.0 = 0.54 < 0.70 -> KHONG chot nham!
    acc_weak = TrackDecisionAccumulator(cfg=cfg)
    acc_weak.observe(1000.0, yaw_deg=0.0, quality_score=0.9, sims_by_employee={1: 0.40})
    acc_weak.observe(1300.0, yaw_deg=0.0, quality_score=0.9, sims_by_employee={1: 0.40})
    assert acc_weak.locked_employee_id is None

    # Neu da locked nhung cac mau moi truot xuong duoi nguong truoc khi COMMITTED -> go lock ve COLLECTING
    acc_drop = TrackDecisionAccumulator(cfg=cfg)
    acc_drop.observe(1000.0, yaw_deg=0.0, quality_score=0.9, sims_by_employee={1: 0.62, 2: 0.20})
    acc_drop.observe(1300.0, yaw_deg=0.0, quality_score=0.9, sims_by_employee={1: 0.62, 2: 0.20})
    assert acc_drop.locked_employee_id == 1 and acc_drop.state == "IDENTIFIED"

    # Sau khi da chot A (cos_mean=0.62), 2 mau moi tiep theo rot xuong duoi nguong -> cua so truot 2 mau moi go lock ngay
    acc_drop.observe(1600.0, yaw_deg=0.0, quality_score=0.9, sims_by_employee={1: 0.20, 2: 0.25})
    acc_drop.observe(1900.0, yaw_deg=0.0, quality_score=0.9, sims_by_employee={1: 0.18, 2: 0.26})
    assert acc_drop.locked_employee_id is None
    assert acc_drop.state == "COLLECTING"


def test_t033_worker_splits_track_on_jump_to_different_person(tmp_path):
    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "db": tmp_path / "t.db"}
    cfg["pipeline"]["two_stage_roi"] = False
    cfg["liveness"]["enabled"] = False

    # Fake worker khong can load model nang: test logic split_cosine khi B thay A trong < 2s
    worker = PerceptionWorker.__new__(PerceptionWorker)
    worker.cfg = cfg
    worker.llr_cfg = LLRConfig.from_cfg(cfg)
    worker.tracker = IoUTracker(max_age=2.0)
    worker.viewport_grace = 0.80
    worker.camera_id = "gate-1"
    worker.seq = 0
    worker.liveness = None
    worker.ctx_det = None
    worker.split_cos = float(cfg["recognition"]["split_cosine"])
    worker.unattached_confirm_frames = int(cfg["tracking"]["unattached_confirm_frames"])
    worker.require_frontal_for_checkin = False

    emb_a = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    emb_b = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)
    state_holder = {"emb": emb_a, "sims": {1: 0.80, 2: 0.10}, "x": 200.0}

    class _DummyEngine:
        model_version = "dummy_v41"

        def detect(self, img):
            x = state_holder["x"]
            return [_face(x, 120, x + 120, 260)]

        def align(self, img, face, pose=None, pose_group=None):
            return img

        def embed_batch(self, aligned_list):
            return [state_holder["emb"] for _ in aligned_list]

    class _DummyPose:
        def estimate(self, img, face, prev_group=None, **kw):
            return PoseResult(0.0, 0.0, 0.0, "frontal", np.zeros((68, 3), np.float32), face.kps, 1.0)

    class _DummyGallery:
        def all_employee_similarities(self, emb, pose_group=None):
            return state_holder["sims"]

        def get_person(self, eid):
            return {1: {"code": "NV001", "name": "A"}, 2: {"code": "NV002", "name": "B"}}.get(eid)

    worker.engine = _DummyEngine()
    worker.pose = _DummyPose()
    worker.gallery = _DummyGallery()

    # Tao frame co du texture de vuot min_blur
    rng = np.random.default_rng(7)
    frame = rng.integers(80, 180, (480, 640, 3), dtype=np.uint8)

    # A di qua luc t=1.0, 1.4 -> chot A
    worker.process_frame(frame, ts_ns=int(1.0 * 1e9))
    worker.process_frame(frame, ts_ns=int(1.4 * 1e9))
    assert len(worker.tracker.tracks) == 1
    tid_a = next(iter(worker.tracker.tracks))
    assert worker.tracker.tracks[tid_a].employee_id == 1

    # Trong vong 1.0s (< track_max_age 2.0s), B buoc vao gan vi tri cu -> _jump se noi nhung split_cosine PHAI tach track!
    state_holder["emb"] = emb_b
    state_holder["sims"] = {1: 0.05, 2: 0.82}
    state_holder["x"] = 250.0
    pkt, evs = worker.process_frame(frame, ts_ns=int(2.4 * 1e9))
    # Track cu cua A phai bi dong/tach, khong duoc gan emb_b cho A hoac checkin nham cho A
    assert any(o["track_id"] != tid_a for o in pkt["objects"])
    assert not any(e.get("event_kind") == "checkin" and e.get("employee_id") == 1 for e in evs)


def test_t034_two_stage_always_fallback_unmatched_finds_second_person():
    frame = np.zeros((1296, 2304, 3), dtype=np.uint8)
    p_a = np.array([100, 200, 450, 1100], dtype=np.float32)

    class _DummyCtx:
        def detect(self, small_img):
            s = small_img.shape[1] / 2304.0
            return ContextScene(persons=[(p_a * s).astype(np.float32)], mediums=[])

    class _DummyEngine:
        def detect(self, img):
            h, w = img.shape[:2]
            # Neu la crop ROI cua nguoi A -> chi thay mat A (nam ben trong ROI [0..570])
            if w < 1000:
                return [_face(80, 60, 200, 200)]
            # Quet fallback toan khung (1280px) -> thay ca mat A (cung vi tri ROI [136,152,256,292] +-4px) va mat B (ngoai ROI, bi YOLOX miss)
            s = w / 2304.0
            return [
                _face(140 * s, 156 * s, 260 * s, 296 * s),
                _face(1400 * s, 280 * s, 1540 * s, 430 * s),
            ]

    faces, _ = detect_faces_two_stage_3mp(
        frame, _DummyEngine(), _DummyCtx(), fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True
    )
    assert len(faces) == 2
    xs = sorted(float(f.bbox[0]) for f in faces)
    assert xs[1] > 1000.0


def test_t034_unattached_streak_liveness_min_frames_profile_frames_and_quality_roll():
    cfg = load_config()
    cfg["pipeline"]["two_stage_roi"] = False
    cfg["liveness"]["enabled"] = True
    cfg["liveness"]["min_frames"] = 4
    cfg["liveness"]["require_frontal_for_checkin"] = True
    cfg["recognition"]["min_profile_frames"] = 4
    cfg["tracking"]["unattached_confirm_frames"] = 4

    # 1. Kiem tra quality.assess noi max_roll khi |yaw| > 40 deg
    rng = np.random.default_rng(11)
    frame = rng.integers(80, 180, (480, 640, 3), dtype=np.uint8)
    f = _face(200, 140, 320, 280)
    pose_side = PoseResult(yaw=65.0, pitch=5.0, roll=42.0, pose_group="right_profile",
                           landmarks3d=np.zeros((68, 3), np.float32), kps5=f.kps, visibility=0.4)
    q_side = assess(frame, f, cfg["quality"], pose=pose_side)
    assert q_side.ok is True, f"Bi loai oan do roll o goc nghieng: {q_side.reason}"

    # 2. Kiem tra unattached_streak: miss than nguoi 2 frame khong khoa spoof vinh vien; 4 frame moi khoa
    worker = PerceptionWorker.__new__(PerceptionWorker)
    worker.cfg = cfg
    worker.llr_cfg = LLRConfig.from_cfg(cfg)
    worker.tracker = IoUTracker(max_age=2.0)
    worker.viewport_grace = 0.80
    worker.camera_id = "gate-1"
    worker.seq = 0
    worker.split_cos = 0.25
    worker.unattached_confirm_frames = 4
    worker.require_frontal_for_checkin = True

    class _DummyEngine:
        model_version = "dummy_v41"

        def detect(self, img):
            return [f]

        def align(self, img, face, pose=None, pose_group=None):
            return img

        def embed_batch(self, aligned_list):
            return [np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32) for _ in aligned_list]

    scene_state = {"has_person": False, "yaw": 5.0, "live": 0.95}

    class _DummyCtx:
        def detect(self, img):
            if scene_state["has_person"]:
                return ContextScene(persons=[np.array([150, 100, 380, 470], dtype=np.float32)], mediums=[])
            return ContextScene(persons=[], mediums=[])

    class _DummyPose:
        def estimate(self, img, face, prev_group=None, **kw):
            y = scene_state["yaw"]
            grp = "frontal" if abs(y) <= 40 else "right_profile"
            return PoseResult(y, 0.0, 0.0, grp, np.zeros((68, 3), np.float32), face.kps, 1.0)

    class _DummyLiveness:
        def real_prob(self, img, bbox):
            return scene_state["live"]

    class _DummyGallery:
        def all_employee_similarities(self, emb, pose_group=None):
            return {1: 0.78, 2: 0.15}

        def get_person(self, eid):
            return {1: {"code": "NV001", "name": "Test"}}.get(eid)

    worker.engine = _DummyEngine()
    worker.ctx_det = _DummyCtx()
    worker.pose = _DummyPose()
    worker.liveness = _DummyLiveness()
    worker.gallery = _DummyGallery()

    # Miss than nguoi 2 frame dau -> KHONG bi spoof vinh vien
    worker.process_frame(frame, ts_ns=int(1.0 * 1e9))
    worker.process_frame(frame, ts_ns=int(1.3 * 1e9))
    tid = next(iter(worker.tracker.tracks))
    assert worker.tracker.tracks[tid].spoof is False

    # Frame 3, 4 co lai than nguoi -> reset unattached_streak, va phai du 4 frame liveness moi COMMITTED!
    scene_state["has_person"] = True
    pkt3, ev3 = worker.process_frame(frame, ts_ns=int(1.6 * 1e9))
    assert not any(e.get("event_kind") == "checkin" for e in ev3)  # moi 3 frame live < min_frames(4)
    pkt4, ev4 = worker.process_frame(frame, ts_ns=int(1.9 * 1e9))
    assert any(e.get("event_kind") == "checkin" for e in ev4)

    # 3. Kiem tra miss lien tiep 4 frame -> bat spoof; va check-in thuan goc nghieng bi chan khi require_frontal_for_checkin=True
    worker.tracker = IoUTracker(max_age=2.0)
    scene_state["has_person"] = True
    scene_state["yaw"] = 68.0
    for k in range(5):
        _, ev_side = worker.process_frame(frame, ts_ns=int((5.0 + k * 0.35) * 1e9))
        assert not any(e.get("event_kind") == "checkin" for e in ev_side)

    worker.tracker = IoUTracker(max_age=2.0)
    scene_state["has_person"] = False
    scene_state["yaw"] = 5.0
    for k in range(4):
        worker.process_frame(frame, ts_ns=int((10.0 + k * 0.3) * 1e9))
    tid_sp = next(iter(worker.tracker.tracks))
    assert worker.tracker.tracks[tid_sp].spoof is True
    assert worker.tracker.tracks[tid_sp].spoof_reason == "unattached_face"


def test_review_fixes_decision_profile_window_and_two_stage_in_roi_second_person():
    # 1. Track co 1 frame dau luot qua |yaw|=39 deg roi 3 frame sau o 68 deg -> lan lock dau van phai xet tren toan bo mau,
    # va khong gan sai verification_tier='verified' neu mau da thu o goc nghieng hoac chua du min_profile_frames
    cfg = LLRConfig(
        a_cos=12.0,
        b_bias=-4.8,
        c_abs_yaw=-0.015,
        d_quality=0.60,
        tau_threshold=0.45,
        margin_base=0.70,
        margin_yaw_slope=0.30,
        min_samples=2,
        min_profile_frames=4,
        frontal_max_yaw=40.0,
        reverify_window=2,
    )
    acc = TrackDecisionAccumulator(cfg=cfg)
    # 2 mau dau o goc nghieng diem thap (chua du lock), 2 mau sau diem cao -> voi use_recent chi tinh 2 mau cuoi se lock som/sai
    acc.observe(1000.0, yaw_deg=68.0, quality_score=0.9, sims_by_employee={1: 0.20, 2: 0.10})
    acc.observe(1300.0, yaw_deg=68.0, quality_score=0.9, sims_by_employee={1: 0.20, 2: 0.10})
    acc.observe(1600.0, yaw_deg=68.0, quality_score=0.9, sims_by_employee={1: 0.65, 2: 0.10})
    assert acc.locked_employee_id is None  # moi 3 < min_profile_frames(4)
    acc.observe(1900.0, yaw_deg=68.0, quality_score=0.9, sims_by_employee={1: 0.65, 2: 0.10})
    # Trung binh 4 mau: (0.2+0.2+0.65+0.65)/4 = 0.425 -> LLR = 12*0.425 - 4.8 - 0.015*68 + 0.6*0.9 = -0.18 < 0.45 -> CHUA duoc lock!
    assert acc.locked_employee_id is None

    # 2. detect_faces_two_stage_3mp KHONG duoc bo sot nguoi thu 2 nam ben trong vung ROI rong cua nguoi thu 1
    frame = np.zeros((1296, 2304, 3), dtype=np.uint8)
    p_a = np.array([100, 150, 750, 1150], dtype=np.float32)

    class _DummyCtxOverlap:
        def detect(self, small_img):
            s = small_img.shape[1] / 2304.0
            return ContextScene(persons=[(p_a * s).astype(np.float32)], mediums=[])

    class _DummyEngineOverlap:
        def detect(self, img):
            h, w = img.shape[:2]
            if w < 1000:
                # Stage 1 ROI chi bat duoc nguoi A ben trai ROI
                return [_face(80, 60, 190, 190)]
            # Fallback toan khung thay ca A (cung vi tri voi ROI) va B (dung sat phia sau, van nam trong ROI cua p_a)
            s = w / 2304.0
            return [
                _face(102 * s, 94 * s, 212 * s, 224 * s),
                _face(520 * s, 230 * s, 630 * s, 360 * s),
            ]

    faces, _ = detect_faces_two_stage_3mp(
        frame, _DummyEngineOverlap(), _DummyCtxOverlap(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
    )
    assert len(faces) == 2


def test_review_fixes_backfill_reloads_gallery_and_pipeline_keeps_evidence(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from server.store.db import DB
    from server.enrollment.video import backfill_legacy_embeddings
    from server.enrollment.gallery import Gallery
    from server.vision.pipeline import Pipeline
    from server.vision.pose3d import PoseResult

    # 1. Kiem tra backfill + reload gallery va truyen spoof_reason qua _handle_worker_ipc
    db = DB(tmp_path / "bf.db")
    emp_id = db.create_employee("NV010", "Backfill User")
    db.set_status(emp_id, "active")
    enroll_dir = tmp_path / "enroll" / "NV010"
    enroll_dir.mkdir(parents=True)
    cv2.imwrite(str(enroll_dir / "00_thang-giua.jpg"), np.full((120, 120, 3), 120, np.uint8))

    class _BFEngine:
        name = "opencv"
        model_version = "opencv_v41_bf"

        def detect(self, img):
            return [_face(20, 20, 100, 100)]

        def align(self, img, face, pose=None, pose_group=None):
            return img

        def embed_aligned(self, aligned):
            return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)

    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "enroll": tmp_path / "enroll", "snapshots": tmp_path / "snap"}
    gal = Gallery(db, "opencv_v41_bf")
    assert gal.size == 0

    import threading
    bctx = SimpleNamespace(db=db, enroll_engine=_BFEngine(), enroll_lock=threading.Lock(), pose=None, cfg=cfg)
    migrated = backfill_legacy_embeddings(bctx)
    assert migrated == 1
    gal.reload()
    assert gal.size == 1

    # 2. Kiem tra Pipeline USB giu bang chung acc khi go lock tam thoi va khong check-in khi require_frontal=True
    class _DummyCam:
        def read(self):
            return 0, None

    class _DummyHRM:
        def notify(self):
            pass

    class _DummyPose:
        def estimate(self, img, face, prev_group=None, **kw):
            return PoseResult(0.0, 0.0, 0.0, "frontal", np.zeros((68, 3), np.float32), face.kps, 1.0)

    class _DummyLive:
        def real_prob(self, img, bbox):
            return 0.95

    sim_state = {"s": 0.72}

    class _DynGallery:
        version = 1

        def all_employee_similarities(self, emb, pose_group=None):
            return {emp_id: sim_state["s"], 999: 0.10}

        def get_person(self, eid):
            return {"code": "NV010", "name": "Backfill User"} if eid == emp_id else None

    class _PipeEngine(_BFEngine):
        def embed_aligned(self, aligned):
            return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)

    cfg["pipeline"]["two_stage_roi"] = False
    cfg["liveness"]["min_frames"] = 4
    pl = Pipeline(
        cfg, db, _PipeEngine(), _DynGallery(), _DummyCam(), _DummyHRM(),
        broadcast=lambda _m: None, liveness=_DummyLive(), clips=None,
        recognize=True, pose=_DummyPose(), context_detector=None,
    )
    rng = np.random.default_rng(9)
    frame = rng.integers(80, 180, (480, 640, 3), dtype=np.uint8)
    pl._process(frame, now=1.0)
    pl._process(frame, now=1.35)
    tid = next(iter(pl.tracker.tracks))
    tr = pl.tracker.tracks[tid]
    assert tr.employee_id == emp_id
    assert tr.decision_acc is not None and tr.decision_acc.sample_count == 2

    # Rot nguong tam thoi truoc khi du 4 frame liveness -> go lock nhung KHONG xoa sach decision_acc.sample_count
    sim_state["s"] = 0.20
    pl._process(frame, now=1.70)
    pl._process(frame, now=2.05)
    assert tr.employee_id is None
    assert tr.decision_acc.sample_count == 4
    assert asyncio is not None


def test_codereview_fixes_pipeline_context_spoof_legal_hold_and_worker_source(tmp_path):
    from server.config import _apply_v4_defaults
    from server.store.db import DB
    from server.enrollment.gallery import Gallery
    from server.vision.pipeline import Pipeline
    from server.vision.pose3d import PoseResult
    from server.vision.worker_perception import resolve_worker_source

    # 1. Pipeline single-process phai phat su kien spoof va dat legal_hold=1 cho screen_detected / unattached_face
    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "enroll": tmp_path / "enroll", "snapshots": tmp_path / "snap"}
    db = DB(tmp_path / "sp.db")
    gal = Gallery(db, "dummy_sp")
    broadcasted = []

    class _Cam:
        def read(self):
            return 0, None

    class _HRM:
        def notify(self):
            pass

    class _Eng:
        name = "opencv"
        model_version = "dummy_sp"

        def detect(self, img):
            return [_face(180, 120, 300, 260)]

        def align(self, img, face, pose=None, pose_group=None):
            return img

        def embed_aligned(self, aligned):
            return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)

    class _Pose:
        def estimate(self, img, face, prev_group=None, **kw):
            return PoseResult(0.0, 0.0, 0.0, "frontal", np.zeros((68, 3), np.float32), face.kps, 1.0)

    class _ScreenCtx:
        def detect(self, small_img):
            s = small_img.shape[1] / 640.0
            return ContextScene(
                persons=[np.array([120 * s, 80 * s, 360 * s, 460 * s], dtype=np.float32)],
                mediums=[(np.array([120 * s, 80 * s, 460 * s, 400 * s], dtype=np.float32), "cell phone", 0.9)],
            )

    cfg["pipeline"]["two_stage_roi"] = True
    cfg["pipeline"]["always_fallback_unmatched"] = False
    pl = Pipeline(
        cfg, db, _Eng(), gal, _Cam(), _HRM(),
        broadcast=broadcasted.append, liveness=None, clips=None,
        recognize=True, pose=_Pose(), context_detector=_ScreenCtx(),
    )
    rng = np.random.default_rng(17)
    frame = rng.integers(80, 180, (480, 640, 3), dtype=np.uint8)
    pl._process(frame, now=1.0)
    assert any(m.get("type") == "event" and m["event"]["kind"] == "spoof" for m in broadcasted)
    pl._end_all_tracks()
    evs = db.list_events(10)
    assert any(e["kind"] == "spoof" for e in evs)
    apps = db.list_appearances(0.0, 9999999999.0, kind="spoof")
    assert len(apps) == 1
    assert apps[0]["spoof_reason"] == "screen_detected"
    assert apps[0]["legal_hold"] == 1
    assert apps[0]["legal_hold_reason"] == "unreviewed_spoof"

    # 2. Cau hinh file video cuc bo hoac RTSP tuy chinh khong duoc bi main_source mac dinh ghi de nham
    cfg_file = _apply_v4_defaults({"camera": {"source": str(tmp_path / "gate_demo.mp4")}})
    assert cfg_file["pipeline"]["isolated_worker"] is False
    assert resolve_worker_source(cfg_file) == str(tmp_path / "gate_demo.mp4")

    cfg_rtsp = _apply_v4_defaults({"camera": {"source": "rtsp://10.0.0.5:554/stream1"}})
    assert cfg_rtsp["pipeline"]["isolated_worker"] is True
    assert resolve_worker_source(cfg_rtsp) == "rtsp://10.0.0.5:554/stream1"

    cfg_mtx = _apply_v4_defaults({
        "camera": {
            "source": "rtsp://127.0.0.1:8554/gate1_sub",
            "main_source": "rtsp://127.0.0.1:8554/gate1_main",
        }
    })
    assert resolve_worker_source(cfg_mtx) == "rtsp://127.0.0.1:8554/gate1_main"


def test_ceiling_sprinkler_false_spoof_blocked_and_static_suppression(tmp_path):
    """Hoi quy bug ceiling-sprinkler-false-spoof: chan unattached_face nho/score thap, chong lap vat tinh va uu tien t.spoof_reason."""
    from server.store.db import DB
    from server.enrollment.gallery import Gallery
    from server.vision.context_detector import StaticUnattachedSuppressor
    from server.vision.pipeline import Pipeline
    from server.vision.pose3d import PoseResult

    def _box_face(x1: float, y1: float, x2: float, y2: float, score: float = 0.92, lm_valid: bool = True) -> Face:
        w, h = x2 - x1, y2 - y1
        kps = np.array([
            [x1 + 0.30 * w, y1 + 0.35 * h],
            [x1 + 0.70 * w, y1 + 0.35 * h],
            [x1 + 0.50 * w, y1 + 0.55 * h],
            [x1 + 0.35 * w, y1 + 0.75 * h],
            [x1 + 0.65 * w, y1 + 0.75 * h],
        ], dtype=np.float32)
        return Face(np.array([x1, y1, x2, y2], dtype=np.float32), score, kps, landmarks_valid=lm_valid)

    cfg = load_config()
    cfg["_paths"] = {**cfg["_paths"], "enroll": tmp_path / "enroll", "snapshots": tmp_path / "snap"}
    cfg["pipeline"]["two_stage_roi"] = True
    cfg["pipeline"]["always_fallback_unmatched"] = True
    cfg["tracking"]["unattached_confirm_frames"] = 4
    cfg["tracking"]["unattached_min_det_score"] = 0.72
    cfg["tracking"]["unattached_static_cooldown_sec"] = 300.0
    cfg["tracking"]["unattached_static_iou"] = 0.65
    cfg["recognition"]["track_max_age"] = 2.0

    db = DB(tmp_path / "sprinkler.db")
    gal = Gallery(db, "dummy_sprinkler")
    broadcasted = []

    class _Cam:
        def read(self):
            return 0, None

    class _HRM:
        def notify(self):
            pass

    state = {"face": _box_face(506, 52, 522, 73, score=0.80), "persons": []}

    class _Eng:
        name = "opencv"
        model_version = "dummy_sprinkler"

        def detect(self, img):
            if img.shape[1] < 640:
                return []
            return [state["face"]] if state["face"] is not None else []

        def align(self, img, face, pose=None, pose_group=None):
            return img

        def embed_aligned(self, aligned):
            return np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)

        def embed_batch(self, aligned_list):
            return [np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32) for _ in aligned_list]

    class _Pose:
        def estimate(self, img, face, prev_group=None, **kw):
            return PoseResult(0.0, 0.0, 0.0, "frontal", np.zeros((68, 3), np.float32), face.kps, 1.0)

    class _Ctx:
        def detect(self, img):
            return ContextScene(persons=list(state["persons"]), mediums=[])

    rng = np.random.default_rng(42)
    frame = rng.integers(80, 180, (480, 640, 3), dtype=np.uint8)

    pl = Pipeline(
        cfg, db, _Eng(), gal, _Cam(), _HRM(),
        broadcast=broadcasted.append, liveness=None, clips=None,
        recognize=True, pose=_Pose(), context_detector=_Ctx(),
    )

    # 1. Voi chua chay ti hon tren tran (16x21 px < min_face=60) trong 6 frame lien tiep -> 0 spoof, 0 appearance
    for k in range(6):
        pl._process(frame, now=1.0 + k * 0.25)
    t_tiny = next(iter(pl.tracker.tracks.values()))
    assert t_tiny.unattached_streak == 0
    assert t_tiny.spoof is False
    pl._end_all_tracks()
    assert db.list_events(20) == []
    assert db.list_appearances(0.0, 9999999999.0) == []

    # 2. Hop lon (>=60px) nhung score thap (0.60 < unattached_min_det_score=0.72) -> khong tang unattached_streak
    state["face"] = _box_face(200, 80, 320, 220, score=0.60)
    for k in range(6):
        pl._process(frame, now=10.0 + k * 0.25)
    t_low = next(iter(pl.tracker.tracks.values()))
    assert t_low.unattached_streak == 0
    assert t_low.spoof is False
    pl._end_all_tracks()
    assert db.list_appearances(0.0, 9999999999.0, kind="spoof") == []

    # 3. Vat the lon hop le (120x140 px, score=0.92, khong co person): track dau phat dung 1 spoof;
    #    cac track tai sinh sau khi het han tai cung toa do tinh bi chan khong spam spoof/unknown/legal_hold
    state["face"] = _box_face(200, 80, 320, 220, score=0.92)
    for k in range(4):
        pl._process(frame, now=20.0 + k * 0.25)
    t_sp1 = next(iter(pl.tracker.tracks.values()))
    assert t_sp1.spoof is True
    assert t_sp1.spoof_reason == "unattached_face"
    # De track 1 het han tu nhien (> track_max_age=2.0s)
    state["face"] = None
    pl._process(frame, now=25.0)
    assert len(db.list_appearances(0.0, 9999999999.0, kind="spoof")) == 1

    # Track 2 & 3 tai sinh tai cung toa do (IoU >= 0.65) trong cua so cooldown -> khong phat them spoof hay unknown
    unknown_before = len(db.list_appearances(0.0, 9999999999.0, kind="unknown"))
    for base_t in (28.0, 36.0):
        state["face"] = _box_face(202, 81, 321, 221, score=0.92)
        for k in range(6):
            pl._process(frame, now=base_t + k * 0.25)
        t_rep = next(iter(pl.tracker.tracks.values()))
        assert t_rep.spoof is False
        assert t_rep.good_frames == 0
        assert t_rep.last_reason == "static_unattached_suppressed"
        state["face"] = None
        pl._process(frame, now=base_t + 5.0)

    assert len(db.list_appearances(0.0, 9999999999.0, kind="spoof")) == 1
    assert len(db.list_appearances(0.0, 9999999999.0, kind="unknown")) == unknown_before

    # Nguoi that dung vao dung toa do do (co body_coupled) KHONG bi chan boi static suppressor
    state["face"] = _box_face(202, 81, 321, 221, score=0.92)
    state["persons"] = [np.array([140, 40, 400, 460], dtype=np.float32)]
    pl._process(frame, now=44.0)
    t_real = next(iter(pl.tracker.tracks.values()))
    assert t_real.spoof is False
    assert t_real.good_frames == 1
    assert t_real.last_reason == "ok"
    pl._end_all_tracks()

    # 4. PerceptionWorker: chan nhieu nho, chan tai sinh vat tinh, va uu tien t.spoof_reason khi phat REJECTED_SPOOF
    state["persons"] = []
    worker = PerceptionWorker.__new__(PerceptionWorker)
    worker.cfg = cfg
    worker.engine = _Eng()
    worker.ctx_det = _Ctx()
    worker.pose = _Pose()
    worker.liveness = None
    worker.gallery = gal
    worker.tracker = IoUTracker(max_age=2.0)
    worker.llr_cfg = LLRConfig.from_cfg(cfg)
    worker.viewport_grace = 0.80
    worker.unattached_confirm_frames = 4
    worker.unattached_min_det_score = 0.72
    worker.static_unattached_suppressor = StaticUnattachedSuppressor(cooldown_sec=300.0, iou_thr=0.65)
    worker.split_cos = 0.25
    worker.require_frontal_for_checkin = True
    worker.camera_id = "gate-1"
    worker.seq = 0
    worker._cycle_index = 0
    worker._chime_events = []
    worker.mode = "attendance"

    # Nhieu nho tren worker -> 0 spoof, va khi het han khong tao unknown/clip hay ro ri track_timelines
    import server.main as main_mod
    from server.api.state import ctx as app_ctx

    old_ctx = {k: getattr(app_ctx, k, None) for k in ("cfg", "db", "pipeline", "engine", "track_timelines", "track_event_ids")}
    clip_calls = []
    orig_archive = main_mod._archive_playback_clip_bg
    try:
        app_ctx.cfg = cfg
        app_ctx.db = db
        app_ctx.pipeline = None
        app_ctx.engine = worker.engine
        app_ctx.track_timelines = {}
        app_ctx.track_event_ids = {}
        main_mod._archive_playback_clip_bg = lambda *a, **kw: clip_calls.append(a)

        unk_before_w = len(db.list_appearances(0.0, 9999999999.0, kind="unknown"))
        state["face"] = _box_face(506, 52, 522, 73, score=0.80)
        for k in range(6):
            pkt, evs = worker.process_frame(frame, ts_ns=int((50.0 + k * 0.25) * 1e9))
            assert not any(e.get("event_kind") == "spoof" for e in evs)
            main_mod._handle_worker_ipc(pkt)
            for ev in evs:
                main_mod._handle_worker_ipc(ev)
        # Cho track nho het han -> track_timelines duoc don sach, 0 unknown, 0 clip
        state["face"] = None
        pkt_exp, evs_exp = worker.process_frame(frame, ts_ns=int(55.0 * 1e9))
        assert any(e.get("event_kind") == "appearance_closed" and e.get("good_frames") == 0 for e in evs_exp)
        for ev in evs_exp:
            main_mod._handle_worker_ipc(ev)
        assert app_ctx.track_timelines == {}
        assert len(db.list_appearances(0.0, 9999999999.0, kind="unknown")) == unk_before_w
        assert clip_calls == []

        # Vat the tinh bi StaticUnattachedSuppressor chan tren worker khi het han cung khong tao unknown/clip
        state["face"] = _box_face(200, 80, 320, 220, score=0.92)
        for k in range(4):
            pkt, evs = worker.process_frame(frame, ts_ns=int((56.0 + k * 0.25) * 1e9))
            main_mod._handle_worker_ipc(pkt)
        state["face"] = None
        _, evs_sp_exp = worker.process_frame(frame, ts_ns=int(59.5 * 1e9))
        for ev in evs_sp_exp:
            main_mod._handle_worker_ipc(ev)
        sp_clips = len(clip_calls)

        state["face"] = _box_face(202, 81, 321, 221, score=0.92)
        for k in range(4):
            pkt, evs = worker.process_frame(frame, ts_ns=int((61.0 + k * 0.25) * 1e9))
            assert not any(e.get("event_kind") == "spoof" for e in evs)
            main_mod._handle_worker_ipc(pkt)
        state["face"] = None
        _, evs_sup_exp = worker.process_frame(frame, ts_ns=int(65.0 * 1e9))
        for ev in evs_sup_exp:
            main_mod._handle_worker_ipc(ev)
        assert app_ctx.track_timelines == {}
        assert len(db.list_appearances(0.0, 9999999999.0, kind="unknown")) == unk_before_w
        assert len(clip_calls) == sp_clips
    finally:
        main_mod._archive_playback_clip_bg = orig_archive
        for k, v in old_ctx.items():
            if v is None and hasattr(app_ctx, k):
                delattr(app_ctx, k)
            elif v is not None:
                setattr(app_ctx, k, v)

    # Uu tien t.spoof_reason ke ca khi ctx_eval["spoof_reason"] la None o frame phat su kien
    worker.tracker = IoUTracker(max_age=2.0)
    state["face"] = _box_face(120, 90, 240, 230, score=0.92)
    state["persons"] = [np.array([60, 40, 320, 460], dtype=np.float32)]
    _, _ = worker.process_frame(frame, ts_ns=int(70.0 * 1e9))
    tr_w = next(iter(worker.tracker.tracks.values()))
    tr_w.spoof = True
    tr_w.spoof_reason = "unattached_face"
    _, evs_prio = worker.process_frame(frame, ts_ns=int(70.3 * 1e9))
    sp_evs = [e for e in evs_prio if e.get("event_kind") == "spoof"]
    assert len(sp_evs) == 1
    assert sp_evs[0]["spoof_reason"] == "unattached_face"

