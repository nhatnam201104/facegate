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
from .pose3d import PoseEstimator3D
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
    def __init__(self, cfg, db, engine, gallery, camera, hrm, broadcast,
                 liveness=None, clips=None, pose_estimator=None, context_detector=None):
        self.cfg, self.db, self.engine, self.gallery = cfg, db, engine, gallery
        self.camera, self.hrm, self.broadcast = camera, hrm, broadcast
        self.liveness, self.clips = liveness, clips
        self.context_detector = context_detector
        rc = cfg["recognition"]
        thr_cfg = rc["thresholds"].get(engine.name, {"frontal": 0.42, "profile": 0.48})
        if isinstance(thr_cfg, dict):
            self.threshold = float(thr_cfg["frontal"])
            self.profile_threshold = float(thr_cfg.get("profile", round(self.threshold + 0.06, 2)))
        else:
            self.threshold = float(thr_cfg)
            self.profile_threshold = round(self.threshold + 0.06, 2)
        self.margin = float(rc["margin"])
        self.profile_margin = float(rc.get("profile_margin", max(self.margin + 0.02, 0.08)))
        self.min_good = int(rc["min_good_frames"])
        self.min_profile = int(rc.get("min_profile_frames", 4))
        self.max_embs = int(rc["max_track_embeddings"])
        self.split_cos = float(rc.get("split_cosine", 0.25))
        self.frontal_max_yaw = float(rc.get("frontal_max_yaw", 40.0))
        self.pose_hysteresis = float(rc.get("pose_hysteresis", 5.0))
        self.pose_estimator = pose_estimator or PoseEstimator3D(
            cfg["_paths"]["models"], self.frontal_max_yaw, self.pose_hysteresis
        )
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
        ctx_scene = None
        person_boxes = None
        if self.context_detector is not None and (faces or self.tracker.tracks):
            ctx_scene = self.context_detector.detect(frame)
            person_boxes = ctx_scene.persons
        matched, expired = self.tracker.update(faces, now, person_boxes=person_boxes)
        active_faces = [f for _, f in matched]
        for t, f in matched:
            if t.id not in self.tracker.tracks:   # da bi gop vao track khac trong vong nay
                continue
            self._start_clip(t)
            self._update_track(t, f, frame, now, ctx_scene=ctx_scene, all_faces=active_faces)
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

    def _update_track(self, t: Track, f: Face, frame: np.ndarray, now: float,
                      ctx_scene=None, all_faces: list[Face] | None = None) -> None:
        with self.engine_lock:
            pose = self.pose_estimator.estimate(frame, f, prev_group=t.pose_group if t.hits > 1 else None)
        q = assess(frame, f, self.qcfg, pose=pose)
        t.last_reason = q.reason
        t.pose_group = pose.pose_group
        t.last_yaw = pose.yaw
        t.pose_history.append((now, pose.yaw, pose.pitch, pose.roll, t.center, pose.landmarks3d))

        if q.ok:
            with self.engine_lock:
                aligned = self.engine.align(frame, f, pose=pose, pose_group=pose.pose_group)
                emb = self.engine.embed_aligned(aligned)
            if t.should_split(emb, pose.pose_group, self.split_cos):
                log.info("Track %d: khuon mat moi khong khop trong ngan %s -> tach track", t.id, pose.pose_group)
                self._on_track_end(t)
                t = self.tracker.split(t, f, now)
                t.pose_history.append((now, pose.yaw, pose.pitch, pose.roll, t.center, pose.landmarks3d))
                self._start_clip(t)
            t.add_embedding(emb, q.score, pose.pose_group, yaw=pose.yaw, max_embs=self.max_embs)
            if q.score > t.best_quality or (pose.pose_group == "frontal" and t.matched_group != "frontal"):
                t.best_quality = q.score
                t.best_crop = crop_with_margin(frame, f.bbox)
            if self.liveness is not None and not t.spoof:
                self._update_liveness_sample(t, f, frame, pose, ctx_scene, all_faces)
            if t.employee_id is None:
                cand_grp, cand_vec = t.select_best_bucket(self.min_good, self.min_profile)
                if cand_grp is not None and cand_vec is not None:
                    t = self._identify(t, now, cand_grp, cand_vec)
            else:
                cur_grp = pose.pose_group
                min_req = self.min_good if cur_grp == "frontal" else self.min_profile
                if len(t.buckets.get(cur_grp, [])) >= min_req:
                    cur_vec = t.agg_embedding(cur_grp)
                    t = self._reverify_current_bucket(t, f, now, cur_grp, cur_vec, pose)
        elif t.best_crop is None:
            t.best_crop = crop_with_margin(frame, f.bbox)
            if self.liveness is not None and not t.spoof and ctx_scene is not None:
                self._update_liveness_sample(t, f, frame, pose, ctx_scene, all_faces, quality_ok=False)

        state = self._live_state(t, frame.shape)
        t.live_state = state
        if state == "spoof":
            t.spoof = True
        if t.spoof and not t.spoof_emitted:
            t.spoof_emitted = True
            t.verification_tier = "rejected_spoof"
            log.warning("Track %d: nghi gia mao (ly do=%s, live=%s, giong %s) -> khong cham cong",
                        t.id, t.spoof_reason or "spoof", t.live_mean, t.claimed[1] if t.claimed else "?")
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

    def _update_liveness_sample(self, t: Track, f: Face, frame: np.ndarray, pose,
                                ctx_scene=None, all_faces: list[Face] | None = None,
                                quality_ok: bool = True) -> None:
        if hasattr(self.liveness, "observe"):
            self.liveness.observe(t, f, frame, pose, ctx_scene=ctx_scene,
                                  all_faces=all_faces, quality_ok=quality_ok)
        elif quality_ok and abs(pose.yaw) <= self.frontal_max_yaw:
            t.live.append(self.liveness.real_prob(frame, f.bbox))
            t.live = t.live[-self.live_win:]

    def _identify(self, t: Track, now: float, pose_group: str, vec: np.ndarray) -> Track:
        """Chot danh tinh uu tien Best-shot ('frontal' truoc, 'profile' sau), tu dong nang hang neu quay thang."""
        m = self.gallery.match(
            vec,
            self.threshold,
            self.margin,
            pose_group=pose_group,
            profile_threshold=self.profile_threshold,
            profile_margin=self.profile_margin,
        )
        t.best_score = max(t.best_score, m.score)
        eff_thr = self.threshold if pose_group == "frontal" else self.profile_threshold
        if m.employee_id is not None and m.score >= eff_thr:
            t.claimed = (m.employee_id, m.code, m.name)

        if t.employee_id is None:
            if m.accepted:
                t.employee_id, t.code, t.name = m.employee_id, m.code, m.name
                t.matched_group = pose_group
                t.verification_tier = "verified" if pose_group == "frontal" else "profile_verified"
                t.score, t.margin, t.confirmed_at = m.score, m.margin, now
                log.info("Nhan dien %s (%s) [%s] score=%.3f margin=%.3f",
                         m.name, m.code, pose_group, m.score, m.margin)
                return self._merge_same_person(t)
        elif m.employee_id != t.employee_id:
            if m.accepted:
                log.info("Track %d: doi danh tinh tu %s sang %s (%s score=%.3f) -> xac minh lai",
                         t.id, t.code, m.code, pose_group, m.score)
                t.reset_identity()
                t.event_done = False
            elif pose_group == t.matched_group and m.score < eff_thr - VERIFY_SLACK:
                log.info("Track %d: khong con khop %s trong ngan %s (score=%.3f) -> xac minh lai",
                         t.id, t.code, pose_group, m.score)
                t.reset_identity()
                t.event_done = False
        else:
            # Cùng nhân viên: nâng hạng từ profile_verified lên verified nếu người đó vừa quay về frontal
            if pose_group == "frontal" and m.accepted:
                t.matched_group = "frontal"
                t.verification_tier = "verified"
                t.score, t.margin = m.score, m.margin
            elif pose_group == t.matched_group:
                if m.score < eff_thr - VERIFY_SLACK:
                    t.reset_identity()
                    t.event_done = False
                else:
                    t.score, t.margin = m.score, m.margin
        return t

    def _reverify_current_bucket(self, t: Track, f: Face, now: float, pose_group: str,
                                 vec: np.ndarray | None, pose) -> Track:
        """Hậu kiểm danh tính trên chính ngăn góc nhìn hiện tại để phát hiện ghép nhầm người khác."""
        if vec is None or t.employee_id is None:
            return t
        emp_sim = self.gallery.employee_similarity(t.employee_id, vec, pose_group)
        if emp_sim is None:
            log.debug("Track %d: %s chua co mau gallery cho ngan %s -> giu nguyen danh tinh", t.id, t.code, pose_group)
            return t
        eff_thr = self.threshold if pose_group == "frontal" else self.profile_threshold
        if emp_sim < eff_thr - VERIFY_SLACK:
            log.info(
                "Track %d: ngan %s khong khop %s (sim=%.3f < %.3f) -> tach track xac minh lai",
                t.id, pose_group, t.code, emp_sim, eff_thr - VERIFY_SLACK,
            )
            saved_bucket = list(t.buckets.get(pose_group, []))
            saved_yaws = list(t.bucket_yaws.get(pose_group, []))
            t.buckets[pose_group] = []
            t.bucket_yaws[pose_group] = []
            self._on_track_end(t)
            new_t = self.tracker.split(t, f, now)
            new_t.buckets[pose_group] = saved_bucket
            new_t.bucket_yaws[pose_group] = saved_yaws
            new_t.good_frames = len(saved_bucket)
            new_t.pose_group = pose_group
            new_t.last_yaw = pose.yaw
            new_t.min_abs_yaw = min((abs(y) for y in saved_yaws), default=abs(pose.yaw))
            new_t.pose_history.append((now, pose.yaw, pose.pitch, pose.roll, new_t.center, pose.landmarks3d))
            self._start_clip(new_t)
            return self._identify(new_t, now, pose_group, vec)
        return self._identify(t, now, pose_group, vec)

    def _merge_same_person(self, t: Track) -> Track:
        for o in self.tracker.tracks.values():
            if (o is not t and o.employee_id == t.employee_id and o.last_seen < t.last_seen
                    and not o.spoof and not t.spoof):
                log.info("Track %d: cung %s voi track %d vua mat -> gop", t.id, t.code, o.id)
                if self.clips is not None:
                    self.clips.stop(t.clip_sid, keep=False)
                self.tracker.absorb(o, t, max_embs=self.max_embs)
                o.live = o.live[-self.live_win:]
                if t.verification_tier == "verified":
                    o.matched_group = "frontal"
                    o.verification_tier = "verified"
                return o
        return t

    def _live_state(self, t: Track, frame_shape=None) -> str:
        """off -> coi nhu nguoi that | checking | live | spoof."""
        if self.liveness is None:
            if t.employee_id is not None and t.verification_tier is None:
                t.verification_tier = "verified" if t.matched_group == "frontal" else "profile_verified"
            t.liveness_mode = "disabled"
            return "live"
        if hasattr(self.liveness, "evaluate_track"):
            return self.liveness.evaluate_track(t, frame_shape=frame_shape)
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
        import json as _json

        grp = t.matched_group if kind == "employee" else t.pose_group
        min_yaw = round(t.min_abs_yaw, 1) if t.min_abs_yaw < 900 else round(abs(t.last_yaw), 1)
        tier = (
            "rejected_spoof" if kind == "spoof"
            else (t.verification_tier or ("verified" if grp == "frontal" else "profile_verified")) if kind == "employee"
            else "unverified"
        )
        app_id = self.db.add_appearance(
            kind=kind, employee_id=who[0], code=who[1], name=who[2],
            start_ts=t.first_seen, end_ts=t.last_seen,
            score=round(t.score if kind == "employee" else max(t.best_score, 0.0), 4),
            liveness=round(t.live_mean, 4) if t.live_mean is not None else None,
            snapshot=t.snapshot or self._save_snapshot(t), event_id=t.event_id,
            camera=self.cfg["camera"]["name"],
            pose_group=grp, min_abs_yaw=min_yaw,
            verification_tier=tier, spoof_reason=t.spoof_reason)
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
        import json as _json

        grp = t.matched_group if kind == "checkin" else t.pose_group
        mean_yaw = round(t.group_mean_yaw(grp), 1)
        if kind == "checkin":
            tier = t.verification_tier or ("verified" if grp == "frontal" else "profile_verified")
        elif kind == "spoof":
            tier = "rejected_spoof"
        else:
            tier = "unverified"
        detail_str = _json.dumps(t.liveness_detail, ensure_ascii=False) if t.liveness_detail else None
        latency = ((t.confirmed_at or now) - t.first_seen) * 1000
        ev = dict(ts=now, camera=self.cfg["camera"]["name"], employee_id=eid, code=code, name=name,
                  score=round(score, 4), margin=round(t.margin, 4), direction=direction,
                  latency_ms=round(latency, 1), frames=t.good_frames,
                  snapshot=t.snapshot or self._save_snapshot(t), model_version=self.engine.model_version,
                  hrm_status="pending" if kind == "checkin" else "skipped", kind=kind, att_type=att,
                  liveness=round(t.live_mean, 4) if t.live_mean is not None else None,
                  pose_group=grp, yaw=mean_yaw, verification_tier=tier,
                  liveness_mode=t.liveness_mode, liveness_detail=detail_str)
        ev["id"] = self.db.add_event(**ev)
        if t.event_id is None or kind == "checkin":
            t.event_id = ev["id"]
        ev_out = {**ev, "liveness_detail": t.liveness_detail or None}
        msg = {"type": "event", "event": ev_out}
        if kind == "checkin":
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
            state = "live" if self.liveness is None else t.live_state
            if t.spoof:
                r_vi = _SPOOF_VI.get(t.spoof_reason or "", "")
                suffix = f" ({r_vi})" if r_vi else ""
                color, label = RED, f"GIẢ MẠO{suffix} - không chấm công{ltxt}"
            elif t.employee_id is not None and state == "live":
                ptag = " · nghiêng" if t.verification_tier == "profile_verified" else ""
                color, label = GREEN, f"{t.name} · {t.score:.2f}{ptag}{ltxt}"
            elif t.employee_id is not None:
                wait_msg = "kiểm tra chuyển động 3D..." if t.matched_group != "frontal" else "kiểm tra người thật..."
                color, label = ORANGE, f"{t.name} · {wait_msg}{ltxt}"
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
               f"{' | chống giả mạo 4 lớp' if self.liveness is not None else ''}")
        view = draw_overlay(view, boxes, hud, line)
        if self.clips is not None:
            self.clips.push(view, now)
        self._publish(view)

    def status(self) -> dict:
        cam = self._next_camera or self.camera
        modes = getattr(self.liveness, "active_modes", ["texture_fas"]) if self.liveness is not None else []
        return {
            "backend": self.engine.name,
            "model_version": self.engine.model_version,
            "max_supported_yaw": int(self.qcfg.get("max_yaw", 90)),
            "source": cam.describe(),
            "camera_connected": cam.connected,
            "camera_fps": round(cam.fps_in, 1),
            "detect_fps": round(self.det_fps, 1),
            "detect_ms": round(self.det_ms, 1),
            "gallery": self.gallery.size,
            "gallery_profile_ready": getattr(self.gallery, "profile_ready_count", 0),
            "tracks": len(self.tracker.tracks),
            "threshold": self.threshold,
            "profile_threshold": self.profile_threshold,
            "margin": self.margin,
            "profile_margin": self.profile_margin,
            "liveness": self.liveness is not None,
            "live_threshold": self.live_thr,
            "liveness_modes": modes,
            "sleeping": self.sleeping,
            "sleep_since": self.sleep_since,
            "idle_seconds": self.idle_secs if self.sleep_enabled else None,
            "motion_zone": self.motion_zone,
            "wakes_rejected": self.wakes_rejected,
            "clips": bool(self.clips is not None and self.clips.enabled),
            "direction": self.ecfg.get("direction"),
            "hrm": self.hrm.kind,
        }


_SPOOF_VI = {
    "screen_detected": "màn hình/khung ảnh",
    "multi_face_single_body": "2 mặt trên 1 người",
    "unattached_face": "không gắn cơ thể",
    "planar_no_parallax": "ảnh phẳng không 3D",
    "moire_pattern": "vân màn hình",
    "texture_spoof": "ảnh/màn hình",
}


_REASON = {
    "small": "mặt nhỏ",
    "blur": "mờ",
    "light": "ánh sáng",
    "yaw": "quay quá góc",
    "pitch": "ngẩng/cúi quá mức",
    "roll": "nghiêng đầu",
    "occluded": "bị che khuất",
    "cut": "bị cắt",
}
