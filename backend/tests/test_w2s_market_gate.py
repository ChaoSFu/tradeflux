"""
Market Gate 纯函数单测：核心指数趋势加权分、风险偏好分（2026-08-22 改用
T-1冻结群体次日反馈替代两融余额变化）、四色分类。
"""
from app.services.w2s_market_gate_service import (
    compute_market_trend_score,
    compute_risk_appetite_score,
    classify_market_state,
    GREEN, YELLOW, ORANGE, RED,
)


def test_trend_score_weighted_average():
    scores = {"000001": 80.0, "399001": 60.0, "399006": 40.0}
    # 0.4*80 + 0.35*60 + 0.25*40 = 32 + 21 + 10 = 63
    assert compute_market_trend_score(scores) == 63.0


def test_trend_score_missing_core_index_returns_none():
    assert compute_market_trend_score({"000001": 80.0, "399001": 60.0}) is None


def test_risk_appetite_missing_updown_returns_none():
    assert compute_risk_appetite_score(
        up_count=None, down_count=None, limit_up_count=10, limit_down_count=2,
        market_effect_profit_strength=80.0, market_effect_loss_strength=20.0,
    ) is None


def test_risk_appetite_bullish_breadth_and_effect_scores_high():
    score = compute_risk_appetite_score(
        up_count=4000, down_count=1000, limit_up_count=80, limit_down_count=5,
        market_effect_profit_strength=90.0, market_effect_loss_strength=10.0,
    )
    assert score is not None
    assert score > 70


def test_risk_appetite_bearish_breadth_and_effect_scores_low():
    score = compute_risk_appetite_score(
        up_count=800, down_count=4200, limit_up_count=5, limit_down_count=60,
        market_effect_profit_strength=10.0, market_effect_loss_strength=90.0,
    )
    assert score is not None
    assert score < 30


def test_risk_appetite_missing_optional_fields_uses_neutral_half():
    score = compute_risk_appetite_score(
        up_count=2500, down_count=2500, limit_up_count=None, limit_down_count=None,
        market_effect_profit_strength=None, market_effect_loss_strength=None,
    )
    # up/down 各半 → 17.5分；涨跌停缺失中性 12.5；效应缺失中性 20 → 总分 50
    assert score == 50.0


def test_risk_appetite_low_confidence_halves_market_effect_weight():
    kwargs = dict(
        up_count=2500, down_count=2500, limit_up_count=50, limit_down_count=50,
        market_effect_profit_strength=60.0, market_effect_loss_strength=40.0,
    )
    normal = compute_risk_appetite_score(**kwargs, market_effect_confidence="NORMAL")
    low = compute_risk_appetite_score(**kwargs, market_effect_confidence="LOW")
    assert normal is not None and low is not None
    assert low < normal  # tracked_pool 近似广度时市场效应分量权重减半，不能跟真实数据同等话语权


def test_classify_market_state_green_needs_both_strong():
    assert classify_market_state(60, 60) == GREEN
    assert classify_market_state(60, 50) != GREEN


def test_classify_market_state_uses_weaker_side():
    assert classify_market_state(90, 36) == YELLOW
    assert classify_market_state(90, 25) == ORANGE
    assert classify_market_state(90, 10) == RED


def test_classify_market_state_missing_data_is_orange():
    assert classify_market_state(None, 80) == ORANGE
    assert classify_market_state(80, None) == ORANGE
