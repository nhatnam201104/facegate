"""Kiem thu hoi quy cho US3 (feature 006): FallbackGate quet bu co dieu kien va bo dem chu ky."""
from __future__ import annotations

import numpy as np

from server.vision.context_detector import ContextScene
from server.vision.engines import Face
from server.vision.worker_perception import detect_faces_two_stage_3mp


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


def _setup_single_matched_person():
    frame = np.zeros((1296, 2304, 3), dtype=np.uint8)
    # Person box [500, 200, 820, 1000] -> ROI [460, 104, 860, 504]
    p_a = np.array([500, 200, 820, 1000], dtype=np.float32)

    class _Ctx:
        def detect(self, img):
            s = img.shape[1] / 2304.0
            return ContextScene(persons=[(p_a * s).astype(np.float32)], mediums=[])

    fallback_calls: list[int] = []

    class _Eng:
        def detect(self, img):
            h, w = img.shape[:2]
            if w >= 1000:
                fallback_calls.append(w)
                s = w / 2304.0
                return [_make_face(610 * s, 260 * s, 710 * s, 380 * s)]
            # Trong ROI [460, 104, 860, 504], mat o [150, 156, 250, 276] -> frame [610, 260, 710, 380], khop p_a!
            return [_make_face(150, 156, 250, 276)]

    return frame, _Eng(), _Ctx(), fallback_calls


def test_fallback_runs_3_times_in_12_cycles_when_every_n_is_4():
    """(a) 12 chu ky, du mat cho moi hop nguoi, every_n=4 -> quet bu dung 3 lan (chu ky 0, 4, 8)."""
    frame, eng, ctx, fb_calls = _setup_single_matched_person()
    ran_cycles = []
    for cyc in range(12):
        before = len(fb_calls)
        faces, _ = detect_faces_two_stage_3mp(
            frame, eng, ctx,
            fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
            cycle_index=cyc, det_cfg={"fallback_every_n_cycles": 4},
        )
        assert len(faces) == 1
        if len(fb_calls) > before:
            ran_cycles.append(cyc)
    assert ran_cycles == [0, 4, 8]


def test_fallback_every_n_1_reproduces_legacy_every_frame():
    """(b) fallback_every_n_cycles=1 -> quet bu moi chu ky (tai lap hanh vi cu)."""
    frame, eng, ctx, fb_calls = _setup_single_matched_person()
    for cyc in range(12):
        detect_faces_two_stage_3mp(
            frame, eng, ctx,
            fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
            cycle_index=cyc, det_cfg={"fallback_every_n_cycles": 1},
        )
    assert len(fb_calls) == 12


def test_fallback_runs_immediately_on_empty_frame():
    """(c) Khung hinh trong (ROI khong thay mat) -> quet bu chay NGAY bat ke cycle_index."""
    frame = np.zeros((1296, 2304, 3), dtype=np.uint8)
    fb_calls: list[int] = []

    class _EmptyCtx:
        def detect(self, img):
            return ContextScene(persons=[], mediums=[])

    class _Eng:
        def detect(self, img):
            if img.shape[1] >= 1000:
                fb_calls.append(img.shape[1])
            return []

    detect_faces_two_stage_3mp(
        frame, _Eng(), _EmptyCtx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
        cycle_index=1, det_cfg={"fallback_every_n_cycles": 4},
    )
    assert len(fb_calls) == 1


def test_fallback_runs_immediately_when_unmatched_person_box_exists():
    """(d) Co hop nguoi chua gan mat -> quet bu chay NGAY ke ca cycle_index % every_n != 0."""
    frame = np.zeros((1296, 2304, 3), dtype=np.uint8)
    p1 = np.array([200, 200, 520, 1000], dtype=np.float32)
    p2 = np.array([1300, 200, 1620, 1000], dtype=np.float32)
    fb_calls: list[int] = []

    class _TwoPersonCtx:
        def detect(self, img):
            s = img.shape[1] / 2304.0
            return ContextScene(
                persons=[(p1 * s).astype(np.float32), (p2 * s).astype(np.float32)],
                mediums=[],
            )

    roi_call = {"n": 0}

    class _Eng:
        def detect(self, img):
            if img.shape[1] >= 1000:
                fb_calls.append(img.shape[1])
                s = img.shape[1] / 2304.0
                return [_make_face(1410 * s, 260 * s, 1510 * s, 380 * s)]
            roi_call["n"] += 1
            # Chi tra mat cho nguoi 1, nguoi 2 bi miss o stage 1
            if roi_call["n"] == 1:
                return [_make_face(150, 156, 250, 276)]
            return []

    faces, _ = detect_faces_two_stage_3mp(
        frame, _Eng(), _TwoPersonCtx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
        cycle_index=1, det_cfg={"fallback_every_n_cycles": 4},
    )
    assert len(fb_calls) == 1
    assert len(faces) == 2


def test_fallback_disabled_when_always_fallback_unmatched_is_false():
    """(e) always_fallback_unmatched=False -> khong quet bu lan nao."""
    frame, eng, ctx, fb_calls = _setup_single_matched_person()
    for cyc in range(8):
        detect_faces_two_stage_3mp(
            frame, eng, ctx,
            fallback_det_width=1280, max_faces=10, always_fallback_unmatched=False,
            cycle_index=cyc, det_cfg={"fallback_every_n_cycles": 4},
        )
    assert len(fb_calls) == 0


def test_fallback_width_clamped_to_source_frame_width():
    """FR-008: fallback_det_width=1280 tren camera 720p (1280) hoac 640x480 khong bao gio phong to khung."""
    small_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    seen_w: list[int] = []

    class _EmptyCtx:
        def detect(self, img):
            return ContextScene(persons=[], mediums=[])

    class _Eng:
        def detect(self, img):
            seen_w.append(img.shape[1])
            return []

    detect_faces_two_stage_3mp(
        small_frame, _Eng(), _EmptyCtx(),
        fallback_det_width=1280, max_faces=10, always_fallback_unmatched=True,
        run_fallback=True,
    )
    assert seen_w == [640]
