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
    # lich su
    vel: tuple = (0.0, 0.0)                          # van toc tam mat (px/giay), de du doan vi tri
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
        self.score = self.margin = 0.0

    def agg_embedding(self) -> np.ndarray | None:
        if not self.embs:
            return None
        v = sum(e * w for e, w in self.embs)
        return v / (np.linalg.norm(v) + 1e-9)


# Noi theo khoang cach tam (du phong khi IoU khong du vi mat di chuyen nhanh):
# cho phep dich toi (BASE + PER_SEC * thoi_gian_mat) lan be rong mat, toi da MAX.
JUMP_BASE, JUMP_PER_SEC, JUMP_MAX = 1.0, 1.5, 3.0
SIZE_RATIO = (0.65, 1.55)   # kich thuoc mat giua 2 lan phai tuong duong
PREDICT_MAX_DT = 0.5        # chi ngoai suy theo van toc toi da N giay (mat lau thi khong doan)
VEL_SMOOTH = 0.5            # lam muot van toc giua cac lan detect


def predicted_bbox(t: "Track", now: float) -> np.ndarray:
    dt = min(max(0.0, now - t.last_seen), PREDICT_MAX_DT)
    dx, dy = t.vel[0] * dt, t.vel[1] * dt
    return t.bbox + np.array([dx, dy, dx, dy], np.float32)


class IoUTracker:
    """Noi detection vao track: uu tien IoU, du phong bang khoang cach tam (co gioi han theo kich thuoc mat).

    Noi bang khoang cach de nham nguoi hon -> pipeline kiem tra lai bang embedding va tach track (split)
    neu khuon mat khong khop.
    """

    def __init__(self, max_age: float = 1.0, iou_thr: float = 0.25):
        self.max_age = max_age
        self.iou_thr = iou_thr
        self.tracks: dict[int, Track] = {}
        self._next = 1

    def _new(self, f: Face, now: float) -> Track:
        t = Track(self._next, f.bbox, now, now)
        self._next += 1
        self.tracks[t.id] = t
        return t

    @staticmethod
    def _jump(t: Track, f: Face, now: float) -> float | None:
        """Khoang cach tam tinh theo be rong mat cua track; None neu vuot gioi han / khac kich thuoc."""
        tw = float(t.bbox[2] - t.bbox[0])
        if tw <= 0 or not SIZE_RATIO[0] <= f.w / tw <= SIZE_RATIO[1]:
            return None
        pb = predicted_bbox(t, now)
        cx, cy = float((pb[0] + pb[2]) / 2), float((pb[1] + pb[3]) / 2)
        fx, fy = float((f.bbox[0] + f.bbox[2]) / 2), float((f.bbox[1] + f.bbox[3]) / 2)
        d = float(np.hypot(fx - cx, fy - cy)) / tw
        limit = min(JUMP_MAX, JUMP_BASE + JUMP_PER_SEC * max(0.0, now - t.last_seen))
        return d if d <= limit else None

    def update(self, faces: list[Face], now: float | None = None) -> tuple[list[tuple[Track, Face]], list[Track]]:
        now = now or time.time()
        used_t, used_f, matched = set(), set(), []

        def take(tid: int, fi: int) -> None:
            t = self.tracks[tid]
            dt = now - t.last_seen
            if dt > 1e-3:
                (ox, oy), nb = t.center, faces[fi].bbox
                v = ((float(nb[0] + nb[2]) / 2 - ox) / dt, (float(nb[1] + nb[3]) / 2 - oy) / dt)
                t.vel = v if t.hits == 1 else tuple(VEL_SMOOTH * a + (1 - VEL_SMOOTH) * b for a, b in zip(v, t.vel))
            t.bbox, t.last_seen, t.hits = faces[fi].bbox, now, t.hits + 1
            used_t.add(tid)
            used_f.add(fi)
            matched.append((t, faces[fi]))

        pred = {tid: predicted_bbox(t, now) for tid, t in self.tracks.items()}
        pairs = sorted(((iou(pred[tid], f.bbox), tid, fi)
                        for tid in self.tracks for fi, f in enumerate(faces)), reverse=True)
        for s, tid, fi in pairs:
            if s >= self.iou_thr and tid not in used_t and fi not in used_f:
                take(tid, fi)
        # du phong: mat di chuyen nhanh -> IoU thap nhung tam van gan
        jumps = []
        for tid, t in self.tracks.items():
            if tid in used_t:
                continue
            for fi, f in enumerate(faces):
                if fi not in used_f and (d := self._jump(t, f, now)) is not None:
                    jumps.append((d, tid, fi))
        for _, tid, fi in sorted(jumps):
            if tid not in used_t and fi not in used_f:
                take(tid, fi)
        for fi, f in enumerate(faces):
            if fi not in used_f:
                matched.append((self._new(f, now), f))
        expired = [t for t in self.tracks.values() if now - t.last_seen > self.max_age]
        for t in expired:
            del self.tracks[t.id]
        return matched, expired

    def split(self, t: Track, f: Face, now: float) -> Track:
        """Detection vua noi vao t la nguoi khac: bo t khoi tracker, tao track moi cho f."""
        self.tracks.pop(t.id, None)
        return self._new(f, now)

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
