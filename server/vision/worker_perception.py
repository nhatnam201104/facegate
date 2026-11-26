"""Tien trinh suy luan doc lap Perception Process: Co lap loi, Lich thuc theo ca (H34) & Cat ROI Dau-Vai 1:1 tren cung khung 3MP (H33, H35)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime
from pathlib import Path

import cv2
import numpy as np

from .audio_prompt import create_look_prompter
from ..config import load_config
from .context_detector import (
    ContextScene,
    StaticUnattachedSuppressor,
    apply_context_spoof_decision,
    create_context_detector,
    evaluate_face_context,
    is_unattached_face_eligible,
)
from ..store.db import DB
from .decision import LLRConfig, TrackDecisionAccumulator
from .engines import Face, create_engine
from ..enrollment.gallery import Gallery
from ..api.ipc import IPCClient, TrackedObject
from .liveness import create_liveness
from .pose3d import create_pose_estimator
from .quality import assess
from ..api.security import BiometricCipher
from .tracker import IoUTracker, _face_matches_person, iou

log = logging.getLogger("worker_perception")


def _parse_hm(s: str) -> dtime:
    h, m = str(s).strip().split(":")
    return dtime(int(h), int(m))


def is_in_active_shift_window(active_windows: list[list[str]] | None, now_dt: datetime | None = None) -> bool:
    """Lich thuc theo ca (H34): True khi gio hien tai nam trong khung cao diem vao/tan ca -> giu nong decode /stream1."""
    if not active_windows:
        return False
    cur = (now_dt or datetime.now()).time()
    for win in active_windows:
        if len(win) != 2:
            continue
        t0, t1 = _parse_hm(win[0]), _parse_hm(win[1])
        if t0 <= t1:
            if t0 <= cur <= t1:
                return True
        elif cur >= t0 or cur <= t1:
            return True
    return False


@dataclass
class _Candidate:
    face: Face
    origin: str          # "roi" | "fallback"
    clipped: bool
    roi_index: int | None = None


def _roi_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return float(inter) / float(ua) if ua > 0 else 0.0


def _square_in_frame(cx: float, cy: float, side: int, w: int, h: int) -> tuple[int, int, int, int]:
    if w < side or h < side:
        return (0, 0, int(w), int(h))
    x1 = int(max(0, round(cx - side * 0.5)))
    y1 = int(max(0, round(cy - side * 0.5)))
    if x1 + side > w:
        x1 = max(0, w - side)
    if y1 + side > h:
        y1 = max(0, h - side)
    return (x1, y1, x1 + side, y1 + side)


def compute_head_shoulder_rois_1to1(
    frame_shape: tuple[int, int],
    person_boxes: list[np.ndarray],
    *,
    max_side: int | None = None,
    merge_iou: float | None = None,
) -> list[tuple[int, int, int, int]]:
    """Tu hop nguoi tren khung goc, cat vung Dau-Vai vuong co dich bien va gop vung chong lap (R5, R6).

    Luu y: `max_side` la tran kich thuoc dau vao bo phat hien (R1) — anh ROI lon hon `max_side`
    duoc giu nguyen khung dau-vai tren toa do 3MP va thu nho dong nhat ty le o `detect_faces_two_stage_3mp`
    de khong chat mat dinh dau/cam cua nguoi dung gan.
    """
    h, w = int(frame_shape[0]), int(frame_shape[1])
    m_iou = 0.60 if merge_iou is None else float(merge_iou)
    rois: list[tuple[int, int, int, int]] = []
    for pb in person_boxes:
        px1, py1, px2, py2 = (float(v) for v in pb[:4])
        pw, ph = max(16.0, px2 - px1), max(16.0, py2 - py1)
        cx = (px1 + px2) * 0.5
        head_h = min(ph * 0.62, max(pw * 1.15, 256.0))
        side = max(320.0, min(float(min(w, h)), max(pw * 1.25, head_h)))
        side_i = int(round(side))
        if w < side_i or h < side_i:
            x1, y1, x2, y2 = 0, 0, w, h
        else:
            x1 = int(max(0, round(cx - side_i * 0.5)))
            y1 = int(max(0, round(py1 - 0.12 * ph)))
            if x1 + side_i > w:
                x1 = max(0, w - side_i)
            if y1 + side_i > h:
                y1 = max(0, h - side_i)
            x2 = x1 + side_i
            y2 = y1 + side_i
        if x2 - x1 >= 64 and y2 - y1 >= 64:
            rois.append((x1, y1, x2, y2))

    # Gop cac vung chong lap IoU > merge_iou bang hop (union) giu dang vuong trong khung (R6, FR-004)
    merged = True
    while merged and len(rois) > 1:
        merged = False
        for i in range(len(rois)):
            for j in range(i + 1, len(rois)):
                if _roi_iou(rois[i], rois[j]) > m_iou:
                    a, b = rois[i], rois[j]
                    ux1, uy1 = min(a[0], b[0]), min(a[1], b[1])
                    ux2, uy2 = max(a[2], b[2]), max(a[3], b[3])
                    u_side = min(min(w, h), max(ux2 - ux1, uy2 - uy1))
                    new_roi = _square_in_frame((ux1 + ux2) * 0.5, (uy1 + uy2) * 0.5, u_side, w, h)
                    rois[i] = new_roi
                    rois.pop(j)
                    merged = True
                    break
            if merged:
                break

    return rois


def _should_run_fallback(
    roi_faces: list[Face],
    scene: ContextScene | None,
    cycle_index: int,
    *,
    enabled: bool,
    every_n: int,
    run_fallback: bool | None = None,
) -> bool:
    """FallbackGate (data-model §5, FR-007): quyet dinh chay quet bu toan khung co dieu kien.

    Dieu kien (b) dung `tracker._face_matches_person` lam nguon su that duy nhat cho quan he
    mat-nguoi; KHONG dung `context_detector._anatomical_head_match` vi nguong khac nhau.
    """
    if run_fallback is not None:
        return bool(run_fallback)
    if not enabled:
        return False
    if not roi_faces:
        return True
    if scene is not None and scene.persons:
        unmatched = sum(
            1 for pb in scene.persons
            if not any(_face_matches_person(f.bbox, pb) for f in roi_faces)
        )
        if unmatched > 0:
            return True
    return (int(cycle_index) % max(1, int(every_n))) == 0


def _merge_candidates(
    candidates: list[_Candidate],
    iou_thr: float = 0.45,
    containment_thr: float = 0.75,
    min_clipped_face: float = 0.0,
) -> list[Face]:
    """Hop nhat toan cuc (R7, FR-006): uu tien (khong bi cat, score), khu trung lap IoU + bao long."""
    ordered = sorted(candidates, key=lambda c: (not c.clipped, float(c.face.score)), reverse=True)
    kept: list[_Candidate] = []
    for cand in ordered:
        if (
            min_clipped_face > 0.0
            and cand.origin == "roi"
            and cand.clipped
            and min(float(cand.face.w), float(cand.face.h)) < min_clipped_face
        ):
            continue
        cb = cand.face.bbox
        c_area = max(1e-6, float(cb[2] - cb[0]) * float(cb[3] - cb[1]))
        drop = False
        for prev in kept:
            pb = prev.face.bbox
            if iou(cb, pb) > iou_thr:
                drop = True
                break
            p_area = max(1e-6, float(pb[2] - pb[0]) * float(pb[3] - pb[1]))
            ix1, iy1 = max(float(cb[0]), float(pb[0])), max(float(cb[1]), float(pb[1]))
            ix2, iy2 = min(float(cb[2]), float(pb[2])), min(float(cb[3]), float(pb[3]))
            inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
            if c_area <= p_area and (inter / c_area) >= containment_thr:
                drop = True
                break
        if not drop:
            kept.append(cand)
    return [c.face for c in kept]


def detect_faces_two_stage_3mp(
    frame: np.ndarray,
    engine,
    context_detector=None,
    fallback_det_width: int = 1280,
    max_faces: int = 10,
    always_fallback_unmatched: bool = True,
    *,
    run_fallback: bool | None = None,
    cycle_index: int = 0,
    det_cfg: dict | None = None,
    min_clipped_face: float | None = None,
) -> tuple[list[Face], ContextScene | None]:
    """Phat hien khuon mat hai tang tren khung 3MP (feature 006):
    1. Chay ContextDetector truc tiep tren khung goc (1 lan thu nho duy nhat: 2304*(640/2304)*(416/640) == 2304*(416/2304)).
    2. Cat ROI Dau-Vai vuong co dich bien & gop vung chong lap; vung > max_roi_det_side duoc thu nho dong nhat ty le.
    3. Danh dau ung vien cham ria noi bo (`clipped`), chay quet bu co dieu kien (FallbackGate), roi hop nhat toan cuc.
    """
    h, w = frame.shape[:2]
    cfg_p = det_cfg or {}
    max_roi_side = int(cfg_p.get("max_roi_det_side", 640))
    roi_merge_iou = float(cfg_p.get("roi_merge_iou", 0.60))
    roi_edge_margin = int(cfg_p.get("roi_edge_margin", 2))
    every_n = int(cfg_p.get("fallback_every_n_cycles", 4))
    merge_iou = float(cfg_p.get("det_fallback_iou", 0.45))
    merge_containment = float(cfg_p.get("det_fallback_containment", 0.75))
    clipped_min_side = float(
        cfg_p.get("min_clipped_face", 60.0) if min_clipped_face is None else min_clipped_face
    )

    scene: ContextScene | None = None
    if context_detector is not None:
        # Hinh hoc tuong duong tuyet doi: 2304 * (640/2304) * (416/640) == 2304 * (416/2304) (R10, FR-013)
        scene = context_detector.detect(frame)

    rois = compute_head_shoulder_rois_1to1(
        (h, w),
        scene.persons if scene is not None else [],
        max_side=max_roi_side,
        merge_iou=roi_merge_iou,
    )
    candidates: list[_Candidate] = []
    for r_idx, (rx1, ry1, rx2, ry2) in enumerate(rois):
        crop = frame[ry1:ry2, rx1:rx2]
        ch, cw = crop.shape[:2]
        max_c = max(ch, cw)
        if max_c > max_roi_side:
            scale = float(max_roi_side) / float(max_c)
            det_crop = cv2.resize(crop, (max(1, int(round(cw * scale))), max(1, int(round(ch * scale)))))
        else:
            scale = 1.0
            det_crop = crop
        for f in engine.detect(det_crop):
            fb = f.bbox / scale if scale < 1.0 else f.bbox
            fk = f.kps / scale if scale < 1.0 else f.kps
            gb = (fb + np.array([rx1, ry1, rx1, ry1], dtype=np.float32)).astype(np.float32)
            gk = (fk + np.array([rx1, ry1], dtype=np.float32)).astype(np.float32)
            face_global = Face(gb, f.score, gk, landmarks_valid=getattr(f, "landmarks_valid", True))
            clipped = (
                (rx1 > 0 and float(gb[0]) <= rx1 + roi_edge_margin)
                or (ry1 > 0 and float(gb[1]) <= ry1 + roi_edge_margin)
                or (rx2 < w and float(gb[2]) >= rx2 - roi_edge_margin)
                or (ry2 < h and float(gb[3]) >= ry2 - roi_edge_margin)
            )
            candidates.append(_Candidate(face=face_global, origin="roi", clipped=clipped, roi_index=r_idx))

    # Chi tinh ung vien khong bi cat ria noi bo khi xet FallbackGate de manh cat khong gia mao hop mat da khop nguoi
    roi_faces = [c.face for c in candidates if not c.clipped]
    if _should_run_fallback(
        roi_faces, scene, cycle_index,
        enabled=always_fallback_unmatched, every_n=every_n, run_fallback=run_fallback,
    ):
        sf = min(1.0, float(fallback_det_width) / max(w, 1))
        fb_img = cv2.resize(frame, (int(w * sf), int(h * sf))) if sf < 1.0 else frame
        for f in engine.detect(fb_img):
            gf = (
                Face(f.bbox / sf, f.score, f.kps / sf, landmarks_valid=getattr(f, "landmarks_valid", True))
                if sf < 1.0 else f
            )
            candidates.append(_Candidate(face=gf, origin="fallback", clipped=False, roi_index=None))

    merged = _merge_candidates(
        candidates, iou_thr=merge_iou, containment_thr=merge_containment,
        min_clipped_face=clipped_min_side,
    )
    merged.sort(key=lambda f: f.w * f.h, reverse=True)
    return merged[:max_faces], scene


def open_capture_with_hwaccel(source: str | int) -> cv2.VideoCapture:
    if os.environ.get("FACEGATE_HWACCEL", "").lower() in ("vaapi", "qsv", "cuda"):
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp|hwaccel;auto")
    else:
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    cap = cv2.VideoCapture(source)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


class PerceptionWorker:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        # Phai dung cung khoa sinh trac voi tien trinh chinh, neu khong gallery doc ra ciphertext (CR4)
        self.db = DB(cfg["_paths"]["db"], database_url=cfg["storage"].get("database_url", ""),
                     cipher=BiometricCipher.from_env_or_cfg(cfg))
        self.engine = create_engine(cfg)
        self.gallery = Gallery(self.db, self.engine.model_version)
        self.pose = create_pose_estimator(cfg)
        self.ctx_det = create_context_detector(cfg)
        self.liveness = create_liveness(cfg)
        trk_cfg = cfg["tracking"]
        self.tracker = IoUTracker(
            max_age=float(cfg["recognition"]["track_max_age"]),
            crossing_face_iou=float(trk_cfg["crossing_face_iou"]),
            crossing_person_iou=float(trk_cfg["crossing_person_iou"]),
        )
        self.llr_cfg = LLRConfig.from_cfg(cfg)
        self.viewport_grace = float(trk_cfg["viewport_bottom_grace"])
        self.unattached_confirm_frames = int(trk_cfg.get("unattached_confirm_frames", 4))
        self.unattached_min_det_score = float(trk_cfg.get("unattached_min_det_score", 0.72))
        self.static_unattached_suppressor = StaticUnattachedSuppressor(
            cooldown_sec=float(trk_cfg.get("unattached_static_cooldown_sec", 300.0)),
            iou_thr=float(trk_cfg.get("unattached_static_iou", 0.65)),
        )
        self.split_cos = float(cfg["recognition"].get("split_cosine", 0.25))
        self.require_frontal_for_checkin = bool(cfg.get("liveness", {}).get("require_frontal_for_checkin", True))
        self.camera_id = str(cfg["camera"].get("id", "gate-1"))
        self.seq = 0
        self._cycle_index = 0
        self._chime_events: list[dict] = []
        # Loa Tapo nhac nhin vao camera / canh bao gia mao (dong thoi day goi IPC camera_chime len Web)
        self.look_prompter = create_look_prompter(cfg, on_event=self._chime_events.append)
        self._stop = False
        # Control plane day xuong qua IPC khi nguoi dung gat sang tab Hang hoa / Khu vuc xuong
        self.mode = str(cfg.get("pipeline", {}).get("mode", "attendance")).strip().lower()
        if self.mode not in ("attendance", "sku", "zone"):
            self.mode = "attendance"

    def process_frame(self, frame: np.ndarray, ts_ns: int | None = None, decode_ms: float = 0.0) -> tuple[dict, list[dict]]:
        t0 = time.perf_counter()
        ts_ns = ts_ns or time.time_ns()
        now_sec = ts_ns / 1e9
        h, w = frame.shape[:2]
        pcfg = self.cfg["pipeline"]
        max_faces = int(pcfg["max_faces"])

        cur_mode = getattr(self, "mode", "attendance")
        if cur_mode in ("sku", "zone"):
            # Che do Hang hoa / Khu vuc xuong: tat hoan toan FaceID nang o worker (Pipeline xu ly SKU / Zone)
            self.tracker.tracks.clear()
            self._chime_events.clear()
            self.seq += 1
            return {
                "type": "frame_overlay",
                "camera_id": self.camera_id,
                "seq": self.seq,
                "ts_server_ns": ts_ns,
                "infer_ms": round((time.perf_counter() - t0) * 1000.0, 2),
                "decode_ms": round(decode_ms, 2),
                "mode": cur_mode,
                "objects": [],
            }, []

        cyc = getattr(self, "_cycle_index", 0)
        self._cycle_index = cyc + 1
        if pcfg.get("two_stage_roi", True):
            faces, scene = detect_faces_two_stage_3mp(
                frame, self.engine, self.ctx_det,
                fallback_det_width=int(pcfg.get("fallback_det_width", 1280)),
                max_faces=max_faces,
                always_fallback_unmatched=bool(pcfg.get("always_fallback_unmatched", True)),
                cycle_index=cyc,
                det_cfg=pcfg,
                min_clipped_face=float(self.cfg.get("quality", {}).get("min_face", 60)),
            )
        else:
            det_w = int(pcfg.get("fallback_det_width", 1280))
            s = min(1.0, float(det_w) / max(w, 1))
            det_img = cv2.resize(frame, (int(w * s), int(h * s))) if s < 1.0 else frame
            raw_faces = self.engine.detect(det_img)
            if s < 1.0:
                faces = [
                    Face(f.bbox / s, f.score, f.kps / s, landmarks_valid=getattr(f, "landmarks_valid", True))
                    for f in raw_faces
                ]
            else:
                faces = raw_faces
            faces.sort(key=lambda f: f.w * f.h, reverse=True)
            faces = faces[:max_faces]
            scene = self.ctx_det.detect(frame) if self.ctx_det is not None and faces else None

        prev_track_meta = {
            tid: (tr.last_seen, tr.min_abs_yaw)
            for tid, tr in self.tracker.tracks.items()
        }
        matched, expired = self.tracker.update(
            faces, now_sec, person_boxes=scene.persons if scene is not None else None,
        )

        # Batch align + embed cho toi 10 khuon mat dong thoi (K4 - T029)
        batch_indices: list[int] = []
        batch_aligned: list[np.ndarray] = []
        track_poses: list = []
        track_quals: list = []

        for idx, (t, f) in enumerate(matched):
            if t.decision_acc is None:
                t.decision_acc = TrackDecisionAccumulator(cfg=self.llr_cfg)
            if not getattr(f, "landmarks_valid", True):
                t.last_reason = "landmarks_invalid"
                track_poses.append(None)
                track_quals.append(None)
                continue
            pose = self.pose.estimate(frame, f, prev_group=t.pose_group if t.hits > 1 else None, track_id=t.id, ts=now_sec)
            q = assess(frame, f, self.cfg["quality"], pose=pose)
            # last_yaw la yaw THO: dung cho min_abs_yaw/ledger; pose.yaw (EMA) chi de hien thi goi y
            t.pose_group, t.last_yaw, t.last_reason = pose.pose_group, pose.gate_yaw(), q.reason
            pose_ok = bool(getattr(pose, "pose_reliable", True))
            if q.ok and not pose_ok:
                # Feature 002: khung qua nghieng -> khong lam bang chung danh tinh (Hien phap II)
                t.last_reason = "pose_unreliable"
            t.min_abs_yaw = min(t.min_abs_yaw, abs(pose.gate_yaw()))
            track_poses.append(pose)
            track_quals.append(q)
            if q.ok and pose_ok and not t.crossing_active and not t.spoof:
                batch_indices.append(idx)
                batch_aligned.append(self.engine.align(frame, f, pose=pose, pose_group=pose.pose_group))

        batch_embs = self.engine.embed_batch(batch_aligned) if batch_aligned else []
        emb_by_idx = dict(zip(batch_indices, batch_embs))

        events_out: list[dict] = []
        objects_out: list[dict] = []
        min_face = float(self.cfg.get("quality", {}).get("min_face", 60))
        trk_cfg = self.cfg.get("tracking", {})
        unattached_min_score = float(
            getattr(self, "unattached_min_det_score", trk_cfg.get("unattached_min_det_score", 0.72))
        )
        suppressor = getattr(self, "static_unattached_suppressor", None)

        for idx, (t, f) in enumerate(matched):
            pose, q = track_poses[idx], track_quals[idx]
            acc: TrackDecisionAccumulator = t.decision_acc  # type: ignore[assignment]
            ctx_eval = evaluate_face_context(
                f.bbox, scene, frame_height=h,
                viewport_bottom_grace=self.viewport_grace,
            )
            static_suppressed = apply_context_spoof_decision(
                t, f, ctx_eval,
                frame_shape=(h, w), now=now_sec,
                min_face=min_face, min_det_score=unattached_min_score,
                confirm_frames=getattr(self, "unattached_confirm_frames", 4),
                quality=q,
                suppressor=suppressor,
            )

            if idx in emb_by_idx and not t.spoof and not static_suppressed:
                emb = emb_by_idx[idx]
                agg = t.agg_embedding()
                if len(t.embs) >= 2 and agg is not None and float(emb @ agg) < getattr(self, "split_cos", 0.25):
                    # Tracker noi nham sang nguoi khac (vd: _jump) -> dong track cu va tach track moi ngay
                    old_end_ts, old_min_yaw = prev_track_meta.get(t.id, (t.last_seen, t.min_abs_yaw))
                    events_out.append({
                        "type": "track_event",
                        "event_kind": "appearance_closed",
                        "camera_id": self.camera_id,
                        "track_id": t.id,
                        "start_ts": t.first_seen,
                        "end_ts": old_end_ts,
                        "employee_id": t.employee_id,
                        "code": t.code,
                        "name": t.name,
                        "spoof": t.spoof,
                        "spoof_reason": t.spoof_reason,
                        "verification_tier": t.verification_tier,
                        "min_abs_yaw": round(old_min_yaw, 1) if old_min_yaw < 900 else 0.0,
                        "good_frames": t.good_frames,
                    })
                    t = self.tracker.split(t, f, now_sec)
                    t.decision_acc = TrackDecisionAccumulator(cfg=self.llr_cfg)
                    t.pose_group, t.last_yaw, t.last_reason = pose.pose_group, pose.gate_yaw(), q.reason
                    t.min_abs_yaw = abs(pose.gate_yaw())
                    if (
                        ctx_eval["spoof_reason"] == "unattached_face"
                        and is_unattached_face_eligible(f, min_face=min_face, min_det_score=unattached_min_score, quality=q)
                    ):
                        t.unattached_streak = 1
                    acc = t.decision_acc
                t.embs.append((emb, max(q.score, 0.05)))
                t.embs = t.embs[-int(self.cfg["recognition"].get("max_track_embeddings", 10)):]
                t.good_frames += 1

                sims = self.gallery.all_employee_similarities(emb, pose_group=pose.pose_group)
                acc.observe(now_sec * 1000.0, pose.gate_yaw(), q.score, sims)
                if acc.locked_employee_id is not None:
                    person = self.gallery.get_person(acc.locked_employee_id)
                    if person:
                        t.employee_id = acc.locked_employee_id
                        t.code, t.name = person["code"], person["name"]
                        t.verification_tier = acc.verification_tier
                        t.score, t.margin = acc.top1_llr, acc.llr_margin
                        t.quarantine = False
                elif t.employee_id is not None and not t.event_done:
                    # Bi go lock truoc khi COMMITTED
                    t.employee_id = t.code = t.name = t.verification_tier = None
                    t.score = t.margin = 0.0

            liv_cfg = self.cfg.get("liveness", {})
            liv_min = int(liv_cfg.get("min_frames", 4))
            liv_thr = float(liv_cfg.get("threshold", 0.70))
            liv_spoof = float(liv_cfg.get("spoof_below", 0.35))
            liv_win = int(liv_cfg.get("window", 8))
            frontal_max = float(self.cfg.get("recognition", {}).get("frontal_max_yaw", 40.0))
            gate_yaw = pose.gate_yaw() if pose is not None else t.last_yaw
            q_ok = bool(q.ok) if q is not None else False

            if self.liveness is not None and q_ok and pose is not None and abs(gate_yaw) <= frontal_max and not t.spoof and not static_suppressed:
                t.live.append(self.liveness.real_prob(frame, f.bbox))
                t.live = t.live[-liv_win:]
                if len(t.live) >= liv_min and float(np.mean(t.live)) < liv_spoof:
                    t.spoof = True
                    t.spoof_reason = t.spoof_reason or "texture_spoof"

            if t.spoof:
                live_status = "spoof"
            elif self.liveness is None:
                live_status = "live"
            elif len(t.live) >= liv_min and float(np.mean(t.live)) >= liv_thr:
                live_status = "live"
            elif (not getattr(self, "require_frontal_for_checkin", True)) and pose is not None and abs(gate_yaw) > frontal_max and ctx_eval["body_coupled"]:
                live_status = "live"
            else:
                live_status = "checking"
            if getattr(self, "look_prompter", None) is not None and pose is not None:
                # Prompter/overlay dung yaw EMA de loi nhac khong rung
                self.look_prompter.update(t.id, now_sec, pose.yaw, live_status, spoof=t.spoof, event_done=t.event_done)
            state_str = acc.apply_liveness(live_status)

            if state_str == "LIVE_CONFIRMED" and not t.event_done and not t.quarantine and t.employee_id is not None:
                acc.mark_committed()
                t.event_done = True
                state_str = "COMMITTED"
                events_out.append({
                    "type": "track_event",
                    "event_kind": "checkin",
                    "camera_id": self.camera_id,
                    "ts_server_ns": ts_ns,
                    "track_id": t.id,
                    "employee_id": t.employee_id,
                    "code": t.code,
                    "name": t.name,
                    "score": t.score,
                    "margin": t.margin,
                    "verification_tier": t.verification_tier or "verified",
                    "yaw": round(gate_yaw, 1),
                })
            elif state_str == "REJECTED_SPOOF" and not t.event_done:
                t.event_done = True
                events_out.append({
                    "type": "track_event",
                    "event_kind": "spoof",
                    "camera_id": self.camera_id,
                    "ts_server_ns": ts_ns,
                    "track_id": t.id,
                    "spoof_reason": t.spoof_reason or ctx_eval["spoof_reason"] or "texture_spoof",
                })

            role = "alert" if state_str == "REJECTED_SPOOF" else (
                "ok" if state_str in ("LIVE_CONFIRMED", "COMMITTED") else (
                    "low_quality" if not q_ok else "checking"
                )
            )
            lbl = (
                "GIẢ MẠO" if state_str == "REJECTED_SPOOF" else
                f"{t.code} · {t.name}" if t.employee_id is not None and not t.quarantine else
                "Đang xác minh lại..." if t.quarantine else
                "Đang xác minh..."
            )
            obj = TrackedObject(
                camera_id=self.camera_id,
                ts_server_ns=ts_ns,
                track_id=t.id,
                kind="face",
                bbox_norm=(f.bbox[0] / w, f.bbox[1] / h, f.bbox[2] / w, f.bbox[3] / h),
                vel_norm=(t.vel[0] / max(w, 1), t.vel[1] / max(h, 1)),
                state=state_str,
                color_role=role,
                label=lbl,
                verification_tier=t.verification_tier,
                meta={"yaw": round(gate_yaw, 1), "llr_top1": acc.top1_llr, "llr_margin": acc.llr_margin, "quarantine": t.quarantine},
            )
            objects_out.append(obj.to_dict())

        for exp in expired:
            if getattr(self, "look_prompter", None) is not None:
                self.look_prompter.forget(exp.id)
            if hasattr(self.pose, "forget"):
                self.pose.forget(exp.id)
            if exp.decision_acc is not None:
                exp.decision_acc.finalize_on_expire()
            events_out.append({
                "type": "track_event",
                "event_kind": "appearance_closed",
                "camera_id": self.camera_id,
                "track_id": exp.id,
                "start_ts": exp.first_seen,
                "end_ts": exp.last_seen,
                "employee_id": exp.employee_id,
                "code": exp.code,
                "name": exp.name,
                "spoof": exp.spoof,
                "spoof_reason": exp.spoof_reason,
                "verification_tier": exp.verification_tier,
                "min_abs_yaw": round(exp.min_abs_yaw, 1) if exp.min_abs_yaw < 900 else 0.0,
                "good_frames": exp.good_frames,
            })

        while getattr(self, "_chime_events", None):
            ce = self._chime_events.pop(0)
            events_out.append({**ce, "camera_id": self.camera_id})

        self.seq += 1
        infer_ms = (time.perf_counter() - t0) * 1000.0
        overlay_pkt = {
            "type": "frame_overlay",
            "camera_id": self.camera_id,
            "seq": self.seq,
            "ts_server_ns": ts_ns,
            "infer_ms": round(infer_ms, 2),
            "decode_ms": round(decode_ms, 2),
            "mode": "active_shift" if is_in_active_shift_window(self.cfg["power"].get("active_windows")) else "on_demand",
            "objects": objects_out,
        }
        return overlay_pkt, events_out


DEFAULT_MAIN_RTSP = "rtsp://127.0.0.1:8554/gate1_main"


def resolve_worker_source(cfg: dict) -> str | int:
    """Chon nguon doc cho worker: chi dung main_source khi la MediaMTX mac dinh (8554) hoac khong phai file/RTSP rieng."""
    cam = cfg.get("camera", {})
    src = cam.get("source", 0)
    main_src = cam.get("main_source")
    if not main_src:
        return src
    s_low = str(src).strip().lower()
    is_net = s_low.startswith(("rtsp://", "rtsps://", "http://", "https://"))
    if not is_net:
        return src
    if main_src == DEFAULT_MAIN_RTSP and "127.0.0.1:8554" not in s_low and "localhost:8554" not in s_low:
        return src
    return main_src


async def run_worker_loop(cfg: dict, sock_path: Path, port: int = 18091) -> None:
    worker = PerceptionWorker(cfg)
    ipc = IPCClient(sock_path=sock_path, port=port)
    await ipc.connect()

    async def _listen_commands() -> None:
        while not worker._stop:
            cmd = await ipc.recv()
            if cmd is None:
                break
            if cmd.get("cmd") == "reload_gallery":
                worker.gallery.reload()
            elif cmd.get("cmd") == "set_mode":
                m = str(cmd.get("mode", "")).strip().lower()
                if m in ("attendance", "sku", "zone"):
                    worker.mode = m
                    worker.tracker.tracks.clear()

    cmd_task = asyncio.create_task(_listen_commands())
    src = resolve_worker_source(cfg)
    cap = open_capture_with_hwaccel(src)
    det_iv = 1.0 / max(0.5, float(cfg["pipeline"]["detect_fps"]))
    last_hb = 0.0
    try:
        while not worker._stop:
            t_dec = time.perf_counter()
            ok, frame = await asyncio.to_thread(cap.read)
            dec_ms = (time.perf_counter() - t_dec) * 1000.0
            now = time.time()
            if ok and frame is not None:
                pkt, evs = await asyncio.to_thread(worker.process_frame, frame, time.time_ns(), dec_ms)
                await ipc.send(pkt)
                for ev in evs:
                    await ipc.send(ev)
            if now - last_hb >= 2.0:
                last_hb = now
                await ipc.send({
                    "type": "heartbeat",
                    "camera_id": worker.camera_id,
                    "ts_server_ns": time.time_ns(),
                    "stream1_connected": bool(ok),
                    "stream2_connected": True,
                    "fps_detect": round(float(cfg["pipeline"]["detect_fps"]), 1) if ok else 0.0,
                    "ffmpeg_ingest_alive": bool(ok),
                    "sleeping": False,
                })
            await asyncio.sleep(max(0.01, det_iv - (time.time() - now)))
    finally:
        cmd_task.cancel()
        cap.release()
        if worker.look_prompter is not None:
            worker.look_prompter.close()
        await ipc.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sock", default="")
    ap.add_argument("--port", type=int, default=18091)
    ap.add_argument("--mode", default="")
    args = ap.parse_args()
    cfg = load_config()
    if args.mode in ("attendance", "sku", "zone"):
        cfg["pipeline"]["mode"] = args.mode
    sock = Path(args.sock) if args.sock else (cfg["_paths"]["run"] / "perception.sock")
    asyncio.run(run_worker_loop(cfg, sock_path=sock, port=args.port))


if __name__ == "__main__":
    main()
