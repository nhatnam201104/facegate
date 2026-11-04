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


@dataclass(frozen=True)
class Step:
    bin: str          # ten o goc luu vao DB
    h: int            # huong yaw can dat: -1 | 0 | +1 (dau cua yaw)
    v: int            # huong pitch can dat: -1 (ngang len) | 0 | +1 (cui)
    text: str         # cau huong dan hien tren man hinh


# 5 goc chu thap, thu tu co dinh (cac goc cheo kho thuc hien va it gap luc cham cong nen khong yeu cau)
STEPS: list[Step] = [
    Step("thang-giua", 0, 0, "Nhìn thẳng vào camera"),
    Step("trai-giua", -1, 0, "Quay đầu sang phải"),
    Step("phai-giua", +1, 0, "Quay đầu sang trái"),
    Step("thang-ngang", 0, -1, "Ngẩng đầu lên"),
    Step("thang-cui", 0, +1, "Cúi đầu xuống"),
]

QUALITY_HINT = {
    "small": "Lại gần camera hơn",
    "blur": "Giữ yên đầu, hình đang bị mờ",
    "light": "Ánh sáng chưa phù hợp, hãy đứng chỗ đủ sáng",
    "yaw": "Quay lại một chút",
    "cut": "Lùi ra giữa khung hình, mặt đang bị cắt",
    "out_of_frame": "Đưa khuôn mặt vào khung hình",
}

DEFAULTS = {
    "yaw_enter": 15.0,      # do lech yaw so voi nhin thang de tinh la "da quay"
    "yaw_center": 9.0,      # trong khoang nay la "thang"
    "pitch_enter": 10.0,
    "pitch_center": 8.0,
    "max_turn": 45.0,       # qua muc nay -> bao quay qua
    "hold_seconds": 0.7,    # phai giu dung goc lien tuc chung nay
    "grace_seconds": 0.3,   # cho phep mat/lech tuc thoi (nhieu) ngan hon muc nay ma khong reset
    "pause_seconds": 0.8,   # nghi giua cac buoc de nguoi dung thay dau tick
    "timeout_seconds": 240, # tong thoi gian toi da cua mot phien
    "min_identity_cos": 0.35,  # moi buoc phai cung nguoi voi buoc nhin thang
    "front_max_abs_yaw": 10.0,  # buoc 1: khong quay ngang
    "front_max_abs_pitch": 18.0,
    "poll_interval": 0.08,
}


