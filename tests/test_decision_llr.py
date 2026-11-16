from __future__ import annotations

import pytest

from app.decision import (
    LLRConfig,
    TrackDecisionAccumulator,
    compute_sample_llr,
    fit_llr_logistic_4param,
    required_llr_margin,
)


def test_novelty_gate_decorrelates_static_frames():
    cfg = LLRConfig(novelty_min_dt_ms=250.0, novelty_min_dyaw_deg=8.0, min_samples=2)
    acc = TrackDecisionAccumulator(cfg=cfg)
    assert acc.observe(1000.0, yaw_deg=5.0, quality_score=0.9, sims_by_employee={1: 0.65, 2: 0.20}) is True
    # Dung yen sau 50ms va goc xoay chi lech 1 do -> bi Novelty Gate tu choi
    assert acc.observe(1050.0, yaw_deg=6.0, quality_score=0.9, sims_by_employee={1: 0.65, 2: 0.20}) is False
    assert acc.sample_count == 1
    assert acc.locked_employee_id is None

    # Xoay dau them 10 do du chi moi qua 80ms -> duoc chap nhan!
    assert acc.observe(1080.0, yaw_deg=16.0, quality_score=0.9, sims_by_employee={1: 0.66, 2: 0.18}) is True
    assert acc.sample_count == 2
    assert acc.locked_employee_id == 1
    assert acc.state == "IDENTIFIED"


def test_continuous_llr_around_40_deg_and_constitution_margin_gate():
    cfg = LLRConfig(tau_threshold=0.45, margin_base=0.80, margin_yaw_slope=0.40, min_samples=2)
    # Lien tuc quanh 40 do (khong nhay bac dot ngot giua 39.9 va 40.1 do)
    l39 = compute_sample_llr(0.55, 39.9, 0.8, cfg)
    l40 = compute_sample_llr(0.55, 40.1, 0.8, cfg)
    assert abs(l39 - l40) < 0.01

    # Margin o goc nghieng 80 do chat hon goc thang 0 do
    assert required_llr_margin(80.0, cfg) > required_llr_margin(0.0, cfg)

    # Vuot threshold nhung KHONG vuot margin top1 - top2 -> Nguyen tac I cam chot danh tinh!
    acc = TrackDecisionAccumulator(cfg=cfg)
    acc.observe(1000.0, yaw_deg=0.0, quality_score=0.9, sims_by_employee={1: 0.62, 2: 0.60})
    acc.observe(1300.0, yaw_deg=0.0, quality_score=0.9, sims_by_employee={1: 0.62, 2: 0.60})
    assert acc.locked_employee_id is None
    assert acc.state == "COLLECTING"


def test_fit_llr_enforces_positive_cosine_slope():
    with pytest.raises(ValueError):
        LLRConfig(a_cos=-1.0)
    data = [
        (0.68, 5.0, 0.85, 1),
        (0.60, 45.0, 0.75, 1),
        (0.55, 75.0, 0.70, 1),
        (0.15, 10.0, 0.85, 0),
        (0.22, 65.0, 0.70, 0),
    ] * 20
    params = fit_llr_logistic_4param(data)
    assert params["a_cos"] > 0.5
