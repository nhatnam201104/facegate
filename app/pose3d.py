"""Uoc luong tu the dau 3D (yaw, pitch, roll) va 68 diem moc 3D toi +-90 do bang 3DDFA_V2 ONNX (MIT)."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("pose3d")

POSE_GROUPS = ("frontal", "left_profile", "right_profile")
IDX_CHIN = 8
IDX_NOSE_TIP = 30
IDX_LEFT_EYE = list(range(36, 42))
IDX_RIGHT_EYE = list(range(42, 48))
IDX_MOUTH_LEFT = 48
IDX_MOUTH_RIGHT = 54


@dataclass
class PoseResult:
    yaw: float
    pitch: float
    roll: float
    pose_group: str
    landmarks3d: np.ndarray
    kps5: np.ndarray
    visibility: float = 1.0


def classify_pose_group(yaw: float, prev_group: str | None = None,
                        frontal_max: float = 40.0, hysteresis: float = 5.0) -> str:
    enter_profile = frontal_max + (hysteresis if prev_group == "frontal" else 0.0)
    exit_profile = max(10.0, frontal_max - hysteresis)
    if prev_group == "left_profile":
        if yaw < -exit_profile:
            return "left_profile"
        return "right_profile" if yaw > frontal_max else "frontal"
    if prev_group == "right_profile":
        if yaw > exit_profile:
            return "right_profile"
        return "left_profile" if yaw < -frontal_max else "frontal"
    if yaw < -enter_profile:
        return "left_profile"
    if yaw > enter_profile:
        return "right_profile"
    return "frontal"


def _orthogonalize_rows(M: np.ndarray) -> np.ndarray:
    r1 = M[0] / (np.linalg.norm(M[0]) + 1e-9)
    r2 = M[1] - np.dot(M[1], r1) * r1
    r2 = r2 / (np.linalg.norm(r2) + 1e-9)
    r3 = np.cross(r1, r2)
    return np.stack([r1, r2, r3], axis=0).astype(np.float32)


def euler_from_relative_rotation(R_rel: np.ndarray) -> tuple[float, float, float]:
    yaw = math.degrees(math.atan2(float(R_rel[0, 2]), float(R_rel[2, 2])))
    yaw = max(-95.0, min(95.0, yaw))
    sin_p = max(-1.0, min(1.0, -float(R_rel[1, 2])))
    pitch = math.degrees(math.asin(sin_p))
    roll = math.degrees(math.atan2(-float(R_rel[1, 0]), float(R_rel[1, 1]))) if abs(sin_p) < 0.9999 else 0.0
    return float(yaw), float(pitch), float(roll)


def _parse_roi_box(bbox: np.ndarray) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = (float(v) for v in bbox[:4])
    old_size = (x2 - x1 + y2 - y1) / 2.0
    cx = x2 - (x2 - x1) / 2.0
    cy = y2 - (y2 - y1) / 2.0 + old_size * 0.14
    size = max(16.0, old_size * 1.58)
    return cx - size / 2.0, cy - size / 2.0, cx + size / 2.0, cy + size / 2.0


def _crop_roi(img: np.ndarray, roi_box: tuple[float, float, float, float], dst_size: int = 120) -> np.ndarray:
    h, w = img.shape[:2]
    sx, sy, ex, ey = (int(round(v)) for v in roi_box)
    dh, dw = max(1, ey - sy), max(1, ex - sx)
    res = np.zeros((dh, dw, 3), dtype=np.uint8)
    dsx = -sx if sx < 0 else 0
    sx = max(0, sx)
    dex = dw - (ex - w) if ex > w else dw
    ex = min(w, ex)
    dsy = -sy if sy < 0 else 0
    sy = max(0, sy)
    dey = dh - (ey - h) if ey > h else dh
    ey = min(h, ey)
    if ex > sx and ey > sy and dex > dsx and dey > dsy:
        res[dsy:dey, dsx:dex] = img[sy:ey, sx:ex]
    return cv2.resize(res, (dst_size, dst_size), interpolation=cv2.INTER_LINEAR)


def _kps5_from_68(pts68: np.ndarray) -> np.ndarray:
    le = pts68[IDX_LEFT_EYE, :2].mean(axis=0)
    re = pts68[IDX_RIGHT_EYE, :2].mean(axis=0)
    nose = pts68[IDX_NOSE_TIP, :2]
    lm = pts68[IDX_MOUTH_LEFT, :2]
    rm = pts68[IDX_MOUTH_RIGHT, :2]
    return np.stack([le, re, nose, lm, rm], axis=0).astype(np.float32)


def _fallback_from_5pt(bbox: np.ndarray, kps: np.ndarray) -> tuple[float, float, float, np.ndarray]:
    le, re, nose, lm, rm = kps.astype(np.float32)
    eye_mid = (le + re) / 2.0
    mouth_mid = (lm + rm) / 2.0
    eye_dist = max(float(np.linalg.norm(re - le)), float(bbox[2] - bbox[0]) * 0.25, 1.0)
    yaw = float(np.clip((nose[0] - eye_mid[0]) / eye_dist * 90.0, -90.0, 90.0))
    span = max(float(mouth_mid[1] - eye_mid[1]), 1.0)
    pitch = float(np.clip(((nose[1] - eye_mid[1]) / span - 0.55) * 100.0, -60.0, 60.0))
    roll = float(math.degrees(math.atan2(float(re[1] - le[1]), max(1.0, float(re[0] - le[0])))))
    pts68 = np.zeros((68, 3), dtype=np.float32)
    x1, y1, x2, y2 = (float(v) for v in bbox[:4])
    pts68[:17, 0] = np.linspace(x1, x2, 17)
    pts68[:17, 1] = np.linspace(eye_mid[1], y2, 17)
    pts68[IDX_CHIN] = [(x1 + x2) / 2.0, y2, 0.0]
    pts68[IDX_LEFT_EYE] = [le[0], le[1], 0.0]
    pts68[IDX_RIGHT_EYE] = [re[0], re[1], 0.0]
    pts68[27:36] = [nose[0], nose[1], 0.0]
    pts68[IDX_MOUTH_LEFT] = [lm[0], lm[1], 0.0]
    pts68[IDX_MOUTH_RIGHT] = [rm[0], rm[1], 0.0]
    return yaw, pitch, roll, pts68


class PoseEstimator3D:
    def __init__(self, models_dir: Path | None = None, frontal_max_yaw: float = 40.0, hysteresis: float = 5.0):
        self.frontal_max = frontal_max_yaw
        self.hysteresis = hysteresis
        self.session = None
        self.in_name = ""
        self._r_ref: np.ndarray | None = None
        if models_dir is not None:
            onnx_p = models_dir / "3ddfa_v2_mb1_120x120.onnx"
            mean_p = models_dir / "tddfa_param_mean.bin"
            std_p = models_dir / "tddfa_param_std.bin"
            u_p = models_dir / "tddfa_u_base.bin"
            w_shp_p = models_dir / "tddfa_w_shp_base.bin"
            w_exp_p = models_dir / "tddfa_w_exp_base.bin"
            if all(p.exists() for p in (onnx_p, mean_p, std_p, u_p, w_shp_p, w_exp_p)):
                import onnxruntime as ort
                so = ort.SessionOptions()
                so.log_severity_level = 3
                self.session = ort.InferenceSession(str(onnx_p), so, providers=["CPUExecutionProvider"])
                self.in_name = self.session.get_inputs()[0].name
                self.param_mean = np.fromfile(str(mean_p), dtype=np.float32)
                self.param_std = np.fromfile(str(std_p), dtype=np.float32)
                self.u_base = np.fromfile(str(u_p), dtype=np.float32).reshape(-1, 1)
                self.w_shp = np.fromfile(str(w_shp_p), dtype=np.float32).reshape(-1, 40)
                self.w_exp = np.fromfile(str(w_exp_p), dtype=np.float32).reshape(-1, 10)
                p0 = self.param_mean[:12].reshape(3, 4)[:, :3]
                self._r_ref = _orthogonalize_rows(p0)

    def estimate(self, img: np.ndarray, face, prev_group: str | None = None) -> PoseResult:
        if self.session is None:
            yaw, pitch, roll, pts68 = _fallback_from_5pt(face.bbox, face.kps)
            grp = classify_pose_group(yaw, prev_group, self.frontal_max, self.hysteresis)
            return PoseResult(yaw, pitch, roll, grp, pts68, face.kps.astype(np.float32), 1.0)

        roi = _parse_roi_box(face.bbox)
        crop = _crop_roi(img, roi, 120)
        blob = ((crop.astype(np.float32) - 127.5) / 128.0).transpose(2, 0, 1)[None]
        param = self.session.run(None, {self.in_name: blob})[0][0]
        param = param * self.param_std + self.param_mean
        P = param[:12].reshape(3, 4)
        R_curr = _orthogonalize_rows(P[:, :3])
        R_rel = R_curr @ self._r_ref.T if self._r_ref is not None else R_curr
        yaw, pitch, roll = euler_from_relative_rotation(R_rel)

        alpha_shp = param[12:52].reshape(-1, 1)
        alpha_exp = param[52:62].reshape(-1, 1)
        pts3d = (self.u_base + self.w_shp @ alpha_shp + self.w_exp @ alpha_exp).reshape(3, -1, order="F")
        pts_cam = P[:, :3] @ pts3d + P[:, 3:4]
        pts_cam[1, :] = 121.0 - pts_cam[1, :]
        sx, sy, ex, ey = roi
        scale_x, scale_y = (ex - sx) / 120.0, (ey - sy) / 120.0
        pts_img = np.zeros((68, 3), dtype=np.float32)
        pts_img[:, 0] = pts_cam[0, :] * scale_x + sx
        pts_img[:, 1] = pts_cam[1, :] * scale_y + sy
        pts_img[:, 2] = pts_cam[2, :] * ((scale_x + scale_y) * 0.5)

        grp = classify_pose_group(yaw, prev_group, self.frontal_max, self.hysteresis)
        vis = float(max(0.2, math.cos(math.radians(min(85.0, abs(yaw))))))
        return PoseResult(yaw, pitch, roll, grp, pts_img, _kps5_from_68(pts_img), vis)
