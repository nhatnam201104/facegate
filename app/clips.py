"""Ghi clip video cho moi lan mot khuon mat xuat hien (1 track), co pre-roll / post-roll.

Pipeline day frame da ve khung (cung hinh voi man hinh truc tiep) vao push(). Viec ma hoa video chay
trong 1 thread rieng qua hang doi de khong lam cham vong nhan dien.

Vong doi 1 clip: start() -> [frame...] -> (dong khi stop() + post-roll, hoac khi qua max_seconds)
-> ghi xong -> on_done(duong_dan_tuong_doi | None). Khi stop(keep=False) file bi xoa.
"""
from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("clips")

# WebM/VP8 phat duoc truc tiep tren trinh duyet; mp4/avc1 du phong (Windows Media Foundation)
FORMATS = (("webm", "VP80"), ("mp4", "avc1"))
QUEUE_MAX = 900
CLEANUP_EVERY = 3600.0


class _Session:
    def __init__(self, sid: int, base: Path, start: float):
        self.sid, self.base, self.start = sid, base, start
        self.stop_at: float | None = None
        self.closing = False                 # da gui lenh dong bo ghi
        self.decided = False                 # da goi stop()
        self.keep = True
        self.on_done = None
        self.result: tuple[Path | None, int] | None = None   # (file, so frame) sau khi ghi xong


