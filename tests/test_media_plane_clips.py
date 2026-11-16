from __future__ import annotations

import http.server
import socketserver
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest
import yaml

from app.media_clips import (
    fetch_playback_clip,
    render_burnin_mp4,
    verify_clip_sidecar,
    write_signed_sidecar,
)


def _make_dummy_mp4(path: Path, frames: int = 12, size: tuple[int, int] = (320, 240)) -> None:
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 15.0, size)
    assert w.isOpened()
    for i in range(frames):
        img = np.full((size[1], size[0], 3), 40 + i * 10, dtype=np.uint8)
        w.write(img)
    w.release()


def test_mediamtx_config_enforces_audio_strip_and_tmpfs():
    cfg_path = Path(__file__).resolve().parent.parent / "deploy" / "mediamtx.yml"
    raw = cfg_path.read_text(encoding="utf-8")
    assert "${FACEGATE_RING_DIR}" in raw
    data = yaml.safe_load(raw)
    assert data["playback"] is True
    main_p = data["paths"]["gate1_main"]
    sub_p = data["paths"]["gate1_sub"]
    assert "-map 0:v:0 -c:v copy -an" in main_p["runOnInit"]
    assert "-map 0:v:0 -c:v copy -an" in sub_p["runOnInit"]
    assert main_p["runOnInitRestart"] is True
    assert main_p["recordFormat"] == "fmp4"
    assert main_p["recordDeleteAfter"] == "10m"


def test_playback_fetch_hmac_tamper_detection_and_burnin_export(tmp_path: Path):
    src_mp4 = tmp_path / "src_segment.mp4"
    _make_dummy_mp4(src_mp4)
    mp4_bytes = src_mp4.read_bytes()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path.startswith("/get?"):
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.end_headers()
                self.wfile.write(mp4_bytes)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as httpd:
        port = httpd.server_address[1]
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        data_root = tmp_path / "data"
        dst_mp4 = data_root / "clips" / "20261113" / "101500_1.mp4"
        ok = fetch_playback_clip(
            f"http://127.0.0.1:{port}", "gate1_main",
            start_ts=1700000100.0, end_ts=1700000102.0,
            pre_sec=2.0, post_sec=1.5, dst_mp4=dst_mp4,
        )
        httpd.shutdown()
    assert ok and dst_mp4.is_file()

    cfg = {"audit_and_safety": {"hmac_secret": "unit-test-secret-key"}}
    timeline = [{
        "offset_ms": 2000,
        "objects": [{
            "track_id": 1,
            "kind": "face",
            "bbox_norm": [0.2, 0.15, 0.5, 0.65],
            "color_role": "ok",
            "label": "NV001 · Nguyễn Văn A",
        }],
    }]
    sidecar, clip_sha, _ = write_signed_sidecar(
        dst_mp4, data_root, appearance_id=1, camera_id="gate-1",
        clip_start_server_ns=1700000098_000_000_000, event_offset_ms=2000,
        timeline=timeline, cfg=cfg,
    )
    assert len(clip_sha) == 64

    valid, msg, meta = verify_clip_sidecar(dst_mp4, sidecar, cfg=cfg)
    assert valid and msg == "ok" and meta["audio_stripped"] is True

    # Xuat MP4 dong dau burn-in phuc vu HR doi soat khieu nai luong
    burned = tmp_path / "burnin_1.mp4"
    out = render_burnin_mp4(dst_mp4, sidecar, burned, cfg=cfg)
    assert out.is_file() and out.stat().st_size > 512

    # Sua 1 byte trong file MP4 -> phat hien ngay sai SHA-256
    tampered = bytearray(dst_mp4.read_bytes())
    tampered[-1] ^= 0xFF
    dst_mp4.write_bytes(bytes(tampered))
    valid2, msg2, _ = verify_clip_sidecar(dst_mp4, sidecar, cfg=cfg)
    assert not valid2 and "SHA-256" in msg2
    with pytest.raises(ValueError):
        render_burnin_mp4(dst_mp4, sidecar, tmp_path / "burnin_fail.mp4", cfg=cfg)
