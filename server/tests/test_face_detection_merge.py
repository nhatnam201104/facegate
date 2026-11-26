"""Kiem thu hoi quy cho US2 (feature 006): danh dau clipped ria ROI va hop nhat toan cuc IoU + bao long."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from server.config import load_config
from server.vision.context_detector import ContextScene
from server.vision.engines import Face, OpenCVEngine
from server.vision.worker_perception import detect_faces_two_stage_3mp

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_IMG = ROOT / "data" / "enroll" / "NV002" / "00_thang-giua.jpg"
YUNET_MODEL = ROOT / "server" / "models" / "face_detection_yunet_2023mar.onnx"


def _make_face(x1: float, y1: float, x2: float, y2: float, score: float = 0.9) -> Face:
    w, h = x2 - x1, y2 - y1
    kps = np.array([
        [x1 + 0.30 * w, y1 + 0.35 * h],
        [x1 + 0.70 * w, y1 + 0.35 * h],
        [x1 + 0.50 * w, y1 + 0.55 * h],
        [x1 + 0.35 * w, y1 + 0.75 * h],
        [x1 + 0.65 * w, y1 + 0.75 * h],
    ], dtype=np.float32)
    return Face(np.array([x1, y1, x2, y2], dtype=np.float32), score, kps)


def test_merge_two_people_clipped_fragment_dropped_exact_two_faces():
    """(a) Canh 2 nguoi: ROI cat ngang mat nguoi B tao manh 38px o ria -> hop nhat tra DUNG 2 mat."""
    frame = np.zeros((1296, 2304, 3), dtype=np.uint8)
    # Person A o [200, 200, 520, 1000] -> ROI rong ~400 (x1=160, y1=104, x2=560, y2=504)
    p_a = np.array([200, 200, 520, 1000], dtype=np.float32)

    class _Ctx:
        def detect(self, img):
            s = img.shape[1] / 2304.0
            return ContextScene(persons=[(p_a * s).astype(np.float32)], mediums=[])

    class _Eng:
        def detect(self, img):
            h, w = img.shape[:2]
            if w < 1000:
                # ROI cua A: thay mat A day du + manh cat 38px cua mat B cham sat ria phai ROI (x2 == w)
                return [
                    _make_face(140, 120, 250, 250, score=0.91),
                    _make_face(w - 38, 125, w, 245, score=0.581),
                ]
            # Fallback toan khung 1280px: thay du ca mat A va mat B (khong bi cat)
            s = w / 2304.0
            return [
                _make_face(300 * s, 224 * s, 410 * s, 354 * s, score=0.88),
                _make_face(522 * s, 229 * s, 632 * s, 354 * s, score=0.87),
            ]

    faces, _ = detect_faces_two_stage_3mp(
        frame, _Eng(), _Ctx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
        run_fallback=True,
    )
    assert len(faces) == 2
    widths = sorted(f.w for f in faces)
    assert widths[0] >= 95.0, f"Manh cat 38px van lot vao ket qua: {widths}"


def test_merge_clipped_candidate_ranked_after_unclipped():
    """(b) Ung vien cham ria noi bo ROI bi xep SAU ung vien day du ke ca khi diem cao hon."""
    frame = np.zeros((1296, 2304, 3), dtype=np.uint8)
    p_a = np.array([200, 200, 520, 1000], dtype=np.float32)

    class _Ctx:
        def detect(self, img):
            s = img.shape[1] / 2304.0
            return ContextScene(persons=[(p_a * s).astype(np.float32)], mediums=[])

    class _Eng:
        def detect(self, img):
            h, w = img.shape[:2]
            if w < 1000:
                # Manh cham ria phai ROI voi score cao hon (0.95) nhung bi cat
                return [_make_face(w - 60, 120, w - 1, 240, score=0.95)]
            s = w / 2304.0
            # Mat day du tu fallback voi score thap hon chut (0.85), bao trum manh cat
            return [_make_face(495 * s, 220 * s, 615 * s, 350 * s, score=0.85)]

    faces, _ = detect_faces_two_stage_3mp(
        frame, _Eng(), _Ctx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
        run_fallback=True,
    )
    assert len(faces) == 1
    assert faces[0].w >= 110.0, f"Phai giu mat day du thay vi manh cat: w={faces[0].w}"


def test_merge_containment_drops_small_fragment_inside_large_box():
    """(c) Hop nho nam >= 75% dien tich trong hop lon co uu tien cao hon -> bi loai du IoU < 0.45."""
    frame = np.zeros((1296, 2304, 3), dtype=np.uint8)
    p_a = np.array([200, 200, 520, 1000], dtype=np.float32)

    class _Ctx:
        def detect(self, img):
            s = img.shape[1] / 2304.0
            return ContextScene(persons=[(p_a * s).astype(np.float32)], mediums=[])

    class _Eng:
        def detect(self, img):
            h, w = img.shape[:2]
            if w < 1000:
                # Hop lon (120x140) va hop nho (40x120) nam tron ben trong hop lon (IoU ~0.28 < 0.45, containment=1.0)
                return [
                    _make_face(120, 100, 240, 240, score=0.93),
                    _make_face(195, 105, 235, 225, score=0.60),
                ]
            return []

    # Chay voi run_fallback=False de chung minh buoc hop nhat cung chay khi khong quet bu (T020)
    faces, _ = detect_faces_two_stage_3mp(
        frame, _Eng(), _Ctx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=False,
        run_fallback=False,
    )
    assert len(faces) == 1
    assert faces[0].w >= 115.0


def test_merge_keeps_two_close_side_by_side_faces_within_1_1_width():
    """(d) Hai khuon mat that co tam gan nhau 1.1x be rong trong cung ROI -> CA HAI duoc giu (bo luat tam 1.15x)."""
    frame = np.zeros((1296, 2304, 3), dtype=np.uint8)
    p_wide = np.array([200, 150, 750, 1100], dtype=np.float32)

    class _Ctx:
        def detect(self, img):
            s = img.shape[1] / 2304.0
            return ContextScene(persons=[(p_wide * s).astype(np.float32)], mediums=[])

    class _Eng:
        def detect(self, img):
            h, w = img.shape[:2]
            if w < 1000:
                # ROI thay nguoi A
                return [_make_face(150, 150, 250, 270, score=0.91)]
            # Fallback thay A va B cach nhau 1.1 * be rong (110 px), nam trong cung ROI
            s = w / 2304.0
            # p_wide: pw=550, side=688 -> roi x1=131, y1=36 -> trong fallback A o ~310..420, B o ~420..530
            return [
                _make_face(290 * s, 195 * s, 390 * s, 315 * s, score=0.89),
                _make_face(400 * s, 195 * s, 500 * s, 315 * s, score=0.88),
            ]

    faces_no_fb, _ = detect_faces_two_stage_3mp(
        frame, _Eng(), _Ctx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
        run_fallback=False,
    )
    faces_with_fb, _ = detect_faces_two_stage_3mp(
        frame, _Eng(), _Ctx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
        run_fallback=True,
    )
    assert len(faces_with_fb) == 2
    assert len(faces_with_fb) >= len(faces_no_fb)


def test_real_two_people_shoulder_to_shoulder_no_ghost_face():
    """Kich ban C (quickstart §4) tren anh that: 2 nguoi sat vai -> dung 2 khuon mat, khong co manh gia."""
    if not SAMPLE_IMG.exists() or not YUNET_MODEL.exists():
        pytest.skip("Thieu model YuNet hoac anh mau data/enroll/NV002/00_thang-giua.jpg")

    cfg = load_config()
    eng = OpenCVEngine(cfg["_paths"]["models"], det_score=float(cfg["pipeline"]["det_score"]), cfg=cfg)
    sample = cv2.imread(str(SAMPLE_IMG))
    assert sample is not None
    # Resize mau sao cho mat ~110px
    patch = cv2.resize(sample, (160, 160))
    frame = np.full((1296, 2304, 3), 120, dtype=np.uint8)
    # Dat 2 ban sao cach nhau 145 px theo truc X
    y0 = 260
    x_a = 400
    x_b = x_a + 145
    frame[y0:y0 + 160, x_a:x_a + 160] = patch
    frame[y0:y0 + 160, x_b:x_b + 160] = patch

    # Hop nguoi chi bao nguoi A, ROI cat ngang mat nguoi B o ria phai
    p_a = np.array([320, 260, 560, 960], dtype=np.float32)

    class _FixedCtx:
        def detect(self, img):
            s = img.shape[1] / 2304.0
            return ContextScene(persons=[(p_a * s).astype(np.float32)], mediums=[])

    faces, _ = detect_faces_two_stage_3mp(
        frame, eng, _FixedCtx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
        run_fallback=True,
    )
    assert len(faces) == 2, f"Ky vong dung 2 mat cho 2 nguoi sat vai, nhan {len(faces)}"


def test_clipped_small_fragment_dropped_on_non_fallback_cycle_and_does_not_mask_unmatched_person():
    """Hoi quy: manh cat < min_face o ria noi bo ROI khong lot o chu ky khong fallback va khong gia mao hop mat da khop nguoi."""
    frame = np.zeros((1296, 2304, 3), dtype=np.uint8)
    p_a = np.array([200, 200, 520, 1000], dtype=np.float32)
    p_b = np.array([480, 200, 740, 1000], dtype=np.float32)

    class _CtxOnlyA:
        def detect(self, img):
            return ContextScene(persons=[p_a], mediums=[])

    class _CtxBoth:
        def detect(self, img):
            return ContextScene(persons=[p_a, p_b], mediums=[])

    class _Eng:
        def __init__(self):
            self.roi_calls = 0
            self.fb_calls = 0

        def detect(self, img):
            h, w = img.shape[:2]
            if w >= 1000:
                self.fb_calls += 1
                s = w / 2304.0
                return [
                    _make_face(300 * s, 224 * s, 410 * s, 354 * s, score=0.88),
                    _make_face(522 * s, 229 * s, 632 * s, 354 * s, score=0.87),
                ]
            self.roi_calls += 1
            if self.roi_calls == 1:
                return [
                    _make_face(140, 120, 250, 250, score=0.91),
                    _make_face(w - 38, 125, w, 245, score=0.581),
                ]
            return []

    # 1. Chi co p_a o cycle_index=1 -> khong ep fallback (giu SC-003), va manh cat 38px bi loai
    eng_a = _Eng()
    faces_a, _ = detect_faces_two_stage_3mp(
        frame, eng_a, _CtxOnlyA(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
        cycle_index=1,
    )
    assert eng_a.fb_calls == 0
    assert len(faces_a) == 1
    assert faces_a[0].w >= 100.0

    # 2. Co ca p_a & p_b nhung ROI B miss -> manh cat 38px KHONG duoc tinh la khop p_b -> fallback chay ngay lay du 2 mat
    eng_ab = _Eng()
    faces_ab, _ = detect_faces_two_stage_3mp(
        frame, eng_ab, _CtxBoth(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
        cycle_index=1,
    )
    assert eng_ab.fb_calls == 1
    assert len(faces_ab) == 2
    assert min(f.w for f in faces_ab) >= 100.0

    # 3. Anh that YuNet o cu ly sat dx=120px (chi co p_a) o cycle_index=1 -> manh cat ~50px bi loai, khong gay quarantine oan
    if SAMPLE_IMG.exists() and YUNET_MODEL.exists():
        cfg = load_config()
        eng_real = OpenCVEngine(cfg["_paths"]["models"], det_score=float(cfg["pipeline"]["det_score"]), cfg=cfg)
        sample = cv2.imread(str(SAMPLE_IMG))
        assert sample is not None
        patch = cv2.resize(sample, (160, 160))
        fr3 = np.full((1296, 2304, 3), 120, dtype=np.uint8)
        fr3[260:420, 400:560] = patch
        fr3[260:420, 520:680] = patch
        p_real = np.array([320, 260, 560, 960], dtype=np.float32)

        class _RealCtxOnlyA:
            def detect(self, img):
                s = img.shape[1] / 2304.0
                return ContextScene(persons=[(p_real * s).astype(np.float32)], mediums=[])

        fs_c1, _ = detect_faces_two_stage_3mp(
            fr3, eng_real, _RealCtxOnlyA(),
            fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
            cycle_index=1, min_clipped_face=float(cfg["quality"]["min_face"]),
        )
        assert len(fs_c1) == 1
        assert fs_c1[0].w >= 80.0
