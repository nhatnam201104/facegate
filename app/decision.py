"""May trang thai quyet dinh lien tuc TrackDecisionState + Novelty Gate + LLR 4 tham so tuan thu Nguyen tac I Constitution (H4, H25)."""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np


@dataclass
class LLRConfig:
    novelty_min_dt_ms: float = 250.0
    novelty_min_dyaw_deg: float = 8.0
    a_cos: float = 12.0
    b_bias: float = -4.8
    c_abs_yaw: float = -0.015
    d_quality: float = 0.60
    tau_threshold: float = 0.45
    margin_base: float = 0.70
    margin_yaw_slope: float = 0.30
    min_samples: int = 2

    def __post_init__(self) -> None:
        if self.a_cos <= 0:
            raise ValueError("a_cos bat buoc > 0 de bao toan tinh don dieu theo cosine")

    @classmethod
    def from_cfg(cls, cfg: dict) -> "LLRConfig":
        d = cfg.get("decision", {})
        lw = d.get("llr_weights", {})
        rc = cfg.get("recognition", {})
        return cls(
            novelty_min_dt_ms=float(d.get("novelty_min_dt_ms", 250.0)),
            novelty_min_dyaw_deg=float(d.get("novelty_min_dyaw_deg", 8.0)),
            a_cos=max(0.01, float(lw.get("a_cos", 12.0))),
            b_bias=float(lw.get("b_bias", -4.8)),
            c_abs_yaw=float(lw.get("c_abs_yaw", -0.015)),
            d_quality=float(lw.get("d_quality", 0.60)),
            tau_threshold=float(d.get("tau_threshold", 0.45)),
            margin_base=float(d.get("margin_base", 0.70)),
            margin_yaw_slope=float(d.get("margin_yaw_slope", 0.30)),
            min_samples=int(rc.get("min_good_frames", 2)),
        )


def compute_sample_llr(cosine_sim: float, yaw_deg: float, quality_score: float, cfg: LLRConfig) -> float:
    """LLR(s, yaw, q) = a*s + b + c*|yaw| + d*q — lien tuc khong nhay bac tai goc 40 do (H25)."""
    return float(
        cfg.a_cos * float(cosine_sim)
        + cfg.b_bias
        + cfg.c_abs_yaw * min(90.0, abs(float(yaw_deg)))
        + cfg.d_quality * float(quality_score)
    )


def required_llr_margin(mean_abs_yaw: float, cfg: LLRConfig) -> float:
    """Bien do cach biet top1 - top2 siet chat dan theo goc nghieng: mu(yaw) = mu_0 + mu_1 * |yaw| / 90."""
    ratio = min(1.0, max(0.0, abs(float(mean_abs_yaw)) / 90.0))
    return float(cfg.margin_base + cfg.margin_yaw_slope * ratio)


