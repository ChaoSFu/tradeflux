"""
Market Gate 纯函数单测：核心指数趋势加权分、风险偏好分、四色分类。
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
        margin_balance_chg_pct=1.0,
    ) is None


def test_risk_appetite_bullish_breadth_scores_high():
    score = compute_risk_appetite_score(
        up_count=4000, down_count=1000, limit_up_count=80, limit_down_count=5,
        margin_balance_chg_pct=4.0,
    )
    assert score is not None
    assert score > 70


def test_risk_appetite_bearish_breadth_scores_low():
    score = compute_risk_appetite_score(
        up_count=800, down_count=4200, limit_up_count=5, limit_down_count=60,
        margin_balance_chg_pct=-4.0,
    )
    assert score is not None
    assert score < 30


def test_risk_appetite_missing_optional_fields_uses_neutral_half():
    score = compute_risk_appetite_score(
        up_count=2500, down_count=2500, limit_up_count=None, limit_down_count=None,
        margin_balance_chg_pct=None,
    )
    # up/down 各半 → 20分；缺失的两项各给中性一半 15+15 = 30；总分 50
    assert score == 50.0


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
