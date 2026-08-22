"""
候选池发现纯函数单测：MA 计算、百分位、Prompt1/Prompt2 本地二次校验。
成交额条件不在本地复核范围内（见 w2s_candidate_service.py 模块头注释），
verify_prompt1/verify_prompt2 因此不吃 amount 相关参数。
"""
from app.services.w2s_candidate_service import (
    compute_ma, compute_pct20_percentile, verify_prompt1, verify_prompt2,
)


def test_compute_ma_insufficient_data_returns_none():
    assert compute_ma([1.0, 2.0], 5) is None


def test_compute_ma_exact_window():
    assert compute_ma([1.0, 2.0, 3.0, 4.0, 5.0], 5) == 3.0


def test_compute_ma_uses_most_recent_window():
    assert compute_ma([10.0, 1.0, 2.0, 3.0], 3) == 2.0


def test_compute_pct20_percentile_bounds():
    assert compute_pct20_percentile(5.0, []) == 0.0
    assert compute_pct20_percentile(100.0, [10.0, 20.0, 30.0]) == 1.0
    assert compute_pct20_percentile(0.0, [10.0, 20.0, 30.0]) == 0.0


def test_verify_prompt1_passes_with_all_conditions_met():
    assert verify_prompt1(limit_up_days_20d=2, pct20_percentile=0.5, yesterday_pct_change=-1.5) is True


def test_verify_prompt1_fails_without_yesterday_decline():
    assert verify_prompt1(limit_up_days_20d=2, pct20_percentile=0.5, yesterday_pct_change=1.0) is False


def test_verify_prompt1_fails_without_limit_up_or_top_percentile():
    assert verify_prompt1(limit_up_days_20d=0, pct20_percentile=0.3, yesterday_pct_change=-1.0) is False


def test_verify_prompt1_top_percentile_alone_is_sufficient():
    assert verify_prompt1(limit_up_days_20d=0, pct20_percentile=0.85, yesterday_pct_change=-1.0) is True


def test_verify_prompt2_passes_with_all_conditions_met():
    assert verify_prompt2(pct20_percentile=0.9, yesterday_close=9.5, ma5=10.0, ma20=8.0) is True


def test_verify_prompt2_fails_without_ma_data():
    assert verify_prompt2(pct20_percentile=0.9, yesterday_close=9.5, ma5=None, ma20=None) is False


def test_verify_prompt2_fails_when_not_between_ma5_and_ma20():
    # 昨收要低于MA5(跌破)但仍高于MA20——价格已经跌破 MA20 时不成立
    assert verify_prompt2(pct20_percentile=0.9, yesterday_close=7.0, ma5=10.0, ma20=8.0) is False


def test_verify_prompt2_fails_below_top_percentile():
    assert verify_prompt2(pct20_percentile=0.5, yesterday_close=9.5, ma5=10.0, ma20=8.0) is False