def evaluate(step: Step, dyaw: float, dpitch: float, p: dict) -> tuple[bool, str]:
    """Dung/sai cua mot mau (yaw, pitch da tru baseline) voi buoc muc tieu + cau goi y. Ham thuan -> de test."""
    # --- ngang
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
    def __init__(self, ctx, code: str, name: str):
        self.id = uuid.uuid4().hex[:12]
        self.ctx, self.code, self.name = ctx, code, name
        ecfg = ctx.cfg["enrollment"]
        self.p = {**DEFAULTS, **(ecfg.get("guided") or {})}
        self.qcfg = dict(ctx.cfg["quality"])
        self.qcfg["max_yaw"] = max(self.qcfg.get("max_yaw", 35), self.p["max_turn"])
        self.state = "running"          # running | done | failed | cancelled
        self.step = 0
        self.done: list[bool] = [False] * len(STEPS)
        self.hint = "Đưa khuôn mặt vào khung hình"
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
            cur = STEPS[min(self.step, len(STEPS) - 1)]
            return {
                "id": self.id, "state": self.state, "step": self.step, "total": len(STEPS),
                "instruction": cur.text if self.state == "running" else "",
                "target": {"h": {-1: "right", 1: "left"}.get(cur.h), "v": {-1: "up", 1: "down"}.get(cur.v)},
                "hint": self.hint, "ok": self.ok, "hold": round(self.hold, 2),
                "steps": [{"bin": s.bin, "text": s.text, "done": d, "h": s.h, "v": s.v}
                          for s, d in zip(STEPS, self.done)],
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
        """Tra ve (face, quality, hint). face=None neu khong du dieu kien de xet goc."""
        ctx = self.ctx
        small, s = _resize_for_detect(frame)
        with ctx.engine_lock:
            faces = ctx.engine.detect(small)
        self.stats["frames_total"] += 1
        if not faces:
            self.stats["no_face"] += 1
            return None, None, "Đưa khuôn mặt vào khung hình"
        for f in faces:
            f.bbox, f.kps = f.bbox / s, f.kps / s
        faces.sort(key=lambda f: f.w * f.h, reverse=True)
        if len(faces) > 1 and faces[1].w * faces[1].h > 0.4 * faces[0].w * faces[0].h:
            self.stats["multi_face"] += 1
            return None, None, "Chỉ một người trong khung hình"
        q = assess(frame, faces[0], self.qcfg)
        if not q.ok:
            self.stats["rejected"][q.reason] = self.stats["rejected"].get(q.reason, 0) + 1
            return None, q, QUALITY_HINT.get(q.reason, "Chất lượng hình chưa đạt")
        return faces[0], q, ""

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

        while self.step < len(STEPS):
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
            if now < pause_until:          # dang hien dau tick cua buoc truoc
                continue

            step = STEPS[self.step]
            face, q, hint = self._analyse(frame)
            ok = False
            dyaw = dpitch = 0.0
            if face is not None:
                by, bp = self._baseline
                dyaw, dpitch = q.yaw - by, q.pitch - bp
                if self.step == 0:
                    # buoc 1 chua co baseline: chi can dung thang tuong doi (yaw ~ 0, pitch trong khoang hop ly)
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
                    hold_best = {"q": q, "frame": frame.copy(), "face": face}
                prog = min(1.0, (now - hold_start) / p["hold_seconds"])
                self._set(ok=True, hold=prog, hint=hint)
                if prog < 1.0:
                    continue
                # ---- du thoi gian giu dung goc -> chot buoc
                err = self._commit(step, hold_best, hold_poses, front_emb)
                if err:
                    hold_start, hold_best, hold_poses = None, None, []
                    self._set(ok=False, hold=0.0, hint=err)
                    continue
                if self.step == 0:
                    front_emb = self.picked[0]["emb"]
                    self._baseline = tuple(np.mean(hold_poses, axis=0))
                with self._lock:
                    self.done[self.step] = True
                    self.step += 1
                    self.ok, self.hold = False, 0.0
                    self.hint = ("Hoàn tất, đang xử lý…" if self.step >= len(STEPS)
                                 else f"Tốt! Tiếp theo: {STEPS[self.step].text}")
                hold_start, hold_best, hold_poses = None, None, []
                pause_until = time.time() + p["pause_seconds"]
            else:
                # cho phep nhieu tuc thoi ngan, qua grace moi reset tien do
                fail_since = fail_since or now
                if hold_start is not None and now - fail_since > p["grace_seconds"]:
                    hold_start, hold_best, hold_poses = None, None, []
                self._set(ok=False, hint=hint, hold=0.0 if hold_start is None else self.hold)

        self._finish()

    def _commit(self, step: Step, best: dict, poses: list, front_emb) -> str | None:
        """Tinh embedding cho frame tot nhat cua buoc. Tra ve thong bao loi (buoc khong duoc chot) hoac None."""
        ctx = self.ctx
        with ctx.engine_lock:
            emb = ctx.engine.embed_aligned(ctx.engine.align(best["frame"], best["face"]))
        if front_emb is not None and float(emb @ front_emb) < self.p["min_identity_cos"]:
            return "Khuôn mặt không khớp với bước đầu, chỉ một người thực hiện đăng ký"
        self.picked.append({"emb": emb, "q": best["q"], "bin": step.bin,
                            "crop": crop_with_margin(best["frame"], best["face"].bbox)})
        return None

    def _finish(self) -> None:
        self._set(hint="Đang xử lý…")
        try:
            report = store_enrollment(self.picked, self.code, self.name, self.ctx, self.stats,
                                      n_good=len(self.picked), n_outliers=0,
                                      bin_names=[s.bin for s in STEPS], guided=True)
        except EnrollError as e:
            return self._fail(str(e))
        self.ctx.gallery.reload()
        bc({"type": "employees"})
        self._set(state="done", report=report, hint="Đăng ký hoàn tất", ok=False, hold=0.0)
