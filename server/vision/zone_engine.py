"""Polygon Zone & Rule Engine hien dien khu vuc xuong (GAP-04, Time-Based 100%).

Tuan thu docs/TAI_LIEU_TRIEN_KHAI_ERP.md:
- Chay nhat quan tren moi FPS camera (10fps..30fps) nho nguong thoi gian thuc (enter_confirm_sec,
  min_dwell_sec, exit_grace_sec, alert_empty_sec) thay cho dem frame cung.
- Chi luu du lieu vi tri / thoi gian hien dien khach quan (enter_ts, exit_ts, dwell_sec),
  tuyet doi khong suy dien nang suat / KPI ca nhan.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

log = logging.getLogger("zone_engine")


def _person_iou(a: np.ndarray, b: np.ndarray) -> float:
    ix1, iy1 = max(float(a[0]), float(b[0])), max(float(a[1]), float(b[1]))
    ix2, iy2 = min(float(a[2]), float(b[2])), min(float(a[3]), float(b[3]))
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    ua = (
        max(0.0, float(a[2] - a[0])) * max(0.0, float(a[3] - a[1]))
        + max(0.0, float(b[2] - b[0])) * max(0.0, float(b[3] - b[1]))
        - inter
    )
    return float(inter / ua) if ua > 0 else 0.0


def _anchor_point(box: np.ndarray, frame_h: int) -> tuple[float, float]:
    """Diem neo kiem tra nguoi dung trong vung: ha trong tam (85% chieu cao);
    neu hop bi cat day khung hinh (Viewport-Aware) thi dung tam hop."""
    x1, y1, x2, y2 = (float(v) for v in box[:4])
    cx = (x1 + x2) * 0.5
    if y2 >= 0.88 * max(1, frame_h):
        return (cx, (y1 + y2) * 0.5)
    return (cx, y1 + 0.85 * (y2 - y1))


@dataclass
class PersonTrack:
    track_id: int
    bbox: np.ndarray
    first_seen: float
    last_seen: float
    hits: int = 1


@dataclass
class _ZoneOccupancy:
    zone_id: int
    person_track_id: int
    candidate_since: float
    last_inside_ts: float
    confirmed: bool = False
    enter_ts: float = 0.0
    db_session_id: int | None = None


class ZoneEngine:
    """Theo doi nguoi bang IoU nhe tren dau ra YOLOX-Nano va danh gia hien dien da vung theo giay thuc."""

    def __init__(self, cfg: dict, db):
        self.cfg = cfg
        self.db = db
        zcfg = cfg.get("zones") or {}
        self.default_enter_sec = float(zcfg.get("enter_confirm_sec", 1.0))
        self.default_min_dwell_sec = float(zcfg.get("min_dwell_sec", 3.0))
        self.default_exit_grace_sec = float(zcfg.get("exit_grace_sec", 2.0))
        self.alert_cooldown_sec = float(zcfg.get("alert_cooldown_sec", 30.0))

        self._lock = threading.Lock()
        self.zones: list[dict] = []
        self.person_tracks: dict[int, PersonTrack] = {}
        self._next_person_id = 1
        self._occ: dict[tuple[int, int], _ZoneOccupancy] = {}
        self._zone_understaffed_since: dict[int, float] = {}
        self._zone_last_alert_at: dict[int, float] = {}
        self._last_state_snapshot: list[dict] = []
        self.db.close_orphan_zone_sessions()
        self.reload_zones()

    def reload_zones(self) -> None:
        active_zones = self.db.list_zones(active_only=True)
        with self._lock:
            valid_ids = {int(z["id"]) for z in active_zones}
            now = time.time()
            for key in [k for k in self._occ if k[0] not in valid_ids]:
                occ = self._occ.pop(key)
                if occ.db_session_id is not None:
                    dw = max(0.0, occ.last_inside_ts - occ.enter_ts)
                    self.db.close_zone_session(occ.db_session_id, occ.last_inside_ts or now, dw)
            self.zones = active_zones

    @property
    def zone_count(self) -> int:
        with self._lock:
            return len(self.zones)

    def reset_runtime(self, now: float | None = None) -> None:
        ts_now = float(now if now is not None else time.time())
        with self._lock:
            for occ in self._occ.values():
                if occ.db_session_id is not None:
                    end_t = max(occ.enter_ts, occ.last_inside_ts or ts_now)
                    self.db.close_zone_session(occ.db_session_id, end_t, end_t - occ.enter_ts)
            self._occ.clear()
            self.person_tracks.clear()
            self._zone_understaffed_since.clear()
            self._last_state_snapshot = []

    def live_status(self) -> list[dict]:
        with self._lock:
            return list(self._last_state_snapshot)

    def _update_person_tracks(self, person_boxes: list[np.ndarray], now: float, max_age: float) -> list[PersonTrack]:
        used_t, used_d = set(), set()
        pairs = []
        for tid, tr in self.person_tracks.items():
            for di, box in enumerate(person_boxes):
                ov = _person_iou(tr.bbox, box)
                if ov >= 0.25:
                    pairs.append((ov, tid, di))
        for _, tid, di in sorted(pairs, reverse=True):
            if tid in used_t or di in used_d:
                continue
            tr = self.person_tracks[tid]
            tr.bbox = np.asarray(person_boxes[di][:4], dtype=np.float32)
            tr.last_seen = now
            tr.hits += 1
            used_t.add(tid)
            used_d.add(di)

        for di, box in enumerate(person_boxes):
            if di in used_d:
                continue
            tr = PersonTrack(
                track_id=self._next_person_id,
                bbox=np.asarray(box[:4], dtype=np.float32),
                first_seen=now,
                last_seen=now,
            )
            self._next_person_id += 1
            self.person_tracks[tr.track_id] = tr

        expired = [tid for tid, tr in self.person_tracks.items() if (now - tr.last_seen) > max_age]
        for tid in expired:
            del self.person_tracks[tid]
        return list(self.person_tracks.values())

    def update(
        self,
        person_boxes: list[np.ndarray],
        frame_shape: tuple[int, int],
        now: float | None = None,
        camera_name: str = "gate-1",
    ) -> dict:
        """Cap nhat danh sach hộp nguoi tren khung hinh hien tai.
        Tra ve: {person_tracks, zone_states, closed_sessions, alerts}.
        """
        now = float(now if now is not None else time.time())
        fh, fw = max(1, int(frame_shape[0])), max(1, int(frame_shape[1]))

        with self._lock:
            zones = list(self.zones)
            max_grace = max(
                [float(z.get("exit_grace_sec", self.default_exit_grace_sec)) for z in zones] + [self.default_exit_grace_sec]
            )
            tracks = self._update_person_tracks(person_boxes, now, max_age=max(2.5, max_grace + 0.8))
            fresh_tracks = [tr for tr in tracks if (now - tr.last_seen) <= 0.4]

            closed_sessions: list[dict] = []
            alerts: list[dict] = []
            zone_states: list[dict] = []
            person_zone_labels: dict[int, tuple[str, float]] = {}

            for z in zones:
                zid = int(z["id"])
                zcode = str(z["code"])
                zname = str(z["name"])
                poly_norm = z.get("polygon") or []
                if len(poly_norm) < 3:
                    continue
                poly_px = np.array([[p[0] * (fw - 1), p[1] * (fh - 1)] for p in poly_norm], dtype=np.float32).reshape(-1, 1, 2)
                enter_sec = float(z.get("enter_confirm_sec") if z.get("enter_confirm_sec") is not None else self.default_enter_sec)
                min_dwell = float(z.get("min_dwell_sec") if z.get("min_dwell_sec") is not None else self.default_min_dwell_sec)
                exit_grace = float(z.get("exit_grace_sec") if z.get("exit_grace_sec") is not None else self.default_exit_grace_sec)
                alert_empty = float(z.get("alert_empty_sec") or 0.0)
                min_persons = max(1, int(z.get("min_persons") or 1))

                inside_tids: set[int] = set()
                for tr in fresh_tracks:
                    ax, ay = _anchor_point(tr.bbox, fh)
                    if cv2.pointPolygonTest(poly_px, (ax, ay), False) >= 0:
                        inside_tids.add(tr.track_id)

                for tid in inside_tids:
                    key = (zid, tid)
                    occ = self._occ.get(key)
                    if occ is None:
                        occ = _ZoneOccupancy(
                            zone_id=zid,
                            person_track_id=tid,
                            candidate_since=now,
                            last_inside_ts=now,
                        )
                        self._occ[key] = occ
                    else:
                        occ.last_inside_ts = now

                    if not occ.confirmed and (now - occ.candidate_since) >= enter_sec:
                        occ.confirmed = True
                        occ.enter_ts = occ.candidate_since

                    if occ.confirmed:
                        dwell_now = max(0.0, now - occ.enter_ts)
                        person_zone_labels[tid] = (zcode, dwell_now)
                        if occ.db_session_id is None and dwell_now >= min_dwell:
                            occ.db_session_id = self.db.open_zone_session(
                                zone_id=zid,
                                zone_code=zcode,
                                zone_name=zname,
                                camera=camera_name,
                                person_track_id=tid,
                                enter_ts=occ.enter_ts,
                            )

                # Kiem tra cac occupancy cua zone nay dang vang mat
                for key in [k for k in self._occ if k[0] == zid and k[1] not in inside_tids]:
                    occ = self._occ[key]
                    absent_sec = now - occ.last_inside_ts
                    if not occ.confirmed:
                        if absent_sec > max(0.5, min(1.0, exit_grace * 0.5)):
                            del self._occ[key]
                        continue
                    if absent_sec > exit_grace:
                        exit_ts = occ.last_inside_ts
                        dwell_final = round(max(0.0, exit_ts - occ.enter_ts), 2)
                        if occ.db_session_id is not None:
                            self.db.close_zone_session(occ.db_session_id, exit_ts, dwell_final)
                            closed_sessions.append({
                                "id": occ.db_session_id,
                                "zone_id": zid,
                                "zone_code": zcode,
                                "zone_name": zname,
                                "camera": camera_name,
                                "person_track_id": occ.person_track_id,
                                "enter_ts": occ.enter_ts,
                                "exit_ts": exit_ts,
                                "dwell_sec": dwell_final,
                            })
                        elif dwell_final >= min_dwell:
                            sid = self.db.open_zone_session(
                                zone_id=zid,
                                zone_code=zcode,
                                zone_name=zname,
                                camera=camera_name,
                                person_track_id=occ.person_track_id,
                                enter_ts=occ.enter_ts,
                            )
                            self.db.close_zone_session(sid, exit_ts, dwell_final)
                            closed_sessions.append({
                                "id": sid,
                                "zone_id": zid,
                                "zone_code": zcode,
                                "zone_name": zname,
                                "camera": camera_name,
                                "person_track_id": occ.person_track_id,
                                "enter_ts": occ.enter_ts,
                                "exit_ts": exit_ts,
                                "dwell_sec": dwell_final,
                            })
                        del self._occ[key]

                confirmed_occs = [
                    occ for (z_i, _), occ in self._occ.items() if z_i == zid and occ.confirmed
                ]
                present_count = len(confirmed_occs)
                max_dwell_live = max([(now - o.enter_ts) for o in confirmed_occs], default=0.0)

                understaffed = present_count < min_persons
                empty_for = 0.0
                alert_active = False
                if understaffed:
                    since = self._zone_understaffed_since.setdefault(zid, now)
                    empty_for = max(0.0, now - since)
                    if alert_empty > 0.0 and empty_for >= alert_empty:
                        alert_active = True
                        last_al = self._zone_last_alert_at.get(zid, -1e18)
                        if (now - last_al) >= self.alert_cooldown_sec:
                            self._zone_last_alert_at[zid] = now
                            alerts.append({
                                "ts": now,
                                "zone_id": zid,
                                "zone_code": zcode,
                                "zone_name": zname,
                                "present_count": present_count,
                                "min_persons": min_persons,
                                "empty_sec": round(empty_for, 1),
                            })
                else:
                    self._zone_understaffed_since.pop(zid, None)

                zone_states.append({
                    "id": zid,
                    "code": zcode,
                    "name": zname,
                    "polygon": poly_norm,
                    "color": z.get("color") or "#3cc85a",
                    "present_count": present_count,
                    "min_persons": min_persons,
                    "max_dwell_sec": round(max_dwell_live, 1),
                    "empty_sec": round(empty_for, 1),
                    "alert": alert_active,
                })

            self._last_state_snapshot = zone_states
            return {
                "person_tracks": fresh_tracks,
                "person_zone_labels": person_zone_labels,
                "zone_states": zone_states,
                "closed_sessions": closed_sessions,
                "alerts": alerts,
            }
