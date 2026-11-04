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
    yaw: float          # do (xap xi), am = quay sang trai-anh
    pitch: float        # xap xi, duong = cui
    ok: bool
    reason: str
    score: float        # 0..1, dung lam trong so

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


def assess(img: np.ndarray, face: Face, qcfg: dict) -> Quality:
    h, w = img.shape[:2]
    x1, y1, x2, y2 = face.bbox.astype(int)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    size = float(min(face.w, face.h))
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return Quality(size, 0, 0, 0, 0, False, "out_of_frame", 0.0)
    gray = cv2.cvtColor(cv2.resize(crop, (112, 112)), cv2.COLOR_BGR2GRAY)
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    bright = float(gray.mean())
    yaw, pitch = estimate_pose(face.kps)

    reason = ""
    if size < qcfg["min_face"]:
        reason = "small"
    elif blur < qcfg["min_blur"]:
        reason = "blur"
    elif not (qcfg["min_brightness"] <= bright <= qcfg["max_brightness"]):
        reason = "light"
    elif abs(yaw) > qcfg["max_yaw"]:
        reason = "yaw"
    edge = x1 <= 1 or y1 <= 1 or x2 >= w - 1 or y2 >= h - 1
    if not reason and edge:
        reason = "cut"
    score = (min(size / 120, 1.0) * min(blur / 150, 1.0)
             * max(0.0, 1 - abs(yaw) / 60) * min(face.score, 1.0))
    return Quality(size, blur, bright, yaw, pitch, reason == "", reason or "ok", float(score))
