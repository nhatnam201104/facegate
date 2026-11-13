"""IoU tracker don gian - du cho toi da vai khuon mat o cong.

Moi track gom embedding cac frame tot, chot danh tinh khi du bang chung.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .engines import Face


def iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / ua) if ua > 0 else 0.0


@dataclass
class Track:
    id: int
    bbox: np.ndarray
    first_seen: float
    last_seen: float
    hits: int = 1
    embs: list = field(default_factory=list)       # (emb, weight)
    good_frames: int = 0
    last_reason: str = ""
    # danh tinh
    employee_id: int | None = None
    code: str | None = None
    name: str | None = None
    score: float = 0.0
    margin: float = 0.0
    confirmed_at: float | None = None
    best_score: float = -1.0                        # match tot nhat (ke ca chua dat)
    # su kien
    event_done: bool = False
    pending_direction: str | None = None
    side: int | None = None
    best_quality: float = -1.0
    best_crop: np.ndarray | None = None
    # chong gia mao
    live: list = field(default_factory=list)        # xac suat nguoi that cac frame gan nhat
    spoof: bool = False                             # da ket luan gia mao (giu nguyen den het track)
    claimed: tuple | None = None                    # (employee_id, code, name) khop gan nhat - de ghi log gia mao
    # lich su & crossing quarantine (H3)
    vel: tuple = (0.0, 0.0)                          # van toc tam mat (px/giay), de du doan vi tri
    person_bbox: np.ndarray | None = None
    quarantine: bool = False                         # dang hoac vua giao cat -> cam chot check-in cho toi khi vuot lai Open-Set
    crossing_active: bool = False
    verification_tier: str | None = None
    pose_group: str = "frontal"
    last_yaw: float = 0.0
    min_abs_yaw: float = 999.0
    decision_acc: object | None = None
    clip_sid: int | None = None
    snapshot: str | None = None
    event_id: int | None = None

    @property
    def center(self) -> tuple[float, float]:
        return float((self.bbox[0] + self.bbox[2]) / 2), float((self.bbox[1] + self.bbox[3]) / 2)

    @property
    def live_mean(self) -> float | None:
        return float(np.mean(self.live)) if self.live else None

    def reset_identity(self) -> None:
        self.employee_id = self.code = self.name = self.confirmed_at = None
        self.verification_tier = None
        self.score = self.margin = 0.0
        self.embs.clear()
        self.good_frames = 0
        if self.decision_acc is not None and hasattr(self.decision_acc, "reset_evidence"):
            self.decision_acc.reset_evidence()

    def enter_quarantine(self) -> None:
        """Crossing Quarantine (H3): loai toan bo mau giao cat, xoa danh tinh tam va cam phat check-in."""
        self.crossing_active = True
        self.quarantine = True
        self.reset_identity()
        self.live.clear()
        if self.decision_acc is not None and hasattr(self.decision_acc, "enter_quarantine"):
            self.decision_acc.enter_quarantine()

    def clear_crossing_if_separated(self) -> None:
        """Khi da tach khoi hop nguoi khac: ha crossing_active de bat dau thu thap mau moi tu dau."""
        if self.crossing_active:
            self.crossing_active = False
            if self.decision_acc is not None and hasattr(self.decision_acc, "exit_quarantine"):
                self.decision_acc.exit_quarantine()

    def agg_embedding(self) -> np.ndarray | None:
        if not self.embs:
            return None
        v = sum(e * w for e, w in self.embs)
        return v / (np.linalg.norm(v) + 1e-9)


# Noi theo khoang cach tam (du phong khi IoU khong du vi mat di chuyen nhanh):
# cho phep dich toi (BASE + PER_SEC * thoi_gian_mat) lan be rong mat, toi da MAX.
JUMP_BASE, JUMP_PER_SEC, JUMP_MAX = 1.2, 1.8, 3.5
SIZE_RATIO = (0.50, 1.95)   # cho phep co hep be rong hop khi xoay mat 0 -> 90 do
PREDICT_MAX_DT = 0.5
VEL_SMOOTH = 0.5


def _face_matches_person(face_bbox: np.ndarray, person_bbox: np.ndarray) -> bool:
    fx = float(face_bbox[0] + face_bbox[2]) * 0.5
    fy = float(face_bbox[1] + face_bbox[3]) * 0.5
    px1, py1, px2, py2 = (float(v) for v in person_bbox[:4])
    pw, ph = max(px2 - px1, 1.0), max(py2 - py1, 1.0)
    if not (px1 - 0.1 * pw <= fx <= px2 + 0.1 * pw):
        return False
    if not (py1 - 0.15 * ph <= fy <= py1 + 0.55 * ph):
        return False
    return 0.08 <= float(face_bbox[2] - face_bbox[0]) / pw <= 0.85


def predicted_bbox(t: "Track", now: float) -> np.ndarray:
    dt = min(max(0.0, now - t.last_seen), PREDICT_MAX_DT)
    dx, dy = t.vel[0] * dt, t.vel[1] * dt
    return t.bbox + np.array([dx, dy, dx, dy], np.float32)


class IoUTracker:
    """Noi detection vao track kem cau noi person_bbox tam thoi & Crossing Quarantine nghiem ngat (H3)."""

    def __init__(self, max_age: float = 1.0, iou_thr: float = 0.25,
                 crossing_face_iou: float = 0.25, crossing_person_iou: float = 0.40):
        self.max_age = max_age
        self.iou_thr = iou_thr
        self.crossing_face_iou = crossing_face_iou
        self.crossing_person_iou = crossing_person_iou
        self.tracks: dict[int, Track] = {}
        self._next = 1

    def _new(self, f: Face, now: float, pbox: np.ndarray | None = None) -> Track:
        t = Track(self._next, f.bbox, now, now, person_bbox=pbox)
        self._next += 1
        self.tracks[t.id] = t
        return t

    @staticmethod
    def _match_person_box(face_bbox: np.ndarray, person_boxes: list[np.ndarray] | None) -> np.ndarray | None:
        if not person_boxes:
            return None
        cands = [pb for pb in person_boxes if _face_matches_person(face_bbox, pb)]
        if not cands:
            return None
        fx, fy = float(face_bbox[0] + face_bbox[2]) * 0.5, float(face_bbox[1] + face_bbox[3]) * 0.5
        return min(cands, key=lambda pb: abs(float(pb[0] + pb[2]) * 0.5 - fx) + abs(float(pb[1]) - fy) * 0.5)

    @staticmethod
    def _jump(t: Track, f: Face, now: float, f_pbox: np.ndarray | None = None) -> float | None:
        tw = float(t.bbox[2] - t.bbox[0])
        if tw <= 0 or not SIZE_RATIO[0] <= f.w / tw <= SIZE_RATIO[1]:
            return None
        pb = predicted_bbox(t, now)
        cx, cy = float((pb[0] + pb[2]) / 2), float((pb[1] + pb[3]) / 2)
        fx, fy = float((f.bbox[0] + f.bbox[2]) / 2), float((f.bbox[1] + f.bbox[3]) / 2)
        d = float(np.hypot(fx - cx, fy - cy)) / tw
        limit = min(JUMP_MAX, JUMP_BASE + JUMP_PER_SEC * max(0.0, now - t.last_seen))
        if d <= limit:
            return d
        if d <= JUMP_MAX and t.person_bbox is not None and f_pbox is not None and iou(t.person_bbox, f_pbox) >= 0.45:
            return d * 0.5
        return None

    def _apply_crossing_quarantine(self, active_tracks: list[Track]) -> None:
        crossing_ids: set[int] = set()
        n = len(active_tracks)
        for i in range(n):
            for j in range(i + 1, n):
                ta, tb = active_tracks[i], active_tracks[j]
                face_ov = iou(ta.bbox, tb.bbox) > self.crossing_face_iou
                person_ov = (
                    ta.person_bbox is not None
                    and tb.person_bbox is not None
                    and iou(ta.person_bbox, tb.person_bbox) > self.crossing_person_iou
                )
                if face_ov or person_ov:
                    crossing_ids.add(ta.id)
                    crossing_ids.add(tb.id)
        for t in active_tracks:
            if t.id in crossing_ids:
                if not t.event_done:
                    t.enter_quarantine()
            else:
                t.clear_crossing_if_separated()

    def update(self, faces: list[Face], now: float | None = None,
               person_boxes: list[np.ndarray] | None = None) -> tuple[list[tuple[Track, Face]], list[Track]]:
        now = now or time.time()
        used_t, used_f, matched = set(), set(), []
        face_pboxes = [self._match_person_box(f.bbox, person_boxes) for f in faces]

        def take(tid: int, fi: int) -> None:
            t = self.tracks[tid]
            dt = now - t.last_seen
            if dt > 1e-3:
                (ox, oy), nb = t.center, faces[fi].bbox
                v = ((float(nb[0] + nb[2]) / 2 - ox) / dt, (float(nb[1] + nb[3]) / 2 - oy) / dt)
                t.vel = v if t.hits == 1 else tuple(VEL_SMOOTH * a + (1 - VEL_SMOOTH) * b for a, b in zip(v, t.vel))
            t.bbox, t.last_seen, t.hits = faces[fi].bbox, now, t.hits + 1
            if face_pboxes[fi] is not None:
                t.person_bbox = face_pboxes[fi]
            used_t.add(tid)
            used_f.add(fi)
            matched.append((t, faces[fi]))

        pred = {tid: predicted_bbox(t, now) for tid, t in self.tracks.items()}
        pairs = sorted(((iou(pred[tid], f.bbox), tid, fi)
                        for tid in self.tracks for fi, f in enumerate(faces)), reverse=True)
        for s, tid, fi in pairs:
            if s >= self.iou_thr and tid not in used_t and fi not in used_f:
                take(tid, fi)
        jumps = []
        for tid, t in self.tracks.items():
            if tid in used_t:
                continue
            for fi, f in enumerate(faces):
                if fi not in used_f and (d := self._jump(t, f, now, face_pboxes[fi])) is not None:
                    jumps.append((d, tid, fi))
        for _, tid, fi in sorted(jumps):
            if tid not in used_t and fi not in used_f:
                take(tid, fi)
        for fi, f in enumerate(faces):
            if fi not in used_f:
                matched.append((self._new(f, now, face_pboxes[fi]), f))
        self._apply_crossing_quarantine([t for t, _ in matched])
        expired = [t for t in self.tracks.values() if now - t.last_seen > self.max_age]
        for t in expired:
            del self.tracks[t.id]
        return matched, expired

    def split(self, t: Track, f: Face, now: float) -> Track:
        self.tracks.pop(t.id, None)
        return self._new(f, now, t.person_bbox)

    def absorb(self, keep: Track, drop: Track) -> None:
        """Gop drop (vua nhan ra la cung nguoi) vao keep: giu lan xuat hien som hon."""
        keep.bbox, keep.last_seen, keep.hits = drop.bbox, drop.last_seen, keep.hits + drop.hits
        keep.embs = keep.embs + drop.embs   # pipeline cat bot theo max_track_embeddings / window
        keep.live = keep.live + drop.live
        keep.good_frames += drop.good_frames
        keep.last_reason = drop.last_reason
        if drop.best_quality > keep.best_quality:
            keep.best_quality, keep.best_crop = drop.best_quality, drop.best_crop
        keep.side, keep.vel = drop.side, drop.vel
        self.tracks.pop(drop.id, None)
