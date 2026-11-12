"""Dang ky khuon mat co huong dan (kieu app ngan hang): 5 goc chu thap co dinh, dat dung goc moi sang buoc tiep.

Quy uoc huong (preview hien thi LAT guong, nhu soi guong):
  yaw < 0  = mui xe dich sang trai-anh-goc = nguoi dung quay sang PHAI cua ho = mui ten sang phai tren man hinh
  yaw > 0  = nguoi dung quay sang TRAI cua ho
  pitch < 0 = ngang len, pitch > 0 = cui xuong
Ten o goc (bin) giu nguyen quy uoc cua enrollment._pose_bin ("trai" = yaw am) de du lieu cu khong doi nghia.
Goc do tinh so voi tu the nhin thang cua chinh nguoi do (buoc 1), vi pitch uoc luong tu 5 landmark lech theo tung nguoi.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass

import numpy as np

from .enrollment import EnrollError, _resize_for_detect, store_enrollment
from .pipeline import crop_with_margin
from .quality import assess
from .state import bc

log = logging.getLogger("guided")


def load_employee_frontal_embeddings(db, code: str, model_version: str) -> list[np.ndarray]:
    emp = db.get_employee_by_code(code)
    if not emp:
        return []
    rows = db.q(
        """SELECT vec, dim FROM embeddings
           WHERE employee_id=? AND model_version=? AND (pose_group='frontal' OR pose_group IS NULL)""",
        (emp["id"], model_version),
    )
    return [np.frombuffer(r["vec"], dtype=np.float32, count=r["dim"]).copy() for r in rows]


@dataclass(frozen=True)
class Step:
    bin: str          # ten o goc luu vao DB
    h: int            # huong yaw can dat: -2 (nghieng sau phai) | -1 | 0 | +1 | +2 (nghieng sau trai)
    v: int            # huong pitch can dat: -1 (ngang len) | 0 | +1 (cui)
    text: str         # cau huong dan hien tren man hinh
    pose_group: str = "frontal"
    verify_only: bool = False


# 5 goc chu thap co ban
STEPS: list[Step] = [
    Step("thang-giua", 0, 0, "Nhìn thẳng vào camera", "frontal"),
    Step("trai-giua", -1, 0, "Quay đầu nhẹ sang phải (~25°)", "frontal"),
    Step("phai-giua", +1, 0, "Quay đầu nhẹ sang trái (~25°)", "frontal"),
    Step("thang-ngang", 0, -1, "Ngẩng đầu lên", "frontal"),
    Step("thang-cui", 0, +1, "Cúi đầu xuống", "frontal"),
]

# Buoc xac minh danh tinh chinh dien truoc khi quay bo sung goc nghieng (khong luu them mau frontal)
VERIFY_FRONT_STEP = Step("xac-minh-thang", 0, 0, "Nhìn thẳng vào camera để xác minh danh tính", "frontal", verify_only=True)
PROFILE_STEPS: list[Step] = [
    Step("nghieng-sau-trai", -2, 0, "Quay hẳn mặt sang phải (~80°)", "left_profile"),
    Step("nghieng-sau-phai", +2, 0, "Quay hẳn mặt sang trái (~80°)", "right_profile"),
]

QUALITY_HINT = {
    "small": "Lại gần camera hơn",
    "blur": "Giữ yên đầu, hình đang bị mờ",
    "light": "Ánh sáng chưa phù hợp, hãy đứng chỗ đủ sáng",
    "yaw": "Quay lại một chút",
    "pitch": "Giữ đầu ngang tầm mắt, đừng ngẩng/cúi quá mức",
    "roll": "Giữ đầu thẳng đứng, đừng nghiêng vai",
    "occluded": "Khuôn mặt đang bị che khuất, hãy bỏ vật che trước mặt",
    "cut": "Lùi ra giữa khung hình, mặt đang bị cắt",
    "out_of_frame": "Đưa khuôn mặt vào khung hình",
}

DEFAULTS = {
    "enable_profile_90": True,
    "yaw_enter": 15.0,      # do lech yaw so voi nhin thang de tinh la "da quay" (buoc ban nghieng)
    "yaw_center": 9.0,      # trong khoang nay la "thang"
    "profile_yaw_min": 65.0,
    "profile_yaw_max": 88.0,
    "pitch_enter": 10.0,
    "pitch_center": 8.0,
    "max_turn": 45.0,       # qua muc nay cho buoc ban nghieng -> bao quay qua
    "hold_seconds": 0.7,    # phai giu dung goc lien tuc chung nay
    "grace_seconds": 0.3,   # cho phep mat/lech tuc thoi (nhieu) ngan hon muc nay ma khong reset
    "pause_seconds": 0.8,   # nghi giua cac buoc de nguoi dung thay dau tick
    "timeout_seconds": 300, # tong thoi gian toi da cua mot phien
    "min_identity_cos": 0.35,  # kiem tra cung nguoi cho cac buoc trong cung nhom frontal
    "front_max_abs_yaw": 10.0,
    "front_max_abs_pitch": 18.0,
    "poll_interval": 0.08,
}


def evaluate(step: Step, dyaw: float, dpitch: float, p: dict) -> tuple[bool, str]:
    """Dung/sai cua mot mau (yaw, pitch da tru baseline) voi buoc muc tieu + cau goi y. Ham thuan -> de test."""
    # --- buoc nghieng suon sau (~65..88 do)
    if abs(step.h) == 2:
        side = "phải" if step.h < 0 else "trái"
        p_min = float(p.get("profile_yaw_min", 65.0))
        p_max = float(p.get("profile_yaw_max", 88.0))
        if dyaw * step.h <= 0:
            return False, f"Quay hẳn mặt sang {side}"
        if abs(dyaw) < p_min:
            return False, f"Xoay thêm sang {side} cho tới góc nghiêng sườn (~80°)"
        if abs(dyaw) > p_max:
            return False, "Quay quá sát 90°, quay lại một chút"
        if abs(dpitch) > max(float(p["pitch_center"]) * 2.2, 18.0):
            return False, "Giữ đầu ngang tầm mắt, đừng ngẩng hoặc cúi"
        return True, "Đúng góc nghiêng rồi, giữ nguyên"

    # --- ngang (buoc chu thap co ban)
    if step.h == 0:
        if abs(dyaw) > p["yaw_center"]:
            return False, "Nhìn thẳng vào camera, đừng xoay ngang"
    else:
        if dyaw * step.h < 0 or abs(dyaw) < p["yaw_enter"]:
            side = "phải" if step.h < 0 else "trái"
            return False, f"Xoay đầu thêm sang {side}" if dyaw * step.h > 0 else f"Xoay đầu sang {side}"
        if abs(dyaw) > p["max_turn"]:
            return False, "Quay quá nhiều, quay lại một chút"
    # --- doc
    if step.v == 0:
        if abs(dpitch) > p["pitch_center"]:
            return False, "Giữ đầu thẳng, đừng ngẩng hoặc cúi"
    else:
        if dpitch * step.v < 0 or abs(dpitch) < p["pitch_enter"]:
            what = "Ngẩng đầu lên thêm" if step.v < 0 else "Cúi đầu xuống thêm"
            return False, what if dpitch * step.v > 0 else what.replace(" thêm", "")
        if abs(dpitch) > p["max_turn"]:
            return False, "Nghiêng quá nhiều, quay lại một chút"
    return True, "Đúng rồi, giữ nguyên"


class GuidedSession:
    def __init__(self, ctx, code: str, name: str,
                 profile_90: bool | None = None, supplement_only: bool = False):
        self.id = uuid.uuid4().hex[:12]
        self.ctx, self.code, self.name = ctx, code, name
        ecfg = ctx.cfg["enrollment"]
        self.p = {**DEFAULTS, **(ecfg.get("guided") or {})}
        self.cross_pose_cos = float(ecfg.get("cross_pose_min_cosine", 0.22))
        use_90 = bool(self.p.get("enable_profile_90", True)) if profile_90 is None else bool(profile_90)
        self.supplement_only = bool(supplement_only)
        self._verify_refs: list[np.ndarray] = []
        self._verified_front_emb: np.ndarray | None = None
        if self.supplement_only:
            self._verify_refs = load_employee_frontal_embeddings(ctx.db, code, ctx.engine.model_version)
            if not self._verify_refs:
                raise EnrollError(f"Nhân viên {code} chưa có mẫu khuôn mặt chính diện cho model hiện tại. Hãy thực hiện đăng ký đầy đủ trước.")
            self.steps = [VERIFY_FRONT_STEP, *PROFILE_STEPS]
        else:
            self.steps = list(STEPS) + (list(PROFILE_STEPS) if use_90 else [])
        self.qcfg = dict(ctx.cfg["quality"])
        max_needed_yaw = float(self.p.get("profile_yaw_max", 88.0)) + 4.0 if (use_90 or self.supplement_only) else self.p["max_turn"]
        self.qcfg["max_yaw"] = max(float(self.qcfg.get("max_yaw", 90)), max_needed_yaw)
        self.state = "running"          # running | done | failed | cancelled
        self.step = 0
        self.done: list[bool] = [False] * len(self.steps)
        self.hint = "Đưa khuôn mặt vào khung hình"
        self.current_pose = {"yaw": 0.0, "pitch": 0.0, "roll": 0.0}
        self.ok = False                 # frame hien tai dung goc
        self.hold = 0.0                 # 0..1 tien do giu dung goc
        self.error: str | None = None
        self.report: dict | None = None
        self.picked: list[dict] = []
        self.stats = {"frames_total": 0, "no_face": 0, "multi_face": 0, "rejected": {}}
        self._baseline = (0.0, 0.0)
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"guided-{self.id}")
        self._lock = threading.Lock()

    # ------------------------------------------------------------ public
    def start(self) -> "GuidedSession":
        self._thread.start()
        return self

    def cancel(self) -> None:
        self._cancel.set()

    def snapshot(self) -> dict:
        with self._lock:
            cur = self.steps[min(self.step, len(self.steps) - 1)]
            h_map = {-2: "right_deep", -1: "right", 1: "left", 2: "left_deep"}
            target_yaw = -78 if cur.h == -2 else (78 if cur.h == 2 else (-22 if cur.h == -1 else (22 if cur.h == 1 else 0)))
            return {
                "id": self.id, "state": self.state, "step": self.step, "total": len(self.steps),
                "supplement_only": self.supplement_only,
                "instruction": cur.text if self.state == "running" else "",
                "target": {"h": h_map.get(cur.h), "v": {-1: "up", 1: "down"}.get(cur.v), "target_yaw": target_yaw},
                "current_pose": dict(self.current_pose),
                "hint": self.hint, "ok": self.ok, "hold": round(self.hold, 2),
                "steps": [{"bin": s.bin, "pose_group": s.pose_group, "text": s.text, "done": d, "h": s.h, "v": s.v}
                          for s, d in zip(self.steps, self.done)],
                "error": self.error, "report": self.report,
            }

    # ------------------------------------------------------------ internals
    def _set(self, **kw) -> None:
        with self._lock:
            for k, v in kw.items():
                setattr(self, k, v)

    def _fail(self, msg: str) -> None:
        self._set(state="failed", error=msg, ok=False, hold=0.0)

    def _analyse(self, frame):
        """Tra ve (face, quality, pose, hint). face=None neu khong du dieu kien de xet goc."""
        ctx = self.ctx
        pose_est = getattr(ctx, "pose", None)
        small, s = _resize_for_detect(frame)
        with ctx.engine_lock:
            faces = ctx.engine.detect(small)
        self.stats["frames_total"] += 1
        if not faces:
            self.stats["no_face"] += 1
            return None, None, None, "Đưa khuôn mặt vào khung hình"
        for f in faces:
            f.bbox, f.kps = f.bbox / s, f.kps / s
        faces.sort(key=lambda f: f.w * f.h, reverse=True)
        if len(faces) > 1 and faces[1].w * faces[1].h > 0.4 * faces[0].w * faces[0].h:
            self.stats["multi_face"] += 1
            return None, None, None, "Chỉ một người trong khung hình"
        with ctx.engine_lock:
            pose = pose_est.estimate(frame, faces[0]) if pose_est is not None else None
        q = assess(frame, faces[0], self.qcfg, pose=pose)
        self._set(current_pose={"yaw": round(q.yaw, 1), "pitch": round(q.pitch, 1), "roll": round(q.roll, 1)})
        if not q.ok:
            self.stats["rejected"][q.reason] = self.stats["rejected"].get(q.reason, 0) + 1
            return None, q, pose, QUALITY_HINT.get(q.reason, "Chất lượng hình chưa đạt")
        return faces[0], q, pose, ""

    def _run(self) -> None:
        try:
            self._loop()
        except Exception as e:  # noqa: BLE001 - phien chay trong thread, khong de lam treo UI
            log.exception("Phien dang ky loi")
            self._fail(f"Lỗi khi đăng ký: {e}")

    def _loop(self) -> None:
        ctx, p = self.ctx, self.p
        t_start = time.time()
        last_id = -1
        hold_start: float | None = None
        fail_since: float | None = None
        hold_best: dict | None = None
        hold_poses: list[tuple[float, float]] = []
        pause_until = 0.0
        front_emb: np.ndarray | None = None
        prev_face_center: tuple[float, float] | None = None

        while self.step < len(self.steps):
            if self._cancel.is_set():
                self._set(state="cancelled", ok=False, hold=0.0)
                return
            if time.time() - t_start > p["timeout_seconds"]:
                return self._fail("Hết thời gian. Hãy bấm Bắt đầu và thực hiện lại.")
            if not ctx.camera.connected:
                self._set(hint="Camera mất kết nối…", ok=False, hold=0.0)
                time.sleep(0.3)
                continue
            fid, frame = ctx.camera.read()
            if frame is None or fid == last_id:
                time.sleep(p["poll_interval"])
                continue
            last_id = fid
            now = time.time()
            if now < pause_until:
                continue

            step = self.steps[self.step]
            face, q, pose, hint = self._analyse(frame)
            ok = False
            dyaw = dpitch = 0.0
            if face is not None:
                prev_face_center = (float(face.bbox[0] + face.bbox[2]) * 0.5, float(face.bbox[1] + face.bbox[3]) * 0.5)
                by, bp = self._baseline
                dyaw, dpitch = q.yaw - by, q.pitch - bp
                if self.step == 0:
                    if abs(q.yaw) > p["front_max_abs_yaw"]:
                        ok, hint = False, "Nhìn thẳng vào camera"
                    elif abs(q.pitch) > p["front_max_abs_pitch"]:
                        ok, hint = False, "Giữ đầu thẳng, nhìn vào camera"
                    else:
                        ok, hint = True, "Đúng rồi, giữ nguyên"
                else:
                    ok, hint = evaluate(step, dyaw, dpitch, p)

            if ok:
                fail_since = None
                if hold_start is None:
                    hold_start, hold_best, hold_poses = now, None, []
                hold_poses.append((q.yaw, q.pitch))
                if hold_best is None or q.score > hold_best["q"].score:
                    hold_best = {"q": q, "frame": frame.copy(), "face": face, "pose": pose, "center": prev_face_center}
                prog = min(1.0, (now - hold_start) / p["hold_seconds"])
                self._set(ok=True, hold=prog, hint=hint)
                if prog < 1.0:
                    continue
                err = self._commit(step, hold_best, hold_poses, front_emb)
                if self.state != "running":
                    return
                if err:
                    hold_start, hold_best, hold_poses = None, None, []
                    pause_until = time.time() + max(float(p["pause_seconds"]), 0.25)
                    self._set(ok=False, hold=0.0, hint=err)
                    continue
                if self.step == 0:
                    front_emb = self._verified_front_emb if step.verify_only else self.picked[0]["emb"]
                    self._baseline = tuple(np.mean(hold_poses, axis=0))
                with self._lock:
                    self.done[self.step] = True
                    self.step += 1
                    self.ok, self.hold = False, 0.0
                    self.hint = ("Hoàn tất, đang xử lý…" if self.step >= len(self.steps)
                                 else f"Tốt! Tiếp theo: {self.steps[self.step].text}")
                hold_start, hold_best, hold_poses = None, None, []
                pause_until = time.time() + p["pause_seconds"]
            else:
                fail_since = fail_since or now
                if hold_start is not None and now - fail_since > p["grace_seconds"]:
                    hold_start, hold_best, hold_poses = None, None, []
                self._set(ok=False, hint=hint, hold=0.0 if hold_start is None else self.hold)

        self._finish()

    def _commit(self, step: Step, best: dict, poses: list, front_emb) -> str | None:
        """Tinh embedding theo khung can chinh cua step.pose_group va doi chieu danh tinh."""
        ctx = self.ctx
        with ctx.engine_lock:
            aligned = ctx.engine.align(best["frame"], best["face"], pose=best.get("pose"), pose_group=step.pose_group)
            emb = ctx.engine.embed_aligned(aligned)
        if step.verify_only:
            rc = ctx.cfg.get("recognition", {})
            eng_name = getattr(ctx.engine, "name", "edgeface")
            thr_cfg = (rc.get("thresholds") or {}).get(eng_name)
            front_thr = float(getattr(ctx, "threshold", 0.40))
            if isinstance(thr_cfg, dict) and "frontal" in thr_cfg:
                front_thr = float(thr_cfg["frontal"])
            best_sim = max((float(emb @ ref) for ref in self._verify_refs), default=-1.0)
            if best_sim < front_thr:
                self._fail(
                    f"Khuôn mặt không khớp với hồ sơ chính diện của nhân viên {self.code} "
                    f"(cosine {best_sim:.2f} < {front_thr:.2f}). Không thể bổ sung góc nghiêng."
                )
                return self.error
            self._verified_front_emb = emb
            return None
        if step.pose_group == "frontal" and front_emb is not None and float(emb @ front_emb) < self.p["min_identity_cos"]:
            return "Khuôn mặt không khớp với bước đầu, chỉ một người thực hiện đăng ký"
        if step.pose_group != "frontal" and front_emb is not None:
            # emb của bước profile được căn chỉnh theo TEMPLATE_PROFILE nên lệch hệ quy chiếu với front_emb (TEMPLATE_FRONTAL).
            # Đối chiếu danh tính chéo góc trên cùng hệ quy chiếu 'frontal' với các mẫu frontal đã xác nhận (gồm cả bán nghiêng ±25°).
            with ctx.engine_lock:
                canon_aligned = ctx.engine.align(
                    best["frame"], best["face"], pose=best.get("pose"), pose_group="frontal"
                )
                canon_emb = ctx.engine.embed_aligned(canon_aligned)
            frontal_refs = [front_emb] + [c["emb"] for c in self.picked if c["pose_group"] == "frontal"]
            if self.supplement_only and self._verify_refs:
                frontal_refs.extend(self._verify_refs)
            best_cross_sim = max(
                max(float(canon_emb @ ref) for ref in frontal_refs),
                float(emb @ front_emb),
            )
            if best_cross_sim < self.cross_pose_cos:
                return "Khuôn mặt góc nghiêng không khớp với bước đầu, chỉ một người thực hiện đăng ký"
        self.picked.append({
            "emb": emb,
            "q": best["q"],
            "bin": step.bin,
            "pose_group": step.pose_group,
            "crop": crop_with_margin(best["frame"], best["face"].bbox),
        })
        return None

    def _finish(self) -> None:
        self._set(hint="Đang xử lý…")
        try:
            report = store_enrollment(
                self.picked, self.code, self.name, self.ctx, self.stats,
                n_good=len(self.picked), n_outliers=0,
                bin_names=[s.bin for s in self.steps if not s.verify_only],
                guided=True, supplement_only=self.supplement_only,
            )
        except EnrollError as e:
            return self._fail(str(e))
        self.ctx.gallery.reload()
        bc({"type": "employees"})
        self._set(state="done", report=report, hint="Đăng ký hoàn tất", ok=False, hold=0.0)
