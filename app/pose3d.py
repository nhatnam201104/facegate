"""Ước lượng tư thế đầu 3D (yaw, pitch, roll) và 68 điểm mốc 3D tới ±90° bằng 3DDFA_V2 ONNX.

Giấy phép mô hình gốc: MIT (cleardusk/3DDFA_V2).
Quy ước dấu khớp với FaceGate:
  - yaw < 0  : mũi lệch sang trái ảnh gốc (người dùng quay sang PHẢI của họ) -> 'left_profile' khi < -40°
  - yaw > 0  : mũi lệch sang phải ảnh gốc (người dùng quay sang TRÁI của họ) -> 'right_profile' khi > +40°
  - pitch < 0: ngẩng đầu lên; pitch > 0: cúi đầu xuống
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("pose3d")

POSE_GROUPS = ("frontal", "left_profile", "right_profile")

# Chỉ số chuẩn trên lưới 68 điểm mốc 3D (iBUG / 300W-LP)
IDX_JAW_LEFT = list(range(0, 8))       # sườn hàm trái-ảnh (nhìn rõ khi right_profile / frontal)
IDX_JAW_RIGHT = list(range(9, 17))     # sườn hàm phải-ảnh (nhìn rõ khi left_profile / frontal)
IDX_CHIN = 8
IDX_NOSE_BRIDGE = 27
IDX_NOSE_TIP = 30
IDX_NOSE_BASE = 33
IDX_LEFT_EYE = list(range(36, 42))     # mắt trái-ảnh
IDX_RIGHT_EYE = list(range(42, 48))    # mắt phải-ảnh
IDX_MOUTH_LEFT = 48
IDX_MOUTH_RIGHT = 54


@dataclass
class PoseResult:
    yaw: float
    pitch: float
    roll: float
    pose_group: str
    landmarks3d: np.ndarray     # (68, 3) theo tọa độ khung hình (x_px, y_px, z_rel)
    kps5: np.ndarray            # (5, 2) dẫn xuất từ 68 điểm (ổn định hơn khi nghiêng sâu)
    visibility: float           # độ mở sườn mặt nhìn thấy (0..1)


def classify_pose_group(
    yaw: float,
    prev_group: str | None = None,
    frontal_max: float = 40.0,
    hysteresis: float = 5.0,
) -> str:
    """Phân loại tư thế vào 3 nhóm với dải trễ hysteresis quanh biên ±frontal_max."""
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
    """Trích góc Euler (yaw, pitch, roll) tính bằng độ theo phân rã chuẩn Y-X-Z (R = Ry · Rx · Rz).

    Giữ ổn định pitch và roll ngay cả khi |yaw| tiến sát 90° dưới góc lắp camera nghiêng nhẹ.
    """
    yaw = math.degrees(math.atan2(float(R_rel[0, 2]), float(R_rel[2, 2])))
    yaw = max(-95.0, min(95.0, yaw))
    sin_p = max(-1.0, min(1.0, -float(R_rel[1, 2])))
    pitch = math.degrees(math.asin(sin_p))
    if abs(sin_p) < 0.9999:
        roll = math.degrees(math.atan2(-float(R_rel[1, 0]), float(R_rel[1, 1])))
    else:
        roll = 0.0
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


def _fallback_landmarks_from_5pt(bbox: np.ndarray, kps: np.ndarray) -> tuple[float, float, float, np.ndarray]:
    """Dự phòng thuần hình học khi chạy trong unit test giả lập không nạp model ONNX."""
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
    xs = np.linspace(x1, x2, 17)
    pts68[:17, 0] = xs
    pts68[:17, 1] = np.linspace(eye_mid[1], y2, 17)
    pts68[IDX_CHIN] = [(x1 + x2) / 2.0, y2, 0.0]
    pts68[IDX_LEFT_EYE] = [le[0], le[1], 0.0]
    pts68[IDX_RIGHT_EYE] = [re[0], re[1], 0.0]
    pts68[27:36] = [nose[0], nose[1], 0.0]
    pts68[48:68] = [(lm[0] + rm[0]) / 2.0, (lm[1] + rm[1]) / 2.0, 0.0]
    pts68[IDX_MOUTH_LEFT] = [lm[0], lm[1], 0.0]
    pts68[IDX_MOUTH_RIGHT] = [rm[0], rm[1], 0.0]
    # Cột z toàn 0 đánh dấu đây là nhánh 5 điểm dự phòng (không có lưới 68 điểm 3D thật để tính parallax)
    return yaw, pitch, roll, pts68


def has_valid_parallax_landmarks(landmarks_seq: list[np.ndarray]) -> bool:
    """Kiểm tra chuỗi landmark có đủ mật độ lưới 68 điểm 2D thực (loại nhánh 5 điểm dự phòng chỉ có ~23 điểm rời rạc)."""
    if len(landmarks_seq) < 3:
        return False
    valid = 0
    for lm in landmarks_seq:
        if lm is not None and lm.ndim == 2 and lm.shape[0] >= 68 and lm.shape[1] >= 2:
            unique_xy = len(np.unique(np.round(lm[:, :2], 1), axis=0))
            if unique_xy >= 35:
                valid += 1
    return valid >= len(landmarks_seq)


def planar_parallax_score(landmarks_seq: list[np.ndarray]) -> float:
    """Đánh giá mức độ thị sai chuyển động 3D thật (0..1) dựa thuần túy trên phần dư homography 2D.

    Ảnh in hoặc màn hình điện thoại quét ngang qua ống kính biến đổi toàn bộ điểm theo
    phép chiếu phẳng (planar homography) -> phần dư khớp phẳng gần bằng 0, bất kể mô hình
    morphable 3DDFA có gán tọa độ z giả định hay không. Đầu người 3D thật khi bước ngang
    tạo ra phần dư phi phẳng lớn giữa sống mũi nhô cao và viền tai/má ở sâu.
    """
    if len(landmarks_seq) < 3:
        return 0.0
    p0 = landmarks_seq[0]
    pts0 = p0[:, :2].astype(np.float32)
    face_scale = float(np.linalg.norm(pts0.max(axis=0) - pts0.min(axis=0))) + 1e-6
    if face_scale < 8.0:
        return 0.0

    # So sánh frame đầu với frame giữa và cuối của chuỗi quỹ đạo
    indices = [len(landmarks_seq) // 2, len(landmarks_seq) - 1]
    rel_residuals = []
    for idx in indices:
        ptsi = landmarks_seq[idx][:, :2].astype(np.float32)
        H, _ = cv2.findHomography(pts0, ptsi, method=0)
        if H is None:
            continue
        proj = cv2.perspectiveTransform(pts0[None, :, :], H)[0]
        err = np.linalg.norm(proj - ptsi, axis=1)
        nose_err = float(np.mean(err[27:36]))
        all_err = float(np.percentile(err, 85))
        rel_residuals.append(max(nose_err, all_err) / face_scale)

    if not rel_residuals:
        return 0.0
    parallax_2d = float(np.max(rel_residuals))

    # Chuẩn hóa về 0..1 chỉ từ phần dư phi phẳng 2D (không cộng điểm từ tọa độ z của 3DDFA)
    s_par = min(1.0, parallax_2d / 0.012)
    return float(round(s_par, 4))


class PoseEstimator3D:
    REQUIRED_FILES = (
        "3ddfa_v2_mb1_120x120.onnx",
        "tddfa_u_base.bin",
        "tddfa_w_shp_base.bin",
        "tddfa_w_exp_base.bin",
        "tddfa_param_mean.bin",
        "tddfa_param_std.bin",
    )

    def __init__(self, models_dir: Path | None = None, frontal_max_yaw: float = 40.0, hysteresis: float = 5.0):
        self.frontal_max = float(frontal_max_yaw)
        self.hysteresis = float(hysteresis)
        self.size = 120
        self.session = None
        if models_dir is not None and all((models_dir / f).exists() for f in self.REQUIRED_FILES):
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.log_severity_level = 3
            self.session = ort.InferenceSession(
                str(models_dir / "3ddfa_v2_mb1_120x120.onnx"),
                so,
                providers=["CPUExecutionProvider"],
            )
            self.in_name = self.session.get_inputs()[0].name
            self.u_base = np.fromfile(models_dir / "tddfa_u_base.bin", np.float32).reshape(-1, 1)
            self.w_shp = np.fromfile(models_dir / "tddfa_w_shp_base.bin", np.float32).reshape(204, 40)
            self.w_exp = np.fromfile(models_dir / "tddfa_w_exp_base.bin", np.float32).reshape(204, 10)
            self.param_mean = np.fromfile(models_dir / "tddfa_param_mean.bin", np.float32)
            self.param_std = np.fromfile(models_dir / "tddfa_param_std.bin", np.float32)
            p0 = self.param_mean[:12].reshape(3, 4)[:, :3]
            self.r0_ortho = _orthogonalize_rows(p0)
        else:
            log.warning(
                "Chua co du bo file 3DDFA_V2 trong %s -> dung uoc luong 5 diem du phong (khong du luoi 68 diem cho parallax goc >40 do)",
                models_dir,
            )

    def estimate(self, img: np.ndarray, face, prev_group: str | None = None) -> PoseResult:
        if self.session is None:
            yaw, pitch, roll, pts68 = _fallback_landmarks_from_5pt(face.bbox, face.kps)
            grp = classify_pose_group(yaw, prev_group, self.frontal_max, self.hysteresis)
            vis = float(max(0.25, 1.0 - abs(yaw) / 110.0))
            return PoseResult(yaw, pitch, roll, grp, pts68, face.kps.astype(np.float32), vis)

        roi = _parse_roi_box(face.bbox)
        crop = _crop_roi(img, roi, self.size)
        inp = (crop.astype(np.float32).transpose(2, 0, 1)[None] - 127.5) / 128.0
        raw = self.session.run(None, {self.in_name: inp})[0].reshape(-1).astype(np.float32)
        param = raw * self.param_std + self.param_mean

        P = param[:12].reshape(3, 4)
        R_mat = P[:, :3]
        offset = P[:, 3:4]
        alpha_shp = param[12:52, None]
        alpha_exp = param[52:62, None]

        R_ortho = _orthogonalize_rows(R_mat)
        R_rel = R_ortho @ self.r0_ortho.T
        yaw, pitch, roll = euler_from_relative_rotation(R_rel)

        verts = (self.u_base + self.w_shp @ alpha_shp + self.w_exp @ alpha_exp).reshape(3, 68, order="F")
        pts3 = R_mat @ verts + offset
        # similar_transform về khung hình gốc
        pts3[0, :] -= 1.0
        pts3[2, :] -= 1.0
        pts3[1, :] = self.size - pts3[1, :]
        sx, sy, ex, ey = roi
        scale_x = (ex - sx) / self.size
        scale_y = (ey - sy) / self.size
        pts3[0, :] = pts3[0, :] * scale_x + sx
        pts3[1, :] = pts3[1, :] * scale_y + sy
        pts3[2, :] *= (scale_x + scale_y) / 2.0
        pts3[2, :] -= float(np.min(pts3[2, :]))

        pts68 = pts3.T.astype(np.float32)  # (68, 3)
        kps5 = _kps5_from_68(pts68)
        grp = classify_pose_group(yaw, prev_group, self.frontal_max, self.hysteresis)

        # Độ mở sườn mặt nhìn thấy: đo bề rộng giữa đỉnh mũi và đường viền tai/hàm phía hướng về camera
        face_w = max(float(face.bbox[2] - face.bbox[0]), 1.0)
        if yaw < -15.0:
            cheek_span = float(np.max(pts68[IDX_JAW_RIGHT, 0]) - pts68[IDX_NOSE_TIP, 0])
        elif yaw > 15.0:
            cheek_span = float(pts68[IDX_NOSE_TIP, 0] - np.min(pts68[IDX_JAW_LEFT, 0]))
        else:
            cheek_span = float(np.max(pts68[IDX_JAW_RIGHT, 0]) - np.min(pts68[IDX_JAW_LEFT, 0])) * 0.5
        vis = float(np.clip(cheek_span / (face_w * 0.35), 0.0, 1.0))

        return PoseResult(yaw, pitch, roll, grp, pts68, kps5, vis)
