"""Bộ điều phối chống giả mạo 4 lớp định tuyến theo tư thế đầu (Pose-Gated Multi-Layer Anti-Spoofing).

- Nhánh A (|yaw| <= frontal_max_yaw, mặc định 40°):
    MiniFASNetV2 + MiniFASNetV1SE (Apache-2.0) kết hợp kiểm tra vật chứa giả mạo.
- Nhánh B (40° < |yaw| <= 90°):
    Không đánh rớt bằng MiniFASNet (tránh chặn nhầm người thật), mà bắt buộc vượt đủ 3 cổng:
      1. Context & Body Coupling (không có màn hình điện thoại/tablet, không có 2 mặt/1 thân, gắn đúng cơ thể)
      2. Temporal 3D Motion Parallax (quỹ đạo >= min_frames, dịch ngang >= min_travel_ratio, chiều sâu 3D thật)
      3. High-Frequency Moiré FFT (không có vân lưới pixel màn hình phát video quay sẵn)
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from .context_detector import evaluate_face_context
from .pose3d import planar_parallax_score
from .quality import _visible_skin_roi

log = logging.getLogger("liveness")

MODELS = (("MiniFASNetV2.onnx", 2.7), ("MiniFASNetV1SE.onnx", 4.0))
INPUT = 80


def _crop(img: np.ndarray, bbox: np.ndarray, scale: float) -> np.ndarray:
    h_img, w_img = img.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in bbox)
    bw, bh = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
    s = min((h_img - 1) / bh, (w_img - 1) / bw, scale)
    cx, cy, nw, nh = x1 + bw / 2, y1 + bh / 2, bw * s, bh * s
    a, b = max(0, int(cx - nw / 2)), max(0, int(cy - nh / 2))
    c, d = min(w_img - 1, int(cx + nw / 2)), min(h_img - 1, int(cy + nh / 2))
    return cv2.resize(img[b:d + 1, a:c + 1], (INPUT, INPUT))


def moire_screen_score(crop_bgr: np.ndarray) -> float:
    """Phát hiện đỉnh phổ tuần hoàn cục bộ (vân Moiré lưới pixel màn hình) chuẩn hóa theo từng vành bán kính.
    Trả về điểm 0..1 (da tự nhiên mượt hoặc mờ có phổ đẳng hướng -> gần 0; lưới màn hình có đỉnh nhọn -> > 0.65).
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return 0.0
    gray = cv2.cvtColor(cv2.resize(crop_bgr, (96, 96)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    std = float(gray.std())
    if std < 2.0:
        return 0.0
    gray = (gray - float(gray.mean())) / std
    win = np.outer(np.hanning(96), np.hanning(96)).astype(np.float32)
    spec = np.abs(np.fft.fftshift(np.fft.fft2(gray * win)))
    yy, xx = np.mgrid[:96, :96]
    rr = np.hypot(yy - 48.0, xx - 48.0)

    # Chuẩn hóa theo từng vành bán kính hẹp dr=3 để loại bỏ suy hao tự nhiên 1/f hoặc Gaussian blur đẳng hướng
    max_anisotropy = 0.0
    noise_floor = 6.0
    for r0 in range(12, 39, 3):
        shell = spec[(rr >= r0) & (rr < r0 + 3)]
        if shell.size < 16:
            continue
        med = max(float(np.median(shell)), noise_floor)
        ratio = float(np.max(shell)) / med
        if ratio > max_anisotropy:
            max_anisotropy = ratio
    return float(np.clip((max_anisotropy - 5.5) / 8.0, 0.0, 1.0))


class Liveness:
    def __init__(self, models_dir: Path, cfg: dict | None = None):
        import onnxruntime as ort

        missing = [n for n, _ in MODELS if not (models_dir / n).exists()]
        if missing:
            raise FileNotFoundError(f"Thieu model chong gia mao {missing}. Chay: python scripts/download_models.py")
        so = ort.SessionOptions()
        so.log_severity_level = 3
        self.sessions = [(ort.InferenceSession(str(models_dir / n), so, providers=["CPUExecutionProvider"]), sc)
                         for n, sc in MODELS]

        cfg = cfg or {}
        rc = cfg.get("recognition", {})
        lc = cfg.get("liveness", {})
        self.frontal_max_yaw = float(rc.get("frontal_max_yaw", 40.0))
        self.fas_thr = float(lc.get("threshold", 0.70))
        self.spoof_below = float(lc.get("spoof_below", 0.35))
        self.min_frames = int(lc.get("min_frames", 4))
        self.window = int(lc.get("window", 8))

        cc = lc.get("context", {})
        self.ctx_enabled = bool(cc.get("enabled", True))
        self.require_body = bool(cc.get("require_body_coupling", True))
        rb = cc.get("face_body_ratio", [0.12, 0.65])
        self.ratio_bounds = (float(rb[0]), float(rb[1]))
        self.screen_overlap = float(cc.get("screen_face_overlap", 0.80))
        self.screen_window = int(cc.get("screen_window", 5))
        self.screen_min_hits = int(cc.get("screen_min_hits", 3))

        pc = lc.get("parallax", {})
        self.par_enabled = bool(pc.get("enabled", True))
        self.par_min_frames = int(pc.get("min_frames", 6))
        self.par_min_travel = float(pc.get("min_travel_ratio", 0.08))
        self.par_min_depth = float(pc.get("min_depth_score", 0.55))

        mc = lc.get("moire", {})
        self.moire_enabled = bool(mc.get("enabled", True))
        self.max_screen_score = float(mc.get("max_screen_score", 0.65))

    @property
    def active_modes(self) -> list[str]:
        modes = ["texture_fas"]
        if self.ctx_enabled:
            modes += ["context_medium", "body_coupling"]
        if self.par_enabled:
            modes.append("parallax_3d")
        if self.moire_enabled:
            modes.append("moire_fft")
        return modes

    def real_prob(self, img: np.ndarray, bbox: np.ndarray) -> float:
        """Xac suat khuon mat la nguoi that (0..1) bang MiniFASNet (dung cho |yaw| <= frontal_max_yaw)."""
        p = np.zeros(3, np.float64)
        for sess, scale in self.sessions:
            x = _crop(img, bbox, scale).astype(np.float32).transpose(2, 0, 1)[None]
            z = sess.run(None, {sess.get_inputs()[0].name: x})[0][0].astype(np.float64)
            e = np.exp(z - z.max())
            p += e / e.sum()
        return float(p[1] / len(self.sessions))

    def observe(self, track, face, frame: np.ndarray, pose, ctx_scene=None,
                all_faces: list | None = None, quality_ok: bool = True) -> None:
        """Thu thập mẫu chống giả mạo trên từng frame phát hiện của track."""
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (max(0, int(face.bbox[0])), max(0, int(face.bbox[1])),
                          min(w, int(face.bbox[2])), min(h, int(face.bbox[3])))
        crop = frame[y1:y2, x1:x2]
        vis_roi = _visible_skin_roi(crop, pose.yaw) if crop.size else crop
        m_score = moire_screen_score(vis_roi) if (self.moire_enabled and quality_ok and vis_roi.size) else None

        ctx_info = {
            "medium_clean": True,
            "medium_nearby": False,
            "body_coupled": True,
            "spoof_reason": None,
            "medium_label": None,
        }
        if self.ctx_enabled and ctx_scene is not None:
            ctx_info = evaluate_face_context(
                face.bbox,
                ctx_scene,
                all_faces=all_faces,
                require_body=self.require_body,
                ratio_bounds=self.ratio_bounds,
                screen_face_overlap=self.screen_overlap,
            )

        track.context_history.append({
            "yaw": float(pose.yaw),
            "medium_clean": ctx_info["medium_clean"],
            "medium_nearby": ctx_info.get("medium_nearby", False),
            "body_coupled": ctx_info["body_coupled"],
            "spoof_reason": ctx_info["spoof_reason"],
            "moire_score": m_score,
        })

        # Chỉ đưa frame chính diện / bán nghiêng nhẹ vào MiniFASNet
        if quality_ok and abs(float(pose.yaw)) <= self.frontal_max_yaw:
            track.live.append(self.real_prob(frame, face.bbox))
            track.live = track.live[-self.window:]

    def evaluate_track(self, track, frame_shape=None) -> str:
        """Trả về 'live' | 'checking' | 'spoof' và cập nhật liveness_mode, liveness_detail, spoof_reason."""
        if track.spoof:
            return "spoof"

        hist = list(track.context_history)
        # 1. Cổng ngữ cảnh: phát hiện mặt trong màn hình trên tỉ lệ đáng kể các frame gần nhất (vd >= 3/5) hoặc 2 mặt/1 thân
        recent_scr = hist[-self.screen_window:] if hist else []
        screen_hits = sum(1 for h in recent_scr if h["spoof_reason"] == "screen_detected")
        dual_hits = sum(1 for h in hist[-self.screen_window:] if h["spoof_reason"] == "multi_face_single_body")
        if screen_hits >= self.screen_min_hits:
            return self._mark_spoof(track, "screen_detected", 0.05, "context_parallax", medium_clean=False)
        if dual_hits >= 2:
            return self._mark_spoof(track, "multi_face_single_body", 0.08, "context_parallax", body_coupled=False)

        moire_vals = [h["moire_score"] for h in hist if h.get("moire_score") is not None]
        moire_med = float(np.median(moire_vals[-6:])) if moire_vals else 0.0
        moire_ready = (not self.moire_enabled) or (len(moire_vals) >= 4)
        moire_ok = (not self.moire_enabled) or (moire_ready and moire_med <= self.max_screen_score)
        if self.moire_enabled and len(moire_vals) >= 4 and moire_med > self.max_screen_score + 0.08:
            return self._mark_spoof(track, "moire_pattern", round(1.0 - moire_med, 4), "context_parallax",
                                    moire_ok=False)

        # 2. Nhánh A (Ưu tiên Best-shot chính diện nếu có đủ mẫu MiniFASNet hoặc đang chốt ở frontal)
        if len(track.live) >= self.min_frames:
            fas_mean = float(np.mean(track.live))
            body_ok = (not hist) or (sum(1 for h in hist[-4:] if h["body_coupled"]) >= max(1, len(hist[-4:]) // 2))
            moire_a_ok = (not self.moire_enabled) or (not moire_ready and moire_med <= self.max_screen_score) or moire_ok
            track.liveness_mode = "hybrid" if hist else "texture_fas"
            track.liveness_score = round(fas_mean, 4)
            track.liveness_detail = {
                "fas": round(fas_mean, 4),
                "medium_clean": True,
                "body_coupled": body_ok,
                "parallax_ok": None,
                "moire_ok": moire_a_ok,
                "reason": None,
            }
            if fas_mean < self.spoof_below:
                return self._mark_spoof(track, "texture_spoof", fas_mean, track.liveness_mode, fas=fas_mean)
            if fas_mean >= self.fas_thr and body_ok and moire_a_ok:
                if track.employee_id is not None:
                    track.verification_tier = "verified"
                return "live"
            return "checking"

        # Nếu đang nhắm chốt ở ngăn frontal mà chưa đủ min_frames -> tiếp tục chờ
        if track.matched_group == "frontal" and len(track.buckets.get("left_profile", [])) < self.min_frames \
                and len(track.buckets.get("right_profile", [])) < self.min_frames:
            return "checking"

        # 3. Nhánh B: Góc nghiêng sâu (40° < |yaw| <= 90°) — Kiểm định Context + Body + 3D Motion Parallax + Moiré
        track.liveness_mode = "context_parallax"
        if len(track.pose_history) < self.par_min_frames:
            return "checking"

        recent_ctx = hist[-self.par_min_frames:] if hist else []
        if recent_ctx and self.require_body:
            unattached = sum(1 for h in recent_ctx if h["spoof_reason"] == "unattached_face")
            if unattached >= max(3, len(recent_ctx) - 1):
                return self._mark_spoof(track, "unattached_face", 0.12, "context_parallax", body_coupled=False)

        frame_w = float(frame_shape[1]) if frame_shape is not None and len(frame_shape) >= 2 else 640.0
        xs = [float(p[4][0]) for p in track.pose_history]
        travel_ratio = (max(xs) - min(xs)) / max(frame_w, 1.0)
        if self.par_enabled and travel_ratio < self.par_min_travel:
            track.liveness_detail = {
                "fas": None,
                "medium_clean": True,
                "body_coupled": True,
                "parallax_ok": False,
                "travel_ratio": round(travel_ratio, 4),
                "moire_ok": moire_ok,
                "reason": "waiting_motion",
            }
            return "checking"

        lm_seq = [p[5] for p in list(track.pose_history)[-max(self.par_min_frames, 8):] if p[5] is not None]
        par_score = planar_parallax_score(lm_seq) if self.par_enabled else 1.0
        par_ok = (not self.par_enabled) or (par_score >= self.par_min_depth)
        body_ok = (not recent_ctx) or (sum(1 for h in recent_ctx if h["body_coupled"]) >= len(recent_ctx) // 2 + 1)

        if self.par_enabled and len(lm_seq) >= self.par_min_frames and par_score < self.par_min_depth * 0.65:
            return self._mark_spoof(track, "planar_no_parallax", par_score, "context_parallax",
                                    parallax_ok=False, parallax_score=par_score)

        if self.moire_enabled and not moire_ready:
            return "checking"

        if not moire_ok:
            return self._mark_spoof(track, "moire_pattern", round(1.0 - moire_med, 4), "context_parallax",
                                    moire_ok=False)

        if par_ok and body_ok and moire_ok:
            comp_score = round(float(0.65 * par_score + 0.35 * (1.0 - moire_med)), 4)
            track.liveness_score = comp_score
            track.liveness_detail = {
                "fas": None,
                "medium_clean": True,
                "body_coupled": body_ok,
                "parallax_ok": True,
                "parallax_score": round(par_score, 4),
                "moire_ok": True,
                "reason": None,
            }
            if track.employee_id is not None and track.verification_tier != "verified":
                track.verification_tier = "profile_verified"
            return "live"

        return "checking"

    @staticmethod
    def _mark_spoof(track, reason: str, score: float, mode: str, **flags) -> str:
        track.spoof = True
        track.spoof_reason = reason
        track.liveness_mode = mode
        track.liveness_score = round(float(score), 4)
        track.liveness_detail = {
            "fas": flags.get("fas"),
            "medium_clean": flags.get("medium_clean", True),
            "body_coupled": flags.get("body_coupled", True),
            "parallax_ok": flags.get("parallax_ok"),
            "parallax_score": flags.get("parallax_score"),
            "moire_ok": flags.get("moire_ok", True),
            "reason": reason,
        }
        return "spoof"


def create_liveness(cfg: dict) -> Liveness | None:
    lc = cfg.get("liveness", {})
    if not lc.get("enabled", False):
        log.warning("Chong gia mao dang TAT (liveness.enabled=false): anh/man hinh dien thoai co the duoc cham cong")
        return None
    return Liveness(cfg["_paths"]["models"], cfg=cfg)
