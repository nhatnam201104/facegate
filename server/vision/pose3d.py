"""Uoc luong tu the dau (yaw, pitch, roll) bang cv2.solvePnP tren 5 diem moc YuNet (feature 002).

Khong dung model hay du lieu ben thu ba (thay cho 3DDFA_V2 + Basel Face Model):
mo hinh 3D 5 diem FACE5_3D la gia tri nhan trac trung binh TU UOC LUONG, khong phai sinh tu BFM.
Quy uoc dau giu nguyen nhu ban truoc: yaw > 0 khi mui lech sang phai-anh so voi trung diem hai mat.
Diem moc YuNet kem tin cay khi |yaw| > ~60 do -> pose_reliable=False de pipeline khong chot danh tinh (Hien phap II).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

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

# Mo hinh 3D 5 diem (mm). He truc: x sang phai-anh, y xuong duoi, z HUONG RA XA camera.
# Gia tri tu uoc luong theo nhan trac trung binh nguoi truong thanh, KHONG phai sinh tu BFM.
# Thu tu khop kps YuNet: mat trai-anh, mat phai-anh, dau mui, khoe mieng trai-anh, khoe mieng phai-anh.
FACE5_3D = np.array([
    [-32.0, -35.0, 25.0],
    [32.0, -35.0, 25.0],
    [0.0, 0.0, 0.0],
    [-24.0, 32.0, 22.0],
    [24.0, 32.0, 22.0],
], dtype=np.float64)

MAX_REPROJ_RATIO = 0.25  # loi chieu lai trung binh / khoang cach hai mat


@dataclass
class PoseResult:
    yaw: float
    pitch: float
    roll: float
    pose_group: str
    landmarks3d: np.ndarray
    kps5: np.ndarray
    visibility: float = 1.0
    pose_reliable: bool = True
    raw_yaw: float | None = None   # yaw tho cua khung nay (yaw/pitch o tren da lam muot EMA - chi de hien thi)


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


def euler_from_relative_rotation(R_rel: np.ndarray) -> tuple[float, float, float]:
    yaw = math.degrees(math.atan2(float(R_rel[0, 2]), float(R_rel[2, 2])))
    yaw = max(-95.0, min(95.0, yaw))
    sin_p = max(-1.0, min(1.0, -float(R_rel[1, 2])))
    pitch = math.degrees(math.asin(sin_p))
    roll = math.degrees(math.atan2(-float(R_rel[1, 0]), float(R_rel[1, 1]))) if abs(sin_p) < 0.9999 else 0.0
    return float(yaw), float(pitch), float(roll)


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


def camera_matrix(w: int, h: int) -> np.ndarray:
    f = float(max(w, h))
    return np.array([[f, 0.0, w / 2.0], [0.0, f, h / 2.0], [0.0, 0.0, 1.0]], dtype=np.float64)


def pose_from_rotation(R: np.ndarray) -> tuple[float, float, float]:
    """Doi ma tran quay (mo hinh -> camera) sang (yaw, pitch, roll) theo quy uoc dau cua he thong."""
    yaw, pitch, roll = euler_from_relative_rotation(R)
    # Quay duong quanh truc y (he z ra xa camera) day mat sang phai-anh -> mui lech trai-anh -> yaw he thong am.
    # Pitch giu cung dau voi _fallback_from_5pt / quality.estimate_pose (duong = mui thap xuong so voi mat).
    return -yaw, pitch, roll


def solve_pnp_5pt(kps: np.ndarray, w: int, h: int) -> tuple[np.ndarray, float] | None:
    """Tra ve (R, loi chieu lai trung binh px) hoac None neu that bai."""
    img_pts = np.asarray(kps, dtype=np.float64).reshape(5, 2)
    K = camera_matrix(w, h)
    try:
        ok, rvec, tvec = cv2.solvePnP(FACE5_3D, img_pts, K, None, flags=cv2.SOLVEPNP_EPNP)
        if not ok:
            return None
        ok, rvec, tvec = cv2.solvePnP(FACE5_3D, img_pts, K, None, rvec, tvec,
                                      useExtrinsicGuess=True, flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok or float(tvec[2, 0]) <= 0:
            return None
    except cv2.error:
        return None
    proj, _ = cv2.projectPoints(FACE5_3D, rvec, tvec, K, None)
    err = float(np.mean(np.linalg.norm(proj.reshape(5, 2) - img_pts, axis=1)))
    R, _ = cv2.Rodrigues(rvec)
    return R, err


class PoseEstimator3D:
    """Giu ten lop cu de khong vo cac noi goi; models_dir bi bo qua (khong con model pose)."""

    def __init__(self, models_dir=None, frontal_max_yaw: float = 40.0, hysteresis: float = 5.0,
                 max_reliable_yaw: float = 60.0, ema_tau_sec: float = 0.3):
        self.frontal_max = frontal_max_yaw
        self.hysteresis = hysteresis
        self.max_reliable_yaw = float(max_reliable_yaw)
        self.ema_tau_sec = float(ema_tau_sec)
        self._ema: dict[int, tuple[float, float, float]] = {}  # track_id -> (ts, yaw, pitch)

    def _smooth(self, track_id: int | None, ts: float | None, yaw: float, pitch: float) -> tuple[float, float]:
        if track_id is None or ts is None or self.ema_tau_sec <= 0:
            return yaw, pitch
        prev = self._ema.get(track_id)
        if prev is None or ts <= prev[0]:
            self._ema[track_id] = (ts, yaw, pitch)
            return yaw, pitch
        alpha = 1.0 - math.exp(-(ts - prev[0]) / self.ema_tau_sec)
        y = prev[1] + alpha * (yaw - prev[1])
        p = prev[2] + alpha * (pitch - prev[2])
        self._ema[track_id] = (ts, y, p)
        if len(self._ema) > 512:
            for k in sorted(self._ema, key=lambda k: self._ema[k][0])[:256]:
                self._ema.pop(k, None)
        return y, p

    def forget(self, track_id: int) -> None:
        self._ema.pop(track_id, None)

    def estimate(self, img: np.ndarray, face, prev_group: str | None = None,
                 track_id: int | None = None, ts: float | None = None) -> PoseResult:
        h, w = img.shape[:2]
        kps = face.kps.astype(np.float32)
        fb_yaw, fb_pitch, fb_roll, pts68 = _fallback_from_5pt(face.bbox, kps)
        eye_dist = max(float(np.linalg.norm(kps[1] - kps[0])), 1.0)
        res = solve_pnp_5pt(kps, w, h)
        if res is not None and res[1] <= MAX_REPROJ_RATIO * eye_dist:
            raw_yaw, raw_pitch, roll = pose_from_rotation(res[0])
            # Do tin cay + nhom tu the quyet dinh tren yaw THO cua khung nay: EMA tre pha khi quay dau nhanh
            # (0 -> 80 do trong 1 khung van chi ra ~34 do) se de lot mat nghieng vao gallery chinh dien.
            reliable = abs(raw_yaw) <= self.max_reliable_yaw
            yaw, pitch = self._smooth(track_id, ts, raw_yaw, raw_pitch)
        else:
            # PnP that bai: gia tri du phong khong duoc ghi vao trang thai EMA
            raw_yaw, raw_pitch, roll = fb_yaw, fb_pitch, fb_roll
            yaw, pitch = raw_yaw, raw_pitch
            reliable = False
        grp = classify_pose_group(raw_yaw, prev_group, self.frontal_max, self.hysteresis)
        vis = float(max(0.2, math.cos(math.radians(min(85.0, abs(raw_yaw))))))
        return PoseResult(float(yaw), float(pitch), float(roll), grp, pts68, kps, vis, reliable, float(raw_yaw))


def create_pose_estimator(cfg: dict) -> PoseEstimator3D:
    rc = cfg.get("recognition", {})
    pc = cfg.get("pose", {})
    engine = str(pc.get("engine", "pnp5"))
    if engine != "pnp5":
        raise ValueError(f"pose.engine={engine!r} chưa được hỗ trợ (chỉ 'pnp5'; 'mediapipe' xem T042)")
    return PoseEstimator3D(
        frontal_max_yaw=float(rc.get("frontal_max_yaw", 40.0)),
        hysteresis=float(rc.get("pose_hysteresis", 5.0)),
        max_reliable_yaw=float(pc.get("max_reliable_yaw", 60.0)),
        ema_tau_sec=float(pc.get("ema_tau_sec", 0.3)),
    )
