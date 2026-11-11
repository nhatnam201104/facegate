"""Pose-Bucketed Person-Face Tracker: giu lien mach 1 ID khi xoay dau 0..+-90 do va tach 3 ngan embedding."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .engines import Face

POSE_GROUPS = ("frontal", "left_profile", "right_profile")


def iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1 = max(float(a[0]), float(b[0])), max(float(a[1]), float(b[1]))
    x2, y2 = min(float(a[2]), float(b[2])), min(float(a[3]), float(b[3]))
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter / ua) if ua > 0 else 0.0


def _face_matches_person(face_bbox: np.ndarray, person_bbox: np.ndarray) -> bool:
    """Kiểm tra hộp mặt nằm hợp lý ở phần đầu-vai của hộp cơ thể."""
    fx = float(face_bbox[0] + face_bbox[2]) * 0.5
    fy = float(face_bbox[1] + face_bbox[3]) * 0.5
    px1, py1, px2, py2 = (float(v) for v in person_bbox[:4])
    pw, ph = max(px2 - px1, 1.0), max(py2 - py1, 1.0)
    if not (px1 - 0.1 * pw <= fx <= px2 + 0.1 * pw):
        return False
    if not (py1 - 0.15 * ph <= fy <= py1 + 0.55 * ph):
        return False
    fw = float(face_bbox[2] - face_bbox[0])
    return 0.08 <= fw / pw <= 0.85


def _empty_buckets() -> dict[str, list[tuple[np.ndarray, float]]]:
    return {g: [] for g in POSE_GROUPS}


def _empty_yaws() -> dict[str, list[float]]:
    return {g: [] for g in POSE_GROUPS}


@dataclass
class Track:
    id: int
    bbox: np.ndarray
    first_seen: float
    last_seen: float
    hits: int = 1
    person_bbox: np.ndarray | None = None
    buckets: dict[str, list[tuple[np.ndarray, float]]] = field(default_factory=_empty_buckets)
    bucket_yaws: dict[str, list[float]] = field(default_factory=_empty_yaws)
    pose_group: str = "frontal"
    last_yaw: float = 0.0
    min_abs_yaw: float = 999.0
    pose_history: deque = field(default_factory=lambda: deque(maxlen=24))
    context_history: deque = field(default_factory=lambda: deque(maxlen=16))
    good_frames: int = 0
    last_reason: str = ""
    # danh tinh
    employee_id: int | None = None
    code: str | None = None
    name: str | None = None
    score: float = 0.0
    margin: float = 0.0
    matched_group: str = "frontal"
    verification_tier: str | None = None
    confirmed_at: float | None = None
    best_score: float = -1.0
    # su kien
    event_done: bool = False
    pending_direction: str | None = None
    side: int | None = None
    best_quality: float = -1.0
    best_crop: np.ndarray | None = None
    # chong gia mao
    live: list = field(default_factory=list)        # xac suat MiniFASNet o cac frame |yaw| <= 40°
    liveness_score: float | None = None             # diem tong hop (MiniFASNet hoac Context+Parallax)
    liveness_mode: str = "texture_fas"
    liveness_detail: dict = field(default_factory=dict)
    spoof: bool = False
    spoof_reason: str | None = None
    claimed: tuple | None = None                    # (employee_id, code, name) khop gan nhat
    # lich su
    vel: tuple = (0.0, 0.0)
    clip_sid: int | None = None
    snapshot: str | None = None
    event_id: int | None = None

    @property
    def embs(self) -> list[tuple[np.ndarray, float]]:
        return self.buckets[self.matched_group]

    @property
    def center(self) -> tuple[float, float]:
        return float((self.bbox[0] + self.bbox[2]) / 2), float((self.bbox[1] + self.bbox[3]) / 2)

    @property
    def live_mean(self) -> float | None:
        if self.live:
            return float(np.mean(self.live))
        return self.liveness_score

    def reset_identity(self) -> None:
        self.employee_id = self.code = self.name = self.confirmed_at = None
        self.verification_tier = None
        self.score = self.margin = 0.0

    def agg_embedding(self, pose_group: str | None = None) -> np.ndarray | None:
        grp = pose_group or self.matched_group
        items = self.buckets.get(grp, [])
        if not items:
            return None
        v = sum(e * w for e, w in items)
        return v / (np.linalg.norm(v) + 1e-9)

    def should_split(self, emb: np.ndarray, pose_group: str, split_cos: float) -> bool:
        """Chỉ kiểm tra bất đồng nhất danh tính giữa các vector TRONG CÙNG MỘT pose_group."""
        items = self.buckets.get(pose_group, [])
        if len(items) < 2:
            return False
        agg = self.agg_embedding(pose_group)
        return agg is not None and float(emb @ agg) < split_cos

    def add_embedding(self, emb: np.ndarray, weight: float, pose_group: str,
                      yaw: float = 0.0, max_embs: int = 10) -> None:
        grp = pose_group if pose_group in POSE_GROUPS else "frontal"
        self.pose_group = grp
        self.last_yaw = float(yaw)
        self.min_abs_yaw = min(self.min_abs_yaw, abs(float(yaw)))
        b = self.buckets[grp]
        b.append((emb, max(float(weight), 0.05)))
        if len(b) > max_embs:
            del b[:-max_embs]
        ys = self.bucket_yaws[grp]
        ys.append(float(yaw))
        if len(ys) > max_embs:
            del ys[:-max_embs]
        self.good_frames += 1

    def group_mean_yaw(self, pose_group: str) -> float:
        ys = self.bucket_yaws.get(pose_group, [])
        return float(np.mean(ys)) if ys else self.last_yaw

    def select_best_bucket(self, min_frontal: int = 2, min_profile: int = 4) -> tuple[str | None, np.ndarray | None]:
        """Quy tắc Best-shot: hễ có đủ frame ở ngăn 'frontal' thì luôn ưu tiên chốt trên 'frontal'."""
        if len(self.buckets["frontal"]) >= min_frontal:
            return "frontal", self.agg_embedding("frontal")
        best_prof = max(("left_profile", "right_profile"), key=lambda g: len(self.buckets[g]))
        if len(self.buckets[best_prof]) >= min_profile:
            return best_prof, self.agg_embedding(best_prof)
        return None, None


# Cho phep bien thien be rong mat rong hon khi quay tu chinh dien sang nghieng suon 90 do
JUMP_BASE, JUMP_PER_SEC, JUMP_MAX = 1.2, 1.8, 3.5
SIZE_RATIO = (0.50, 1.95)
PREDICT_MAX_DT = 0.5
VEL_SMOOTH = 0.5


def predicted_bbox(t: "Track", now: float) -> np.ndarray:
    dt = min(max(0.0, now - t.last_seen), PREDICT_MAX_DT)
    dx, dy = t.vel[0] * dt, t.vel[1] * dt
    return t.bbox + np.array([dx, dy, dx, dy], np.float32)


class IoUTracker:
    """Noi detection vao track: uu tien IoU mat, du phong bang khoang cach tam va hop co the person_bbox."""

    def __init__(self, max_age: float = 1.0, iou_thr: float = 0.25):
        self.max_age = max_age
        self.iou_thr = iou_thr
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
        if t.person_bbox is not None and f_pbox is not None and iou(t.person_bbox, f_pbox) >= 0.45:
            return d * 0.5
        return None

    def update(
        self,
        faces: list[Face],
        now: float | None = None,
        person_boxes: list[np.ndarray] | None = None,
    ) -> tuple[list[tuple[Track, Face]], list[Track]]:
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
        expired = [t for t in self.tracks.values() if now - t.last_seen > self.max_age]
        for t in expired:
            del self.tracks[t.id]
        return matched, expired

    def split(self, t: Track, f: Face, now: float) -> Track:
        self.tracks.pop(t.id, None)
        return self._new(f, now, t.person_bbox)

    def absorb(self, keep: Track, drop: Track, max_embs: int = 10) -> None:
        keep.bbox, keep.last_seen, keep.hits = drop.bbox, drop.last_seen, keep.hits + drop.hits
        if drop.person_bbox is not None:
            keep.person_bbox = drop.person_bbox
        for g in POSE_GROUPS:
            keep.buckets[g] = (keep.buckets[g] + drop.buckets[g])[-max_embs:]
            keep.bucket_yaws[g] = (keep.bucket_yaws[g] + drop.bucket_yaws[g])[-max_embs:]
        keep.pose_history.extend(drop.pose_history)
        keep.context_history.extend(drop.context_history)
        keep.live = keep.live + drop.live
        keep.good_frames += drop.good_frames
        keep.min_abs_yaw = min(keep.min_abs_yaw, drop.min_abs_yaw)
        keep.last_reason = drop.last_reason
        if drop.best_quality > keep.best_quality:
            keep.best_quality, keep.best_crop = drop.best_quality, drop.best_crop
        keep.side, keep.vel = drop.side, drop.vel
        self.tracks.pop(drop.id, None)
