"""Danh gia chat luong khuon mat bang heuristic (khong ton them model)."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .engines import Face


@dataclass
class Quality:
    size: float
    blur: float
    brightness: float
    yaw: float          # do, am = quay sang trai-anh
    pitch: float        # duong = cui
    ok: bool
    reason: str
    score: float        # 0..1, dung lam trong so
    roll: float = 0.0
    pose_group: str = "frontal"

    def as_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def estimate_pose(kps: np.ndarray) -> tuple[float, float]:
    le, re, nose, lm, rm = kps
    eye_mid = (le + re) / 2
    mouth_mid = (lm + rm) / 2
    eye_dist = np.linalg.norm(re - le) + 1e-6
    yaw = float((nose[0] - eye_mid[0]) / eye_dist) * 90.0
    span = (mouth_mid[1] - eye_mid[1]) + 1e-6
    pitch = float((nose[1] - eye_mid[1]) / span - 0.55) * 100.0
    return yaw, pitch


def _visible_skin_roi(crop: np.ndarray, yaw: float) -> np.ndarray:
    """Chi do do sang va do net tren nua suon mat thuc su nhin thay khi |yaw| > 40 do."""
    h, w = crop.shape[:2]
    if w < 12 or h < 12 or abs(yaw) <= 40.0:
        return crop
    if yaw < -40.0:
        return crop[:, int(w * 0.28):]
    return crop[:, :int(w * 0.72)]


def assess(img: np.ndarray, face: Face, qcfg: dict, pose=None) -> Quality:
    h, w = img.shape[:2]
    x1, y1, x2, y2 = face.bbox.astype(int)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    size = float(max(face.w, face.h * 0.72)) if (pose is not None and abs(pose.yaw) > 40.0) else float(min(face.w, face.h))
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return Quality(size, 0, 0, 0, 0, False, "out_of_frame", 0.0)

    if pose is not None:
        yaw, pitch, roll, grp = float(pose.yaw), float(pose.pitch), float(pose.roll), str(pose.pose_group)
    else:
        yaw, pitch = estimate_pose(face.kps)
        roll = 0.0
        grp = "left_profile" if yaw < -40.0 else ("right_profile" if yaw > 40.0 else "frontal")

    roi = _visible_skin_roi(crop, yaw)
    gray = cv2.cvtColor(cv2.resize(roi, (96, 96)), cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    bright = float(gray.mean())

    max_yaw = float(qcfg.get("max_yaw", 90))
    max_pitch = float(qcfg.get("max_pitch", 35))
    max_roll = float(qcfg.get("max_roll", 35))

    reason = ""
    if size < qcfg["min_face"]:
        reason = "small"
    elif blur < qcfg["min_blur"]:
        reason = "blur"
    elif not (qcfg["min_brightness"] <= bright <= qcfg["max_brightness"]):
        reason = "light"
    elif abs(yaw) > max_yaw:
        reason = "yaw"
    elif abs(pitch) > max_pitch:
        reason = "pitch"
    elif abs(roll) > max_roll:
        reason = "roll"
    edge = x1 <= 1 or y1 <= 1 or x2 >= w - 1 or y2 >= h - 1
    if not reason and edge:
        reason = "cut"

    yaw_factor = max(0.25, 1.0 - abs(yaw) / 120.0)
    score = (min(size / 110.0, 1.0) * min(blur / 140.0, 1.0) * yaw_factor * min(float(face.score), 1.0))
    return Quality(size, blur, bright, yaw, pitch, reason == "", reason or "ok", float(score), roll=roll, pose_group=grp)
