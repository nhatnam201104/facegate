"""Tien trinh suy luan doc lap Perception Process: Co lap loi, Lich thuc theo ca (H34) & Cat ROI Dau-Vai 1:1 tren cung khung 3MP (H33, H35)."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from datetime import datetime, time as dtime
from pathlib import Path

import cv2
import numpy as np

from .config import load_config
from .context_detector import ContextScene, create_context_detector, evaluate_face_context
from .db import DB
from .decision import LLRConfig, TrackDecisionAccumulator
from .engines import Face, create_engine
from .gallery import Gallery
from .ipc import IPCClient, TrackedObject
from .liveness import create_liveness
from .pose3d import PoseEstimator3D
from .quality import assess
from .security import BiometricCipher
from .tracker import IoUTracker, iou

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


def compute_head_shoulder_rois_1to1(frame_shape: tuple[int, int], person_boxes: list[np.ndarray]) -> list[tuple[int, int, int, int]]:
    """Tu hop nguoi tren cung khung /stream1 (2304x1296), cat vung Dau-Vai ty le 1:1 goc (khong resize meo mat - H33, H35)."""
    h, w = frame_shape[:2]
    rois: list[tuple[int, int, int, int]] = []
    for pb in person_boxes:
        px1, py1, px2, py2 = (float(v) for v in pb[:4])
        pw, ph = max(16.0, px2 - px1), max(16.0, py2 - py1)
        cx = (px1 + px2) * 0.5
        head_h = min(ph * 0.62, max(pw * 1.15, 256.0))
        side = max(320.0, min(float(min(w, h)), max(pw * 1.25, head_h)))
        x1 = int(max(0, round(cx - side * 0.5)))
        y1 = int(max(0, round(py1 - 0.12 * ph)))
        x2 = int(min(w, round(x1 + side)))
        y2 = int(min(h, round(y1 + side)))
        if x2 - x1 >= 64 and y2 - y1 >= 64:
            rois.append((x1, y1, x2, y2))
    return rois


def detect_faces_two_stage_3mp(
    frame: np.ndarray,
    engine,
    context_detector=None,
    fallback_det_width: int = 1280,
    max_faces: int = 10,
) -> tuple[list[Face], ContextScene | None]:
    """Giai phap H33 + H35:
    1. Resize chinh frame /stream1 ve <=640px chay YOLOX-Nano (<1ms resize, Δt = 0ms giua person box va frame 3MP).
    2. Cat ROI Dau-Vai 1:1 tren cung frame 3MP goc dua vao YuNet -> giu 100% mat do pixel o cu ly 2.0-3.2m.
    3. Fallback quet o fallback_det_width (1280px) khi dung qua sat camera khong bat duoc than nguoi.
    """
    h, w = frame.shape[:2]
    scene: ContextScene | None = None
    if context_detector is not None:
        s_ctx = min(1.0, 640.0 / max(w, 1))
        small_ctx = cv2.resize(frame, (int(w * s_ctx), int(h * s_ctx))) if s_ctx < 1.0 else frame
        scene = context_detector.detect(small_ctx)
        if s_ctx < 1.0:
            scene.persons = [(pb / s_ctx).astype(np.float32) for pb in scene.persons]
            scene.mediums = [((mb / s_ctx).astype(np.float32), lbl, sc) for mb, lbl, sc in scene.mediums]

    rois = compute_head_shoulder_rois_1to1((h, w), scene.persons if scene is not None else [])
    collected: list[Face] = []
    for rx1, ry1, rx2, ry2 in rois:
        crop = frame[ry1:ry2, rx1:rx2]
        for f in engine.detect(crop):
            gb = f.bbox + np.array([rx1, ry1, rx1, ry1], dtype=np.float32)
            gk = f.kps + np.array([rx1, ry1], dtype=np.float32)
            if not any(iou(gb, prev.bbox) > 0.55 for prev in collected):
                collected.append(Face(gb, f.score, gk))

    if not collected:
        sf = min(1.0, float(fallback_det_width) / max(w, 1))
        fb_img = cv2.resize(frame, (int(w * sf), int(h * sf))) if sf < 1.0 else frame
        for f in engine.detect(fb_img):
            collected.append(Face(f.bbox / sf, f.score, f.kps / sf) if sf < 1.0 else f)

    collected.sort(key=lambda f: f.w * f.h, reverse=True)
    return collected[:max_faces], scene


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
        self.pose = PoseEstimator3D(cfg["_paths"]["models"])
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
        self.camera_id = str(cfg["camera"].get("id", "gate-1"))
        self.seq = 0
        self._stop = False

    def process_frame(self, frame: np.ndarray, ts_ns: int | None = None, decode_ms: float = 0.0) -> tuple[dict, list[dict]]:
        t0 = time.perf_counter()
        ts_ns = ts_ns or time.time_ns()
        now_sec = ts_ns / 1e9
        h, w = frame.shape[:2]
        pcfg = self.cfg["pipeline"]
        max_faces = int(pcfg["max_faces"])

        if pcfg.get("two_stage_roi", True):
            faces, scene = detect_faces_two_stage_3mp(
                frame, self.engine, self.ctx_det,
                fallback_det_width=int(pcfg.get("fallback_det_width", 1280)),
                max_faces=max_faces,
            )
        else:
            faces = self.engine.detect(frame)[:max_faces]
            scene = self.ctx_det.detect(frame) if self.ctx_det is not None and faces else None

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
            pose = self.pose.estimate(frame, f, prev_group=t.pose_group if t.hits > 1 else None)
            q = assess(frame, f, self.cfg["quality"], pose=pose)
            t.pose_group, t.last_yaw, t.last_reason = pose.pose_group, pose.yaw, q.reason
            t.min_abs_yaw = min(t.min_abs_yaw, abs(pose.yaw))
            track_poses.append(pose)
            track_quals.append(q)
            if q.ok and not t.crossing_active:
                batch_indices.append(idx)
                batch_aligned.append(self.engine.align(frame, f, pose=pose, pose_group=pose.pose_group))

        batch_embs = self.engine.embed_batch(batch_aligned) if batch_aligned else []
        emb_by_idx = dict(zip(batch_indices, batch_embs))

        events_out: list[dict] = []
        objects_out: list[dict] = []

        for idx, (t, f) in enumerate(matched):
            pose, q = track_poses[idx], track_quals[idx]
            acc: TrackDecisionAccumulator = t.decision_acc  # type: ignore[assignment]
            ctx_eval = evaluate_face_context(
                f.bbox, scene, frame_height=h,
                viewport_bottom_grace=self.viewport_grace,
            )
            if ctx_eval["spoof_reason"] in ("screen_detected", "unattached_face"):
                t.spoof = True

            if idx in emb_by_idx and not t.spoof:
                emb = emb_by_idx[idx]
                sims = self.gallery.all_employee_similarities(emb, pose_group=pose.pose_group)
                acc.observe(now_sec * 1000.0, pose.yaw, q.score, sims)
                if acc.locked_employee_id is not None:
                    person = self.gallery.get_person(acc.locked_employee_id)
                    if person:
                        t.employee_id = acc.locked_employee_id
                        t.code, t.name = person["code"], person["name"]
                        t.verification_tier = acc.verification_tier
                        t.score, t.margin = acc.top1_llr, acc.llr_margin
                        t.quarantine = False

            if self.liveness is not None and q.ok and abs(pose.yaw) <= 40.0 and not t.spoof:
                t.live.append(self.liveness.real_prob(frame, f.bbox))
                t.live = t.live[-8:]
                if len(t.live) >= int(self.cfg["liveness"].get("min_frames", 4)):
                    lm = float(np.mean(t.live))
                    if lm < float(self.cfg["liveness"].get("spoof_below", 0.35)):
                        t.spoof = True

            live_status = "spoof" if t.spoof else (
                "live" if (not self.liveness or (len(t.live) >= 2 and float(np.mean(t.live)) >= 0.70) or abs(pose.yaw) > 40.0 and ctx_eval["body_coupled"])
                else "checking"
            )
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
                    "yaw": round(pose.yaw, 1),
                })
            elif state_str == "REJECTED_SPOOF" and not t.event_done:
                t.event_done = True
                events_out.append({
                    "type": "track_event",
                    "event_kind": "spoof",
                    "camera_id": self.camera_id,
                    "ts_server_ns": ts_ns,
                    "track_id": t.id,
                    "spoof_reason": ctx_eval["spoof_reason"] or "texture_spoof",
                })

            role = "alert" if state_str == "REJECTED_SPOOF" else (
                "ok" if state_str in ("LIVE_CONFIRMED", "COMMITTED") else (
                    "low_quality" if not q.ok else "checking"
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
                meta={"yaw": round(pose.yaw, 1), "llr_top1": acc.top1_llr, "llr_margin": acc.llr_margin, "quarantine": t.quarantine},
            )
            objects_out.append(obj.to_dict())

        for exp in expired:
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
                "verification_tier": exp.verification_tier,
                "min_abs_yaw": round(exp.min_abs_yaw, 1) if exp.min_abs_yaw < 900 else 0.0,
            })

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

    cmd_task = asyncio.create_task(_listen_commands())
    src = cfg["camera"].get("main_source") or cfg["camera"]["source"]
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
        await ipc.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sock", default="")
    ap.add_argument("--port", type=int, default=18091)
    args = ap.parse_args()
    cfg = load_config()
    sock = Path(args.sock) if args.sock else (cfg["_paths"]["run"] / "perception.sock")
    asyncio.run(run_worker_loop(cfg, sock_path=sock, port=args.port))


if __name__ == "__main__":
    main()
