"""
候选池发现纯函数单测：MA 计算、百分位、Prompt1/Prompt2 本地二次校验
（含 2026-08-22 补的成交额条件——用实时报价 latest_amount 核验，缺失时不因
本地时序问题制造假阴性）。
"""
from app.services.w2s_candidate_service import (
    compute_ma, compute_pct20_percentile, verify_prompt1, verify_prompt2,
)

MIN_AMOUNT = 3.0e8


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
    assert verify_prompt1(
        limit_up_days_20d=2, pct20_percentile=0.5, yesterday_pct_change=-1.5,
        latest_amount=5.0e8, min_amount=MIN_AMOUNT,
    ) is True


def test_verify_prompt1_fails_when_amount_below_threshold_and_known():
    assert verify_prompt1(
        limit_up_days_20d=2, pct20_percentile=0.5, yesterday_pct_change=-1.5,
        latest_amount=1.0e8, min_amount=MIN_AMOUNT,
    ) is False


def test_verify_prompt1_missing_amount_does_not_exclude():
    # 拿不到实时报价（比如批量拉取失败）时不能因为本地缺数据就武断排除，
    # 保留东财自己的数值过滤结果。
    assert verify_prompt1(
        limit_up_days_20d=2, pct20_percentile=0.5, yesterday_pct_change=-1.5,
        latest_amount=None, min_amount=MIN_AMOUNT,
    ) is True


def test_verify_prompt1_fails_without_yesterday_decline():
    assert verify_prompt1(
        limit_up_days_20d=2, pct20_percentile=0.5, yesterday_pct_change=1.0,
        latest_amount=5.0e8, min_amount=MIN_AMOUNT,
    ) is False


def test_verify_prompt1_fails_without_limit_up_or_top_percentile():
    assert verify_prompt1(
        limit_up_days_20d=0, pct20_percentile=0.3, yesterday_pct_change=-1.0,
        latest_amount=5.0e8, min_amount=MIN_AMOUNT,
    ) is False


def test_verify_prompt2_passes_with_all_conditions_met():
    assert verify_prompt2(
        pct20_percentile=0.9, yesterday_close=9.5, ma5=10.0, ma20=8.0,
        latest_amount=5.0e8, min_amount=MIN_AMOUNT,
    ) is True


def test_verify_prompt2_fails_when_amount_below_threshold_and_known():
    assert verify_prompt2(
        pct20_percentile=0.9, yesterday_close=9.5, ma5=10.0, ma20=8.0,
        latest_amount=1.0e8, min_amount=MIN_AMOUNT,
    ) is False


def test_verify_prompt2_missing_amount_does_not_exclude():
    assert verify_prompt2(
        pct20_percentile=0.9, yesterday_close=9.5, ma5=10.0, ma20=8.0,
        latest_amount=None, min_amount=MIN_AMOUNT,
    ) is True


def test_verify_prompt2_fails_without_ma_data():
    assert verify_prompt2(
        pct20_percentile=0.9, yesterday_close=9.5, ma5=None, ma20=None,
        latest_amount=5.0e8, min_amount=MIN_AMOUNT,
    ) is False


def test_verify_prompt2_fails_when_not_between_ma5_and_ma20():
    # 昨收要低于MA5(跌破)但仍高于MA20——价格已经跌破 MA20 时不成立
    assert verify_prompt2(
        pct20_percentile=0.9, yesterday_close=7.0, ma5=10.0, ma20=8.0,
        latest_amount=5.0e8, min_amount=MIN_AMOUNT,
    ) is False
