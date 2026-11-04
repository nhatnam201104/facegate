"""Doc video trong thread rieng, chi giu frame moi nhat (tranh tre don)."""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("camera")
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")  # MSMF mo webcam nhanh hon

# Webcam tren Windows: MSMF ~30 FPS o 720p, DSHOW chi ~10 FPS -> uu tien MSMF, DSHOW du phong
WEBCAM_BACKENDS = [("MSMF", cv2.CAP_MSMF), ("DSHOW", cv2.CAP_DSHOW)] if os.name == "nt" else [("ANY", cv2.CAP_ANY)]
BLACK_LEVEL = 8      # pixel sang nhat (sau khi lay mau thua) duoi muc nay = frame den
BLACK_SECS = 4.0     # den lien tuc qua thoi gian nay -> driver bi ket, mo lai thiet bi


def is_black(frame: np.ndarray) -> bool:
    return int(frame[::16, ::16].max()) < BLACK_LEVEL


def _parse_source(src):
    if isinstance(src, int):
        return src
    s = str(src).strip()
    return int(s) if s.isdigit() else s


def can_read_video(path: str) -> bool:
    """Kiem tra file video doc duoc it nhat 1 frame (truoc khi doi nguon)."""
    cap = cv2.VideoCapture(path)
    try:
        ok, _ = cap.read() if cap.isOpened() else (False, None)
        return bool(ok)
    finally:
        cap.release()


class Camera:
    def __init__(self, source, loop_file: bool = True, label: str | None = None):
        self.source = _parse_source(source)
        self.is_file = isinstance(self.source, str) and not self.source.lower().startswith(("rtsp", "http"))
        if isinstance(self.source, int):
            self.kind, default_label = "webcam", f"Webcam {self.source}"
        elif self.is_file:
            self.kind, default_label = "file", Path(self.source).name
        else:  # an mat khau trong URL RTSP
            self.kind, default_label = "rtsp", re.sub(r"//[^@/]+@", "//***@", self.source)
        self.label = label or default_label
        self.loop_file = loop_file
        self.frame: np.ndarray | None = None
        self.frame_id = 0
        self.frame_ts = 0.0
        self.connected = False
        self.black = False                     # nguon dang tra frame den (nap che, driver ket...)
        self.fps_in = 0.0
        self._backend_idx = 0
        # che do cho: van grab() de frame khong bi cu, nhung chi giai ma/chuyen mau moi idle_interval giay
        self.idle = False
        self.idle_interval = 0.25
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._recorders: list[list] = []
        self._thread = threading.Thread(target=self._run, daemon=True, name="camera")

    def start(self) -> "Camera":
        self._thread.start()
        return self

    def stop(self, wait: float = 0.0) -> None:
        """Dung doc video. `wait` > 0: cho thread nha thiet bi (webcam) truoc khi mo nguon khac."""
        self._stop.set()
        if wait > 0 and self._thread.is_alive():
            self._thread.join(timeout=wait)
            if self._thread.is_alive():
                log.warning("Nguon %s chua nha thiet bi sau %.0fs", self.label, wait)

    def describe(self) -> dict:
        return {"kind": self.kind, "label": self.label, "connected": self.connected, "black": self.black}

    def _open(self):
        if not isinstance(self.source, int):
            cap = cv2.VideoCapture(self.source)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            return cap
        # thu lan luot cac backend, bat dau tu backend uu tien hien tai
        n = len(WEBCAM_BACKENDS)
        for i in range(n):
            name, api = WEBCAM_BACKENDS[(self._backend_idx + i) % n]
            cap = cv2.VideoCapture(self.source, api)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                log.info("Mo webcam %s bang %s", self.source, name)
                return cap
            cap.release()
        return cap

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            cap = self._open()
            if not cap.isOpened():
                self.connected = False
                log.warning("Khong mo duoc nguon video %s, thu lai sau %.0fs", self.source, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 15)
                continue
            self.connected = True
            file_fps = (cap.get(cv2.CAP_PROP_FPS) or 25.0) if self.is_file else 0.0
            log.info("Da ket noi nguon video %s", self.source)
            t_last, n = time.time(), 0
            black_since, got_frame = None, False
            last_retrieve = 0.0
            while not self._stop.is_set():
                t0 = time.time()
                if self.idle and not self.is_file:
                    if not cap.grab():
                        ok, frame = False, None
                    elif t0 - last_retrieve < self.idle_interval:
                        continue
                    else:
                        ok, frame = cap.retrieve()
                else:
                    ok, frame = cap.read()
                last_retrieve = t0
                if not ok:
                    if self.is_file and self.loop_file:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    log.warning("Mat tin hieu video, ket noi lai...")
                    break
                if not self.is_file:  # watchdog: driver ket van tra frame den voi read() == True
                    if not is_black(frame):
                        black_since, self.black, backoff = None, False, 1.0
                    elif black_since is None:
                        black_since = time.time()
                    elif time.time() - black_since > BLACK_SECS:
                        self.black = True
                        self._backend_idx += 1  # lan sau thu backend khac
                        log.warning("Nguon %s tra hinh den %.0fs, mo lai thiet bi sau %.0fs",
                                    self.label, BLACK_SECS, backoff)
                        break
                got_frame = True
                with self._lock:
                    self.frame = frame
                    self.frame_id += 1
                    self.frame_ts = time.time()
                    for rec in self._recorders:
                        rec.append(frame)
                n += 1
                if time.time() - t_last >= 1.0:
                    self.fps_in = n / (time.time() - t_last)
                    t_last, n = time.time(), 0
                if self.is_file:  # phat file theo dung toc do thuc
                    time.sleep(max(0.0, 1.0 / file_fps - (time.time() - t0)))
            cap.release()
            self.connected, self.fps_in = False, 0.0
            # hinh den / mo duoc nhung khong doc duoc frame (webcam dang bi app khac giu): cho roi thu lai
            if (self.black or not got_frame) and not self.is_file and not self._stop.is_set():
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 5)

    def read(self) -> tuple[int, np.ndarray | None]:
        with self._lock:
            return self.frame_id, self.frame

    def record(self, seconds: float, max_frames: int = 400) -> list[np.ndarray]:
        """Thu cac frame trong `seconds` giay (dung cho dang ky tu camera)."""
        buf: list = []
        with self._lock:
            self._recorders.append(buf)
        try:
            time.sleep(seconds)
        finally:
            with self._lock:
                self._recorders.remove(buf)
        step = max(1, len(buf) // max_frames)
        return buf[::step]
