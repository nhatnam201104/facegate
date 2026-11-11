"""Kiểm thử nhận diện nguồn camera Linux (/dev/video*) và vẽ nhãn tiếng Việt."""
import sys

import numpy as np

from app.camera import Camera, WEBCAM_BACKENDS, is_webcam_source
from app.draw import GREEN, draw_overlay


def test_webcam_source_detection_linux_and_index():
    assert is_webcam_source(0)
    assert is_webcam_source("/dev/video0")
    assert is_webcam_source("/dev/video12")
    assert not is_webcam_source("data/demo.mp4")
    assert not is_webcam_source("rtsp://192.168.1.64:554/stream")


def test_camera_dev_video_not_treated_as_file():
    cam = Camera("/dev/video0")
    assert cam.kind == "webcam"
    assert cam.is_file is False
    assert cam.label == "/dev/video0"

    fcam = Camera("data/sample.mp4")
    assert fcam.kind == "file"
    assert fcam.is_file is True

    rcam = Camera("rtsp://admin:secret@192.168.1.64:554/Streaming/Channels/102")
    assert rcam.kind == "rtsp"
    assert rcam.is_file is False
    assert "secret" not in rcam.label


def test_linux_prefers_v4l2_backend():
    if sys.platform.startswith("linux"):
        assert WEBCAM_BACKENDS[0][0] == "V4L2"


def test_draw_overlay_vietnamese_text():
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    out = draw_overlay(
        frame,
        [{"bbox": (20, 40, 180, 200), "color": GREEN, "label": "Nguyễn Văn Đạt · thật 0.92"}],
        "opencv | detect 5.0 FPS | chống giả mạo",
    )
    assert out.shape == frame.shape
    assert int(out.max()) > 0
