"""Vong xu ly chinh: detect (3-5 FPS) -> track -> quality -> embed -> match -> liveness -> rule -> event.

Moi track (1 lan xuat hien) duoc ghi vao bang appearances kem clip video khi ket thuc.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime

import cv2
import numpy as np

from .attendance import Shift, day_range, greeting
from .draw import GRAY, GREEN, ORANGE, RED, draw_overlay
from .engines import Face
from .quality import assess
from .tracker import IoUTracker, Track

log = logging.getLogger("pipeline")

VERIFY_SLACK = 0.05   # danh tinh da chot bi huy khi cosine tut duoi (nguong - VERIFY_SLACK)
DRAW_FRESH = 0.5      # chi ve khung cua track vua thay trong N giay (track dang cho noi lai thi an)
MOTION_SIZE = (160, 90)      # anh nho de do chuyen dong (re, khong dung model)
MOTION_PIXEL_DIFF = 20       # do lech muc xam de tinh 1 pixel la "thay doi"
SLEEP_PREVIEW_EVERY = 2.0    # che do cho: cap nhat hinh tren man hinh moi N giay
FULL_ZONE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
ZONE_COLOR = (255, 200, 80)


def validate_zone(zone) -> list[list[float]]:
    """Da giac >= 3 dinh, toa do chuan hoa 0..1. Nem ValueError neu sai."""
    if not isinstance(zone, (list, tuple)) or not 3 <= len(zone) <= 32:
        raise ValueError("Vùng phải có từ 3 đến 32 điểm")
    out = []
    for pt in zone:
        if not isinstance(pt, (list, tuple)) or len(pt) != 2:
            raise ValueError("Mỗi điểm phải có dạng [x, y]")
        x, y = float(pt[0]), float(pt[1])
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            raise ValueError("Tọa độ phải nằm trong khoảng 0..1")
        out.append([round(x, 4), round(y, 4)])
    mask = _zone_mask(out)
    if mask.mean() < 0.01:
        raise ValueError("Vùng quá nhỏ (dưới 1% khung hình)")
    return out


def _zone_mask(zone) -> np.ndarray:
    w, h = MOTION_SIZE
    mask = np.zeros((h, w), np.uint8)
    pts = np.array([[x * (w - 1), y * (h - 1)] for x, y in zone], np.int32)
    cv2.fillPoly(mask, [pts], 1)
    return mask.astype(bool)


def crop_with_margin(img: np.ndarray, bbox: np.ndarray, m: float = 0.25) -> np.ndarray:
    h, w = img.shape[:2]
    x1, y1, x2, y2 = bbox
    bw, bh = x2 - x1, y2 - y1
    x1, y1 = int(max(0, x1 - m * bw)), int(max(0, y1 - m * bh))
    x2, y2 = int(min(w, x2 + m * bw)), int(min(h, y2 + m * bh))
    return img[y1:y2, x1:x2].copy()


class Pipeline:
    def __init__(self, cfg, db, engine, gallery, camera, hrm, broadcast, liveness=None, clips=None,
                 recognize: bool = True):
        self.cfg, self.db, self.engine, self.gallery = cfg, db, engine, gallery
        self.camera, self.hrm, self.broadcast = camera, hrm, broadcast
        self.liveness, self.clips = liveness, clips
        # recognize=False (che do isolated_worker): worker_perception nhan dien; o day chi render khung xem truoc,
        # khong tao event/ledger/clip de tranh 2 bo nhan dien chay song song (CR2).
        self.recognize = bool(recognize)
        rc = cfg["recognition"]
        thr_cfg = rc["thresholds"].get(engine.name, 0.42)
        self.threshold = float(thr_cfg["frontal"]) if isinstance(thr_cfg, dict) else float(thr_cfg)
        self.margin = float(rc["margin"])
        self.min_good = int(rc["min_good_frames"])
        self.max_embs = int(rc["max_track_embeddings"])
        self.split_cos = float(rc.get("split_cosine", 0.25))
        self.tracker = IoUTracker(max_age=float(rc["track_max_age"]))
        lc = cfg.get("liveness", {})
        self.live_thr = float(lc.get("threshold", 0.7))
        self.spoof_thr = float(lc.get("spoof_below", 0.35))
        self.live_min = int(lc.get("min_frames", 4))
        self.live_win = int(lc.get("window", 8))
        self.shift = Shift.from_cfg(cfg)
        self.qcfg = cfg["quality"]
        self.ecfg = cfg["events"]
        self.pcfg = cfg["pipeline"]
        self.det_width = 960
        self.jpeg: bytes | None = None
        self.jpeg_id = 0
        self.jpeg_cond = threading.Condition()
        self.det_ms = 0.0
        self.det_fps = 0.0
        self.engine_lock = threading.Lock()   # enrollment dung chung engine
        self._stop = threading.Event()
        self._next_camera = None              # doi nguon video, ap dung trong thread pipeline
        self._gallery_ver = gallery.version
        # che do cho: khong co nguoi/chuyen dong trong idle_seconds -> ngung nhan dien, chi do chuyen dong
        pw = cfg.get("power", {})
        self.sleep_enabled = bool(pw.get("sleep", True))
        self.idle_secs = float(pw.get("idle_seconds", 30))
        self.motion_thr = float(pw.get("motion_threshold", 0.01))
        self.sleep_iv = 1.0 / max(1.0, float(pw.get("sleep_check_fps", 4)))
        self.sleeping = False
        self.sleep_since: float | None = None
        self.last_activity = time.time()
        self.motion_level = 0.0
        self._motion_prev: np.ndarray | None = None
        # chuyen dong trong vung -> tim khuon mat trong confirm_seconds; khong thay thi ngu tiep
        self.confirm_secs = float(pw.get("confirm_seconds", 1.0))
        self._confirm_until: float | None = None
        self.wakes_rejected = 0       # so lan chuyen dong khong phai nguoi (la cay, bong...)
        self.set_motion_zone(pw.get("motion_zone") or FULL_ZONE)
        self._thread = threading.Thread(target=self._run, daemon=True, name="pipeline")

    # ------------------------------------------------------------------ loop
    def start(self) -> "Pipeline":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def set_camera(self, camera) -> None:
        self._next_camera = camera

    def _run(self) -> None:
        det_iv = 1.0 / max(0.5, float(self.pcfg["detect_fps"]))
        st_iv = 1.0 / max(1.0, float(self.pcfg["stream_fps"]))
        last_det = last_st = 0.0
        last_fid = -1
        n_det, t_cnt = 0, time.time()
        while not self._stop.is_set():
            if self._next_camera is not None:  # track cu khong con y nghia voi nguon moi
                self._end_all_tracks()
                self.camera, self._next_camera = self._next_camera, None
                self.tracker = IoUTracker(max_age=float(self.cfg["recognition"]["track_max_age"]))
                last_fid = -1
                if self.sleeping:
                    self._wake(time.time(), "doi nguon video")
            fid, frame = self.camera.read()
            if frame is None or fid == last_fid:
                time.sleep(0.005)
                continue
            last_fid = fid
            now = time.time()
            if not self.recognize:
                if now - last_st >= st_iv:
                    last_st = now
                    self._render(frame, now)
                continue
            if self.sleeping:
                if now - last_det < self.sleep_iv:
                    continue
                last_det = now
                if not self._wake_check(frame, now):
                    if self.clips is not None:
                        self.clips.push(frame, now)   # giu pre-roll cho clip khi co nguoi den
                    if now - last_st >= SLEEP_PREVIEW_EVERY:
                        last_st = now
                        self._render_sleep(frame)
                    if now - t_cnt >= 2.0:
                        self.det_fps, n_det, t_cnt = 0.0, 0, now
                    continue
                self._wake(now, f"thay khuon mat sau chuyen dong {self.motion_level:.1%}")
                last_det = 0.0
            if now - last_det >= det_iv:
                last_det = now
                try:
                    self._process(frame, now)
                except Exception:  # noqa: BLE001
                    log.exception("Loi xu ly frame")
                n_det += 1
                if self.tracker.tracks:   # chi khuon mat moi giu app thuc (la cay lay khong tinh)
                    self.last_activity = now
                elif self.sleep_enabled and now - self.last_activity > self.idle_secs:
                    self._sleep(now, frame)
                    continue
            if now - t_cnt >= 2.0:
                self.det_fps, n_det, t_cnt = n_det / (now - t_cnt), 0, now
            if now - last_st >= st_iv:
                last_st = now
                self._render(frame, now)
        self._end_all_tracks()

    # ------------------------------------------------------------- che do cho
    def set_motion_zone(self, zone) -> None:
        self.motion_zone = validate_zone(zone)
        self._zone = _zone_mask(self.motion_zone)
        self._motion_prev = None

    def _motion(self, frame: np.ndarray) -> bool:
        """So voi lan do truoc: ti le pixel thay doi TRONG VUNG >= motion_threshold."""
        small = cv2.cvtColor(cv2.resize(frame, MOTION_SIZE, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        small = cv2.GaussianBlur(small, (5, 5), 0)
        prev, self._motion_prev = self._motion_prev, small
        if prev is None:
            return False
        changed = cv2.absdiff(small, prev) > MOTION_PIXEL_DIFF
        self.motion_level = float(changed[self._zone].mean())
        return self.motion_level >= self.motion_thr

    def _wake_check(self, frame: np.ndarray, now: float) -> bool:
        """Che do cho: True khi co chuyen dong trong vung VA thay khuon mat (trong confirm_seconds)."""
        if self._motion(frame) and self._confirm_until is None:
            self._confirm_until = now + self.confirm_secs
        if self._confirm_until is None:
            return False
        if self._detect(frame):
            self._confirm_until = None
            return True
        if now > self._confirm_until:
            self._confirm_until = None
            self.wakes_rejected += 1
            log.debug("Chuyen dong %.1f%% nhung khong thay khuon mat -> ngu tiep", self.motion_level * 100)
        return False

    def _sleep(self, now: float, frame: np.ndarray) -> None:
        self._end_all_tracks()
        self.sleeping, self.sleep_since = True, now
        self._motion_prev, self._confirm_until = None, None
        self.camera.idle = True
        log.info("Khong co nguoi %.0fs -> che do cho", self.idle_secs)
        self._render_sleep(frame)
        self.broadcast({"type": "status", "status": self.status()})

    def _wake(self, now: float, reason: str) -> None:
        self.sleeping, self.sleep_since = False, None
        self.camera.idle = False
        self.last_activity = now
        log.info("Thoat che do cho (%s)", reason)
        self.broadcast({"type": "status", "status": self.status()})

    def _render_sleep(self, frame: np.ndarray) -> None:
        h, w = frame.shape[:2]
        s = min(1.0, float(self.pcfg["stream_width"]) / w)
        view = cv2.resize(frame, (int(w * s), int(h * s))) if s < 1 else frame.copy()
        view = cv2.convertScaleAbs(view, alpha=0.35)
        vh, vw = view.shape[:2]
        if self.motion_zone != FULL_ZONE:
            pts = np.array([[x * (vw - 1), y * (vh - 1)] for x, y in self.motion_zone], np.int32)
            cv2.polylines(view, [pts], True, ZONE_COLOR, 2, cv2.LINE_AA)
        view = draw_overlay(view, [], "CHẾ ĐỘ CHỜ · tự bật khi có người vào vùng cửa")
        self._publish(view)

    def _publish(self, view: np.ndarray) -> None:
        ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            with self.jpeg_cond:
                self.jpeg = buf.tobytes()
                self.jpeg_id += 1
                self.jpeg_cond.notify_all()

    def _end_all_tracks(self) -> None:
        for t in list(self.tracker.tracks.values()):
            try:
                self._on_track_end(t)
            except Exception:  # noqa: BLE001
                log.exception("Loi ket thuc track %d", t.id)
        self.tracker.tracks.clear()

    # --------------------------------------------------------------- process
    def _detect(self, frame: np.ndarray) -> list[Face]:
        h, w = frame.shape[:2]
        s = min(1.0, self.det_width / w)
        small = cv2.resize(frame, (int(w * s), int(h * s))) if s < 1 else frame
        with self.engine_lock:
            faces = self.engine.detect(small)
        if s < 1:
            for f in faces:
                f.bbox = f.bbox / s
                f.kps = f.kps / s
        faces.sort(key=lambda f: f.w * f.h, reverse=True)
        return faces[: int(self.pcfg["max_faces"])]

    def _process(self, frame: np.ndarray, now: float) -> None:
        t0 = time.time()
        self._recheck_gallery()
        faces = self._detect(frame)
        matched, expired = self.tracker.update(faces, now)
        for t, f in matched:
            if t.id not in self.tracker.tracks:   # da bi gop vao track khac trong vong nay
                continue
            self._start_clip(t)
            self._update_track(t, f, frame, now)
        for t in expired:
            self._on_track_end(t)
        self.det_ms = (time.time() - t0) * 1000

    def _recheck_gallery(self) -> None:
        """Nhan vien bi xoa/khoa trong luc dang dung truoc camera -> bo danh tinh cua track ngay."""
        if self.gallery.version == self._gallery_ver:
            return
        self._gallery_ver = self.gallery.version
        for t in self.tracker.tracks.values():
            if t.employee_id is not None and not self.gallery.has(t.employee_id):
                log.info("Track %d: %s khong con trong danh sach nhan dien -> bo danh tinh", t.id, t.code)
                t.reset_identity()
                t.event_done = False

    def _start_clip(self, t: Track) -> None:
        if t.clip_sid is None and self.clips is not None and self.clips.enabled:
            t.clip_sid = self.clips.start()

    def _update_track(self, t: Track, f: Face, frame: np.ndarray, now: float) -> None:
        q = assess(frame, f, self.qcfg)
        t.last_reason = q.reason
        if q.ok:
            with self.engine_lock:
                emb = self.engine.embed_aligned(self.engine.align(frame, f))
            if len(t.embs) >= 2 and float(emb @ t.agg_embedding()) < self.split_cos:
                # tracker noi nham nguoi khac vao track nay -> tach, khong tron embedding 2 nguoi
                log.info("Track %d: khuon mat moi khong khop (cos %.2f) -> tach track",
                         t.id, float(emb @ t.agg_embedding()))
                self._on_track_end(t)
                t = self.tracker.split(t, f, now)
                self._start_clip(t)
            t.embs.append((emb, max(q.score, 0.05)))
            t.embs = t.embs[-self.max_embs:]
            t.good_frames += 1
            if q.score > t.best_quality:
                t.best_quality = q.score
                t.best_crop = crop_with_margin(frame, f.bbox)
            if self.liveness is not None and not t.spoof:
                t.live.append(self.liveness.real_prob(frame, f.bbox))
                t.live = t.live[-self.live_win:]
            if t.good_frames >= self.min_good:
                t = self._identify(t, now)
        elif t.best_crop is None:
            t.best_crop = crop_with_margin(frame, f.bbox)

        state = self._live_state(t)
        if state == "spoof" and not t.spoof:
            t.spoof = True
            log.warning("Track %d: nghi gia mao (live %.2f, giong %s) -> khong cham cong",
                        t.id, t.live_mean, t.claimed[1] if t.claimed else "?")
            self._emit(t, None, kind="spoof")
        if t.spoof:
            return

        # --- rule ghi su kien: chi khi da xac dinh la nguoi that ---
        live = state == "live"
        mode = self.ecfg.get("direction", "none")
        if mode == "line":
            d = self._crossing(t, frame.shape)
            if d == "OUT":   # chi cham cong luc vao
                d = None
            if d:
                if t.employee_id is not None and live:
                    self._emit(t, d)
                else:
                    t.pending_direction = d
            if t.employee_id is not None and live and t.pending_direction and not t.event_done:
                self._emit(t, t.pending_direction)
        elif t.employee_id is not None and live and not t.event_done:
            self._emit(t, None)

    def _identify(self, t: Track, now: float) -> Track:
        """Chot danh tinh, va xac minh lai lien tuc sau khi da chot. Tra ve track de xu ly tiep
        (co the la track cu cua cung nguoi neu vua gop)."""
        m = self.gallery.match(t.agg_embedding(), self.threshold, self.margin)
        t.best_score = max(t.best_score, m.score)
        if m.employee_id is not None and m.score >= self.threshold:
            t.claimed = (m.employee_id, m.code, m.name)
        if t.employee_id is None:
            if m.accepted:
                t.employee_id, t.code, t.name = m.employee_id, m.code, m.name
                t.score, t.margin, t.confirmed_at = m.score, m.margin, now
                log.info("Nhan dien %s (%s) score=%.3f margin=%.3f", m.name, m.code, m.score, m.margin)
                return self._merge_same_person(t)
        elif m.employee_id != t.employee_id or m.score < self.threshold - VERIFY_SLACK:
            log.info("Track %d: khong con khop %s (top=%s score=%.3f) -> xac minh lai",
                     t.id, t.code, m.code, m.score)
            t.reset_identity()
            t.event_done = False
        else:
            t.score, t.margin = m.score, m.margin
        return t

    def _merge_same_person(self, t: Track) -> Track:
        """Track moi vua nhan ra nhan vien X trong khi track cu cua X dang cho noi lai (mat mat < max_age):
        gop vao track cu -> 1 lan xuat hien, 1 clip. Chi gop track cu KHONG thay trong vong nay
        (2 mat cung luc giong X, vd nguoi that + anh dien thoai, thi khong gop)."""
        for o in self.tracker.tracks.values():
            if (o is not t and o.employee_id == t.employee_id and o.last_seen < t.last_seen
                    and not o.spoof and not t.spoof):
                log.info("Track %d: cung %s voi track %d vua mat -> gop", t.id, t.code, o.id)
                if self.clips is not None:
                    self.clips.stop(t.clip_sid, keep=False)   # clip cua o van dang ghi ca doan nay
                self.tracker.absorb(o, t)
                o.embs, o.live = o.embs[-self.max_embs:], o.live[-self.live_win:]
                return o
        return t

    def _live_state(self, t: Track) -> str:
        """off -> coi nhu nguoi that | checking | live | spoof."""
        if self.liveness is None:
            return "live"
        if t.spoof:
            return "spoof"
        if len(t.live) < self.live_min:
            return "checking"
        m = t.live_mean
        if m >= self.live_thr:
            return "live"
        return "spoof" if m < self.spoof_thr else "checking"

    def _crossing(self, t: Track, shape) -> str | None:
        h, w = shape[:2]
        (ax, ay), (bx, by) = [(p[0] * w, p[1] * h) for p in self.ecfg["line"]]
        cx, cy = t.center
        side = 1 if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 0 else -1
        prev, t.side = t.side, side
        if prev is None or prev == side:
            return None
        # side tang (-1 -> 1) ~ di xuong voi line nam ngang trai->phai
        going_down = side > prev
        down_is_in = self.ecfg.get("in_when", "down") == "down"
        return "IN" if going_down == down_is_in else "OUT"

    def _on_track_end(self, t: Track) -> None:
        if t.spoof:
            kind = "spoof"
        elif t.employee_id is not None:
            kind = "employee"
        elif t.good_frames >= self.min_good:
            kind = "unknown"
        else:
            kind = None   # thoang qua / chat luong qua kem: khong luu lich su
        if kind == "unknown" and not t.event_done and self.ecfg.get("log_unknown", True):
            self._emit(t, t.pending_direction, kind="unknown")
        if kind is None:
            if self.clips is not None:
                self.clips.stop(t.clip_sid, keep=False)
            return

        if kind == "employee":
            who = (t.employee_id, t.code, t.name)
        elif kind == "spoof" and t.claimed:
            who = t.claimed
        else:
            who = (None, None, None)
        app_id = self.db.add_appearance(
            kind=kind, employee_id=who[0], code=who[1], name=who[2],
            start_ts=t.first_seen, end_ts=t.last_seen,
            score=round(t.score if kind == "employee" else max(t.best_score, 0.0), 4),
            liveness=round(t.live_mean, 4) if t.live_mean is not None else None,
            snapshot=t.snapshot or self._save_snapshot(t), event_id=t.event_id,
            camera=self.cfg["camera"]["name"])
        from .ledger import hold_clip_if_pending_audit
        hold_clip_if_pending_audit(self.db, app_id, t.event_id)
        if self.clips is not None and self.clips.enabled:
            self.clips.stop(t.clip_sid, keep=True, on_done=lambda rel, a=app_id: self._clip_done(a, rel))
        self.broadcast({"type": "appearance", "id": app_id})

    def _clip_done(self, app_id: int, rel: str | None) -> None:
        self.db.set_clip(app_id, rel)
        self.broadcast({"type": "appearance", "id": app_id})

    # ----------------------------------------------------------------- events
    def _save_snapshot(self, t: Track) -> str | None:
        if t.best_crop is None or not t.best_crop.size:
            return None
        day = datetime.now().strftime("%Y%m%d")
        folder = self.cfg["_paths"]["snapshots"] / day
        folder.mkdir(parents=True, exist_ok=True)
        tag = t.code or ("spoof" if t.spoof else "unknown")
        fname = f"{datetime.now():%H%M%S}_{t.id}_{tag}.jpg"
        cv2.imwrite(str(folder / fname), t.best_crop)
        t.snapshot = f"snapshots/{day}/{fname}"
        return t.snapshot

    def _emit(self, t: Track, direction: str | None, kind: str = "checkin") -> None:
        now = time.time()
        att = None
        if kind == "checkin":   # moi nhan vien chi check-in 1 lan/ngay
            t.event_done = True
            day0, _ = day_range(date.today())
            first = self.db.first_checkin_between(t.employee_id, day0, now)
            if first:  # da check-in hom nay: chi chao, khong ghi them
                self.broadcast({"type": "greet", "name": t.name, "code": t.code, "last_ts": first})
                return
            att = "in"
        elif kind == "unknown":
            t.event_done = True

        if kind == "checkin":
            eid, code, name, score = t.employee_id, t.code, t.name, t.score
        elif kind == "spoof":
            claimed = t.claimed or (None, None, None)
            eid, code, name, score = None, claimed[1], claimed[2] or "Không xác định", max(t.best_score, 0.0)
        else:
            eid, code, name, score = None, None, "Unknown", t.best_score
        latency = ((t.confirmed_at or now) - t.first_seen) * 1000
        ev = dict(ts=now, camera=self.cfg["camera"]["name"], employee_id=eid, code=code, name=name,
                  score=round(score, 4), margin=round(t.margin, 4), direction=direction,
                  latency_ms=round(latency, 1), frames=t.good_frames,
                  snapshot=t.snapshot or self._save_snapshot(t), model_version=self.engine.model_version,
                  # Ledger la nguon duy nhat day len HRM; trang thai duoc phan chieu ve event sau khi sync.
                  hrm_status="skipped", kind=kind, att_type=att,
                  liveness=round(t.live_mean, 4) if t.live_mean is not None else None)
        ev["id"] = self.db.add_event(**ev)
        if t.event_id is None or kind == "checkin":
            t.event_id = ev["id"]
        msg = {"type": "event", "event": ev}
        if kind == "checkin":
            from .ledger import record_auto_checkin
            record_auto_checkin(
                self.db,
                employee_id=int(eid),
                code=str(code),
                ts=now,
                verification_tier=getattr(t, "verification_tier", None) or "verified",
                score=float(score),
                tau_threshold=float(self.cfg.get("decision", {}).get("tau_threshold", 0.45)),
                camera_id=str(self.cfg["camera"].get("id", "gate-1")),
                event_id=ev["id"],
                audit_cfg=self.cfg.get("audit_and_safety"),
            )
            self.hrm.notify()
            msg["greet"] = greeting(now, self.shift)
        self.broadcast(msg)

    # ----------------------------------------------------------------- render
    def _render(self, frame: np.ndarray, now: float) -> None:
        h, w = frame.shape[:2]
        s = min(1.0, float(self.pcfg["stream_width"]) / w)
        view = cv2.resize(frame, (int(w * s), int(h * s))) if s < 1 else frame.copy()
        boxes = []
        for t in list(self.tracker.tracks.values()):
            if now - t.last_seen > DRAW_FRESH:   # dang cho noi lai: khong ve khung "ma" o vi tri cu
                continue
            lm = t.live_mean
            ltxt = f" · thật {lm:.2f}" if self.liveness is not None and lm is not None else ""
            state = self._live_state(t)
            if t.spoof:
                color, label = RED, f"GIẢ MẠO - không chấm công{ltxt}"
            elif t.employee_id is not None and state == "live":
                color, label = GREEN, f"{t.name} · {t.score:.2f}{ltxt}"
            elif t.employee_id is not None:
                color, label = ORANGE, f"{t.name} · kiểm tra người thật...{ltxt}"
            elif t.good_frames >= self.min_good:
                color, label = RED, f"Unknown{ltxt}"
            elif t.last_reason not in ("ok", ""):
                color, label = GRAY, f"Chất lượng thấp ({_REASON.get(t.last_reason, t.last_reason)})"
            else:
                color, label = ORANGE, "Đang xác minh..."
            boxes.append({"bbox": t.bbox * s, "color": color, "label": label})
        line = None
        if self.ecfg.get("direction") == "line":
            vh, vw = view.shape[:2]
            line = [(int(p[0] * vw), int(p[1] * vh)) for p in self.ecfg["line"]]
        hud = (f"{self.engine.name} | detect {self.det_fps:.1f} FPS {self.det_ms:.0f} ms | "
               f"cam {self.camera.fps_in:.0f} FPS | NV: {self.gallery.size}"
               f"{' | chống giả mạo' if self.liveness is not None else ''}")
        view = draw_overlay(view, boxes, hud, line)
        if self.clips is not None:
            self.clips.push(view, now)
        self._publish(view)

    def status(self) -> dict:
        cam = self._next_camera or self.camera
        return {"backend": self.engine.name, "model_version": self.engine.model_version,
                "source": cam.describe(),
                "camera_connected": cam.connected, "camera_fps": round(cam.fps_in, 1),
                "detect_fps": round(self.det_fps, 1), "detect_ms": round(self.det_ms, 1),
                "gallery": self.gallery.size, "tracks": len(self.tracker.tracks),
                "threshold": self.threshold, "margin": self.margin,
                "liveness": self.liveness is not None, "live_threshold": self.live_thr,
                "sleeping": self.sleeping, "sleep_since": self.sleep_since,
                "idle_seconds": self.idle_secs if self.sleep_enabled else None,
                "motion_zone": self.motion_zone, "wakes_rejected": self.wakes_rejected,
                "clips": bool(self.clips is not None and self.clips.enabled),
                "direction": self.ecfg.get("direction"), "hrm": self.hrm.kind}


_REASON = {"small": "mặt nhỏ", "blur": "mờ", "light": "ánh sáng", "yaw": "quay ngang", "cut": "bị cắt"}