class ClipRecorder:
    def __init__(self, cfg: dict):
        c = cfg.get("clips", {})
        self.enabled = bool(c.get("enabled", True))
        self.root: Path = cfg["_paths"]["data"]
        self.dir: Path = cfg["_paths"]["clips"]
        self.fps = float(c.get("fps", 10))
        self.width = int(c.get("width", 640))
        self.pre = float(c.get("pre_seconds", 2))
        self.post = float(c.get("post_seconds", 1.5))
        self.max_len = float(c.get("max_seconds", 60))
        self.retention_days = float(c.get("retention_days", 30))
        self._fmt: tuple[str, str] | None = None
        self._pre: deque = deque()
        self._sessions: dict[int, _Session] = {}
        self._ids = itertools.count(1)
        self._last_push = 0.0
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._dropped = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._writer, daemon=True, name="clips")
        if self.enabled:
            self._thread.start()

    # ------------------------------------------------------------ pipeline API
    def push(self, frame: np.ndarray, now: float | None = None) -> None:
        """Goi tu thread pipeline voi frame da ve overlay."""
        if not self.enabled:
            return
        now = now or time.time()
        if now - self._last_push < 1.0 / self.fps - 1e-3:
            return
        self._last_push = now
        h, w = frame.shape[:2]
        s = min(1.0, self.width / w)
        small = cv2.resize(frame, (int(w * s) // 2 * 2, int(h * s) // 2 * 2)) if s < 1 else frame.copy()
        with self._lock:
            self._pre.append((now, small))
            while self._pre and now - self._pre[0][0] > self.pre:
                self._pre.popleft()
            self._tick(now)
            for ses in self._sessions.values():
                if not ses.closing:
                    self._put(("frame", ses, small))

    def _tick(self, now: float) -> None:
        """Dong clip da het post-roll / qua do dai toi da (dang giu self._lock)."""
        for ses in self._sessions.values():
            if not ses.closing and ((ses.stop_at is not None and now >= ses.stop_at)
                                    or now - ses.start > self.max_len):
                ses.closing = True
                self._put(("close", ses), force=True)

    def start(self) -> int | None:
        if not self.enabled:
            return None
        sid = next(self._ids)
        base = self.dir / datetime.now().strftime("%Y%m%d") / f"{datetime.now():%H%M%S}_{sid}"
        with self._lock:
            ses = _Session(sid, base, time.time() - self.pre)
            self._sessions[sid] = ses
            now = time.time()
            for ts, fr in self._pre:
                if now - ts <= self.pre:
                    self._put(("frame", ses, fr))
        return sid

    def stop(self, sid: int | None, keep: bool, on_done=None) -> None:
        """Ket thuc clip (sau post-roll neu keep). on_done(rel_path | None) duoc goi khi file da ghi xong."""
        if sid is None:
            if on_done:
                on_done(None)
            return
        with self._lock:
            ses = self._sessions.get(sid)
            if ses is None:
                if on_done:
                    on_done(None)
                return
            ses.decided, ses.keep, ses.on_done = True, keep, on_done
            if not ses.closing:
                ses.stop_at = time.time() + (self.post if keep else 0.0)
                if not keep:
                    ses.closing = True
                    self._put(("close", ses), force=True)
            elif ses.result is not None:   # da dong vi qua max_seconds
                self._finish(ses)

    def close(self) -> None:
        with self._lock:
            for ses in self._sessions.values():
                ses.decided = True
                if not ses.closing:
                    ses.closing = True
                    self._put(("close", ses), force=True)
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=10)

    # ---------------------------------------------------------------- internal
    def _finish(self, ses: _Session) -> None:
        """Goi khi da co ca ket qua ghi va quyet dinh giu/xoa (dang giu self._lock)."""
        self._sessions.pop(ses.sid, None)
        file, n = ses.result
        ok = ses.keep and file is not None and n > 0
        if not ok and file is not None:
            file.unlink(missing_ok=True)
        if ses.on_done:
            rel = file.relative_to(self.root).as_posix() if ok else None
            threading.Thread(target=ses.on_done, args=(rel,), daemon=True).start()

    def _put(self, item, force: bool = False) -> None:
        try:
            if force:
                self._q.put(item, timeout=5)
            else:
                self._q.put_nowait(item)
        except queue.Full:
            self._dropped += 1
            if self._dropped % 100 == 1:
                log.warning("Hang doi ghi clip day, bo %d frame (CPU khong kip ma hoa)", self._dropped)

    def _open_writer(self, base: Path, size: tuple[int, int]):
        base.parent.mkdir(parents=True, exist_ok=True)
        for ext, cc in ([self._fmt] if self._fmt else FORMATS):
            file = base.with_suffix("." + ext)
            w = cv2.VideoWriter(str(file), cv2.VideoWriter_fourcc(*cc), self.fps, size)
            if w.isOpened():
                if self._fmt is None:
                    self._fmt = (ext, cc)
                    log.info("Ghi clip dinh dang %s (%s)", ext, cc)
                return w, file
            w.release()
        return None, None

    def _writer(self) -> None:
        state: dict[int, list] = {}   # sid -> [writer, file, n_frames]
        last_cleanup = 0.0
        while not (self._stop.is_set() and self._q.empty()):
            if time.time() - last_cleanup > CLEANUP_EVERY:
                last_cleanup = time.time()
                self._cleanup()
            try:
                item = self._q.get(timeout=0.5)
            except queue.Empty:  # nguon video dung gui frame: van phai dong clip dung han
                with self._lock:
                    self._tick(time.time())
                continue
            try:
                self._handle(item, state)
            except Exception:  # noqa: BLE001
                log.exception("Loi ghi clip")

    def _handle(self, item: tuple, state: dict) -> None:
        op, ses = item[0], item[1]
        if op == "frame":
            frame = item[2]
            st = state.get(ses.sid)
            if st is None:
                h, w = frame.shape[:2]
                writer, file = self._open_writer(ses.base, (w, h))
                if writer is None:
                    log.error("Khong mo duoc bo ghi video cho clip %s", ses.base)
                st = state[ses.sid] = [writer, file, 0]
            if st[0] is not None:
                st[0].write(frame)
                st[2] += 1
        elif op == "close":
            writer, file, n = state.pop(ses.sid, [None, None, 0])
            if writer is not None:
                writer.release()
            with self._lock:
                ses.result = (file, n)
                if ses.decided:
                    self._finish(ses)

    def _cleanup(self) -> None:
        """Xoa clip qua han luu tru (du lieu sinh trac hoc - khong giu vo thoi han)."""
        if self.retention_days <= 0 or not self.dir.exists():
            return
        cutoff = time.time() - self.retention_days * 86400
        n = 0
        for f in self.dir.glob("*/*.*"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
                n += 1
        for d in self.dir.iterdir():
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        if n:
            log.info("Da xoa %d clip qua %g ngay", n, self.retention_days)
