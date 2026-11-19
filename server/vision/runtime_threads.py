"""Cau hinh luong suy luan dung chung (OpenCV + ONNX Runtime).

Mac dinh ONNX Runtime de cac luong intra-op "spin" (quay vong cho viec) sau moi lan Run; voi nhip
nhan dien 5-6 lan/giay va 3 session (YOLOX + 2 MiniFASNet) cac luong gan nhu spin lien tuc, an ~2 loi CPU
du khong co viec. OpenCV mac dinh dung moi loi logic, thua voi model nho (YuNet/SFace).
Do tren 1 camera (6 lan/giay): 2.54 loi -> 0.62 loi, chu ky TB 46 -> 55 ms (specs/004-multi-camera-support).
"""
from __future__ import annotations

import logging

import cv2

log = logging.getLogger(__name__)


def apply_cv_threads(cfg: dict) -> int:
    """Dat so luong OpenCV theo pipeline.cv_threads (<= 0: giu mac dinh cua OpenCV). Tra ve so luong hien hanh."""
    n = int(cfg.get("pipeline", {}).get("cv_threads", 0))
    if n > 0:
        cv2.setNumThreads(n)
    cur = cv2.getNumThreads()
    log.info("OpenCV threads = %d (pipeline.cv_threads=%d)", cur, n)
    return cur


def ort_session_options(cfg: dict | None):
    """SessionOptions chung cho moi session ONNX Runtime: tat spin khi pipeline.ort_allow_spinning=false."""
    import onnxruntime as ort

    so = ort.SessionOptions()
    so.log_severity_level = 3
    spin = bool(((cfg or {}).get("pipeline") or {}).get("ort_allow_spinning", False))
    flag = "1" if spin else "0"
    so.add_session_config_entry("session.intra_op.allow_spinning", flag)
    so.add_session_config_entry("session.inter_op.allow_spinning", flag)
    return so
