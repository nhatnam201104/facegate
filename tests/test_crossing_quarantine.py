from __future__ import annotations

from datetime import datetime
import numpy as np

from app.context_detector import ContextScene, evaluate_face_context
from app.decision import LLRConfig, TrackDecisionAccumulator
from app.engines import Face
from app.tracker import IoUTracker
from app.worker_perception import (
    compute_head_shoulder_rois_1to1,
    detect_faces_two_stage_3mp,
    is_in_active_shift_window,
)


def _face(x1: float, y1: float, x2: float, y2: float) -> Face:
    kps = np.array([[x1 + 10, y1 + 15], [x2 - 10, y1 + 15], [(x1 + x2) / 2, (y1 + y2) / 2],
                    [x1 + 12, y2 - 12], [x2 - 12, y2 - 12]], dtype=np.float32)
    return Face(np.array([x1, y1, x2, y2], dtype=np.float32), 0.92, kps)


def test_uniform_crossing_quarantine_prevents_id_inheritance():
    trk = IoUTracker(crossing_face_iou=0.25, crossing_person_iou=0.40)
    cfg = LLRConfig(min_samples=2, novelty_min_dt_ms=200.0)

    # Frame 1 & 2: Nhan vien A (ben trai) va Cong nhan B mac cung dong phuc (ben phai) di xa nhau
    p_a = np.array([100, 100, 300, 700], np.float32)
    p_b = np.array([600, 100, 800, 700], np.float32)
    matched, _ = trk.update([_face(160, 120, 240, 220), _face(660, 120, 740, 220)], now=1.0, person_boxes=[p_a, p_b])
    ta, tb = matched[0][0], matched[1][0]
    ta.decision_acc = TrackDecisionAccumulator(cfg=cfg)
    tb.decision_acc = TrackDecisionAccumulator(cfg=cfg)

    ta.decision_acc.observe(1000.0, 0.0, 0.9, {101: 0.72, 202: 0.15})
    ta.decision_acc.observe(1300.0, 0.0, 0.9, {101: 0.74, 202: 0.14})
    ta.employee_id, ta.code = 101, "NV101"
    assert ta.decision_acc.locked_employee_id == 101

    # Frame 2: Hai nguoi tien lai gan giua khung hinh voi buoc di tu nhien
    p_mid1 = np.array([220, 100, 420, 700], np.float32)
    p_mid2 = np.array([480, 100, 680, 700], np.float32)
    trk.update([_face(280, 120, 360, 220), _face(540, 120, 620, 220)], now=1.3, person_boxes=[p_mid1, p_mid2])

    # Frame 3: Hai nguoi di luot cheo nhau (hop co the giao cat IoU > 0.40) -> kich hoat Crossing Quarantine!
    p_cross1 = np.array([340, 100, 540, 700], np.float32)
    p_cross2 = np.array([370, 100, 570, 700], np.float32)
    trk.update([_face(400, 120, 480, 220), _face(430, 120, 510, 220)], now=1.6, person_boxes=[p_cross1, p_cross2])
    assert ta.quarantine is True and tb.quarantine is True
    assert ta.employee_id is None
    assert ta.decision_acc.locked_employee_id is None
    # Trong luc dang giao cat, mau thu duoc bi cam nap vao bo tich luy!
    assert ta.decision_acc.observe(1600.0, 0.0, 0.9, {101: 0.80, 202: 0.10}) is False

    # Frame 4: Tach khoi vung giao cat voi buoc di tu nhien, nhung van giu co quarantine bat xac thuc lai tu dau
    p_sep1 = np.array([490, 100, 690, 700], np.float32)
    p_sep2 = np.array([210, 100, 410, 700], np.float32)
    trk.update([_face(550, 120, 630, 220), _face(270, 120, 350, 220)], now=1.9, person_boxes=[p_sep1, p_sep2])
    assert ta.crossing_active is False
    # Chi moi co 1 frame sau khi tach -> chua duoc chot!
    ta.decision_acc.observe(2000.0, 0.0, 0.9, {202: 0.75, 101: 0.12})
    assert ta.decision_acc.locked_employee_id is None
    # Du 2 frame doc lap sau khi tach -> chot dung danh tinh that 202, KHONG bi ke thua nham 101!
    ta.decision_acc.observe(2300.0, 0.0, 0.9, {202: 0.76, 101: 0.11})
    assert ta.decision_acc.locked_employee_id == 202


def test_viewport_aware_truncation_and_3mp_1to1_roi_and_shift_schedule():
    scene = ContextScene(persons=[], mediums=[])
    # Khuon mat cham sat day khung hinh 1296px (y2 = 1100 >= 0.80 * 1296 = 1036.8) -> mien phat unattached_face!
    res_bottom = evaluate_face_context(np.array([900, 950, 1050, 1100], np.float32), scene, frame_height=1296)
    assert res_bottom["spoof_reason"] is None and res_bottom["viewport_truncated"] is True

    # Khuon mat lo lung cao giua khung hinh (y2 = 450 < 0.80 * 1296) khong co than -> phat unattached_face!
    res_floating = evaluate_face_context(np.array([900, 300, 1050, 450], np.float32), scene, frame_height=1296)
    assert res_floating["spoof_reason"] == "unattached_face"

    # Kiem tra cat ROI 1:1 tren khung 3MP (2304x1296) giu nguyen do phan giai goc khong bi thu ve 960px (H33, H35)
    frame_3mp = np.zeros((1296, 2304, 3), dtype=np.uint8)
    pbox = np.array([1000, 200, 1500, 1200], dtype=np.float32)
    rois = compute_head_shoulder_rois_1to1((1296, 2304), [pbox])
    assert len(rois) == 1
    rx1, ry1, rx2, ry2 = rois[0]
    assert (rx2 - rx1) >= 500  # Giu nguyen kich thuoc pixel 1:1 tren anh 3MP!

    seen_crop_widths: list[int] = []

    class _DummyEngine:
        def detect(self, img):
            seen_crop_widths.append(img.shape[1])
            return [_face(100, 80, 190, 190)]

    class _DummyCtx:
        def detect(self, small_img):
            # Nhan vao anh 640px thu nho tu chinh frame 3MP
            scale = 640.0 / 2304.0
            return ContextScene(persons=[(pbox * scale).astype(np.float32)], mediums=[])

    faces, _ = detect_faces_two_stage_3mp(frame_3mp, _DummyEngine(), _DummyCtx())
    assert len(faces) == 1
    # Chung minh mat 90px tren crop 3MP giu nguyen be ngang 90px tren toa do goc (khong bi bop xuong 37px nhu det_width=960)
    assert abs(faces[0].w - 90.0) < 1.0
    assert seen_crop_widths[0] == (rx2 - rx1)

    # Kiem tra Lich thuc theo ca (H34)
    wins = [["06:30", "09:30"], ["16:30", "18:30"]]
    assert is_in_active_shift_window(wins, datetime(2026, 11, 13, 7, 55)) is True
    assert is_in_active_shift_window(wins, datetime(2026, 11, 13, 13, 15)) is False
