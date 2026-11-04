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


class IoUTracker:
    def __init__(self, max_age: float = 1.0, iou_thr: float = 0.25):
        self.max_age = max_age
        self.iou_thr = iou_thr
        self.tracks: dict[int, Track] = {}
        self._next = 1

    def update(self, faces: list[Face], now: float | None = None) -> tuple[list[tuple[Track, Face]], list[Track]]:
        now = now or time.time()
        pairs = sorted(((iou(t.bbox, f.bbox), tid, fi)
                        for tid, t in self.tracks.items() for fi, f in enumerate(faces)), reverse=True)
        used_t, used_f, matched = set(), set(), []
        for s, tid, fi in pairs:
            if s < self.iou_thr or tid in used_t or fi in used_f:
                continue
            t = self.tracks[tid]
            t.bbox, t.last_seen, t.hits = faces[fi].bbox, now, t.hits + 1
            used_t.add(tid)
            used_f.add(fi)
            matched.append((t, faces[fi]))
        for fi, f in enumerate(faces):
            if fi not in used_f:
                t = Track(self._next, f.bbox, now, now)
                self._next += 1
                self.tracks[t.id] = t
                matched.append((t, f))
        expired = [t for t in self.tracks.values() if now - t.last_seen > self.max_age]
        for t in expired:
            del self.tracks[t.id]
        return matched, expired
