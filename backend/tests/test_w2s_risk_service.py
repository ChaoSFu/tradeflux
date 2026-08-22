"""
Space Gate 降级判断 + 三层止损 + 压力情景风险回报比 纯函数单测。
"""
from app.services.w2s_risk_service import (
    evaluate_space_gate, compute_stops, compute_stress_rr,
)


def test_evaluate_space_gate_sufficient():
    ok, reason = evaluate_space_gate(5.0, 2.0)
    assert ok is True
    assert reason is None


def test_evaluate_space_gate_insufficient():
    ok, reason = evaluate_space_gate(1.0, 2.0)
    assert ok is False
    assert "涨停空间" in reason


def test_evaluate_space_gate_missing_data_is_insufficient():
    ok, reason = evaluate_space_gate(None, 2.0)
    assert ok is False
    assert "缺失" in reason


def test_compute_stops_uses_pullback_low_over_ma5():
    stops = compute_stops(price=10.0, ma5=9.0, pullback_low=9.5, limit_down_pct=9.9)
    assert stops["technical_stop"] == 9.5
    assert stops["standard_stop"] == round(9.5 * 0.98, 2)
    assert stops["stress_stop"] == round(10.0 * (1 - 9.9 / 100), 2)


def test_compute_stops_falls_back_to_ma5_without_pullback_low():
    stops = compute_stops(price=10.0, ma5=9.0, pullback_low=None, limit_down_pct=9.9)
    assert stops["technical_stop"] == 9.0


def test_compute_stops_missing_price_returns_all_none():
    stops = compute_stops(price=None, ma5=9.0, pullback_low=9.5, limit_down_pct=9.9)
    assert stops == {"technical_stop": None, "standard_stop": None, "stress_stop": None}


def test_compute_stress_rr_large_room_gives_high_ratio():
    # price=10, stress_stop = 10*(1-9.9%) = 9.01, risk_pct = 9.9%
    rr = compute_stress_rr(price=10.0, stress_stop=9.01, limit_room=9.9)
    assert rr == 1.0

    rr_big_room = compute_stress_rr(price=10.0, stress_stop=9.01, limit_room=19.8)
    assert rr_big_room == 2.0


def test_compute_stress_rr_missing_inputs_returns_none():
    assert compute_stress_rr(price=None, stress_stop=9.0, limit_room=5.0) is None
    assert compute_stress_rr(price=10.0, stress_stop=None, limit_room=5.0) is None
    assert compute_stress_rr(price=10.0, stress_stop=9.0, limit_room=None) is None