@dataclass
class TrackDecisionAccumulator:
    cfg: LLRConfig
    state: str = "SEEN"
    quarantine: bool = False
    last_accepted_ts_ms: float | None = None
    last_accepted_yaw: float | None = None
    sample_count: int = 0
    yaw_samples: list[float] = field(default_factory=list)
    candidate_llrs: dict[int, list[float]] = field(default_factory=dict)
    locked_employee_id: int | None = None
    verification_tier: str | None = None
    top1_llr: float = -999.0
    llr_margin: float = 0.0

    def enter_quarantine(self) -> None:
        """Crossing Quarantine (H3): xoa mau giao cat, cam chot check-in va buoc thu thap lai tu dau."""
        self.quarantine = True
        self.state = "QUARANTINED"
        self.reset_evidence()

    def exit_quarantine(self) -> None:
        """Sau khi 2 hop tach khoi nhau: bat dau thu thap lai tu dau nhu mot track moi."""
        self.quarantine = False
        if self.state == "QUARANTINED":
            self.state = "COLLECTING"

    def reset_evidence(self) -> None:
        self.sample_count = 0
        self.yaw_samples.clear()
        self.candidate_llrs.clear()
        self.last_accepted_ts_ms = None
        self.last_accepted_yaw = None
        self.locked_employee_id = None
        self.verification_tier = None
        self.top1_llr = -999.0
        self.llr_margin = 0.0

    def passes_novelty_gate(self, ts_ms: float, yaw_deg: float) -> bool:
        if self.last_accepted_ts_ms is None or self.last_accepted_yaw is None:
            return True
        dt = float(ts_ms) - self.last_accepted_ts_ms
        dyaw = abs(float(yaw_deg) - self.last_accepted_yaw)
        return dt >= self.cfg.novelty_min_dt_ms or dyaw >= self.cfg.novelty_min_dyaw_deg

    def observe(self, ts_ms: float, yaw_deg: float, quality_score: float,
                sims_by_employee: dict[int, float]) -> bool:
        """Nap mau moi neu vuot Novelty Gate va khong dang o trong pha giao cat quarantine."""
        if self.quarantine:
            self.state = "QUARANTINED"
            return False
        if not self.passes_novelty_gate(ts_ms, yaw_deg):
            return False

        self.last_accepted_ts_ms = float(ts_ms)
        self.last_accepted_yaw = float(yaw_deg)
        self.sample_count += 1
        self.yaw_samples.append(float(yaw_deg))
        if self.state == "SEEN":
            self.state = "COLLECTING"

        if not sims_by_employee:
            return True

        for emp_id, sim in sims_by_employee.items():
            val = compute_sample_llr(sim, yaw_deg, quality_score, self.cfg)
            self.candidate_llrs.setdefault(int(emp_id), []).append(val)
        self._evaluate_identity()
        return True

    def _evaluate_identity(self) -> None:
        if self.sample_count < self.cfg.min_samples or not self.candidate_llrs:
            return
        means = [(eid, float(np.mean(vals))) for eid, vals in self.candidate_llrs.items() if vals]
        means.sort(key=lambda kv: kv[1], reverse=True)
        top_id, top_val = means[0]
        # Neu gallery chi co 1 nhan vien duy nhat, second lay muc baseline am (b_bias) cho s=0
        second_val = means[1][1] if len(means) > 1 else float(self.cfg.b_bias)
        mean_abs_yaw = float(np.mean([abs(y) for y in self.yaw_samples])) if self.yaw_samples else 0.0
        req_margin = required_llr_margin(mean_abs_yaw, self.cfg)

        self.top1_llr = round(top_val, 4)
        self.llr_margin = round(top_val - second_val, 4)
        if top_val >= self.cfg.tau_threshold and (top_val - second_val) >= req_margin:
            self.locked_employee_id = top_id
            min_yaw = min((abs(y) for y in self.yaw_samples), default=90.0)
            self.verification_tier = "verified" if min_yaw <= 40.0 else "profile_verified"
            if self.state in ("SEEN", "COLLECTING", "IDENTIFIED"):
                self.state = "IDENTIFIED"

    def apply_liveness(self, live_status: str) -> str:
        """Chuyen trang thai theo ket qua Liveness: 'live' | 'checking' | 'spoof'."""
        if self.quarantine:
            self.state = "QUARANTINED"
            return self.state
        if live_status == "spoof":
            self.state = "REJECTED_SPOOF"
            self.verification_tier = "rejected_spoof"
        elif live_status == "live" and self.locked_employee_id is not None:
            if self.state != "COMMITTED":
                self.state = "LIVE_CONFIRMED"
        return self.state

    def mark_committed(self) -> None:
        if self.quarantine:
            raise RuntimeError("Cam phat check-in khi track con co quarantine")
        self.state = "COMMITTED"

    def finalize_on_expire(self) -> str:
        if self.state in ("SEEN", "COLLECTING", "QUARANTINED") and self.locked_employee_id is None:
            self.state = "UNKNOWN"
        return self.state


def fit_llr_logistic_4param(samples: list[tuple[float, float, float, int]],
                            lr: float = 0.08, steps: int = 400) -> dict[str, float]:
    """Fit 4 tham so (a_cos > 0, b_bias, c_abs_yaw, d_quality) tren cap cung nguoi (y=1) / khac nguoi (y=0) tu K0."""
    if not samples:
        return {"a_cos": 12.0, "b_bias": -4.8, "c_abs_yaw": -0.015, "d_quality": 0.60}
    X = np.array([[s, 1.0, abs(yaw), q] for s, yaw, q, _ in samples], dtype=np.float64)
    y = np.array([lbl for _, _, _, lbl in samples], dtype=np.float64)
    w = np.array([10.0, -4.0, -0.01, 0.5], dtype=np.float64)
    for _ in range(steps):
        z = np.clip(X @ w, -30.0, 30.0)
        p = 1.0 / (1.0 + np.exp(-z))
        grad = (X.T @ (p - y)) / len(y) + np.array([1e-3 * w[0], 0.0, 1e-3 * w[2], 1e-3 * w[3]])
        w -= lr * grad
        w[0] = max(0.5, w[0])  # rang buoc a_cos > 0
    return {
        "a_cos": round(float(w[0]), 4),
        "b_bias": round(float(w[1]), 4),
        "c_abs_yaw": round(float(w[2]), 5),
        "d_quality": round(float(w[3]), 4),
    }
