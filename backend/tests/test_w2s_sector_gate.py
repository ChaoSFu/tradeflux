"""
Sector Gate 纯函数单测：不需要真实 DB session，用轻量对象模拟 Sector/SectorDailySnapshot。
"""
from types import SimpleNamespace

from app.services.w2s_sector_gate_service import (
    compute_sector_strength_score,
    compute_sector_momentum_score,
    classify_sector_category,
    NEW_START, EXPANDING, MAIN_UPTREND, HEALTHY_DIVERGENCE, HIGH_LEVEL_WARNING, DECLINING, DEAD,
)


def _sector(**kwargs):
    defaults = dict(
        rank_5d=None, rank_10d=None, rank_20d=None, rank_lu=None, rank_board=None,
        board_height=0, limit_up_count=0, strong_stock_count=0,
        pct_change_30d=0.0, emotion_score=50.0, risk_score=0.0, amount=0.0, phase=0,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _snapshot(**kwargs):
    defaults = dict(board_height=0, limit_up_count=0, strong_stock_count=0,
                     emotion_score=50.0, risk_score=0.0, amount=0.0)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_strength_score_top_rank_scores_high():
    s = _sector(rank_5d=1, rank_10d=1, rank_20d=1, rank_lu=1, rank_board=1,
                board_height=4, limit_up_count=5, strong_stock_count=8)
    score = compute_sector_strength_score(s)
    assert score == 100.0


def test_strength_score_no_rank_no_activity_is_zero():
    s = _sector()
    assert compute_sector_strength_score(s) == 0.0


def test_momentum_score_none_without_prev():
    # 没有历史基准时返回 None（"数据积累中"），不能用假的50分冒充算出了持平
    s = _sector()
    assert compute_sector_momentum_score(s, None) is None


def test_momentum_score_rises_with_improving_metrics():
    prev = _snapshot(board_height=1, limit_up_count=1, strong_stock_count=2, emotion_score=40.0, amount=1.0e8)
    s = _sector(board_height=3, limit_up_count=4, strong_stock_count=5, emotion_score=60.0,
                amount=2.0e8, pct_change_30d=2.0)
    score = compute_sector_momentum_score(s, prev)
    assert score > 50.0


def test_momentum_score_falls_with_deteriorating_metrics():
    prev = _snapshot(board_height=4, limit_up_count=6, strong_stock_count=8, emotion_score=70.0, amount=3.0e8)
    s = _sector(board_height=1, limit_up_count=1, strong_stock_count=1, emotion_score=30.0,
                amount=1.0e8, pct_change_30d=-3.0)
    score = compute_sector_momentum_score(s, prev)
    assert score < 50.0


def test_classify_phase_direct_mappings():
    assert classify_sector_category(_sector(phase=0), None) == NEW_START
    assert classify_sector_category(_sector(phase=1), None) == NEW_START
    assert classify_sector_category(_sector(phase=2), None) == EXPANDING
    assert classify_sector_category(_sector(phase=3), None) == MAIN_UPTREND
    assert classify_sector_category(_sector(phase=5), None) == DECLINING
    assert classify_sector_category(_sector(phase=6), None) == DEAD


def test_classify_phase4_healthy_divergence_when_metrics_stable():
    prev = _snapshot(board_height=3, limit_up_count=3, risk_score=20.0, emotion_score=50.0)
    s = _sector(phase=4, board_height=3, limit_up_count=3, risk_score=20.0, emotion_score=55.0)
    assert classify_sector_category(s, prev) == HEALTHY_DIVERGENCE


def test_classify_phase4_high_level_warning_when_deteriorating():
    prev = _snapshot(board_height=6, limit_up_count=8, risk_score=10.0, emotion_score=70.0)
    s = _sector(phase=4, board_height=1, limit_up_count=1, risk_score=80.0, emotion_score=20.0)
    assert classify_sector_category(s, prev) == HIGH_LEVEL_WARNING


def test_classify_phase4_no_prev_defaults_healthy():
    assert classify_sector_category(_sector(phase=4), None) == HEALTHY_DIVERGENCE
