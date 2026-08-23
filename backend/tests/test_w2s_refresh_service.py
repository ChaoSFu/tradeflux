"""
w2s_refresh_service 里可脱离 DB session 独立测试的纯函数（round3 review 新增
的数据合理性校验：VWAP 必须落在 [当日最低价, 当日最高价] 区间内）。
"""
from app.services.w2s_refresh_service import _compute_vwap


def test_vwap_none_on_missing_or_zero_inputs():
    assert _compute_vwap(None, 1000.0) is None
    assert _compute_vwap(1000.0, None) is None
    assert _compute_vwap(1000.0, 0) is None
    assert _compute_vwap(0, 1000.0) is None
    assert _compute_vwap(-1000.0, 1000.0) is None


def test_vwap_plausible_value_within_low_high_band():
    # amount=1,030,000 元，volume=1000手=100000股 → vwap=10.3
    vwap = _compute_vwap(1_030_000.0, 1000.0, low=10.0, high=10.6)
    assert vwap == 10.3


def test_vwap_rejected_when_outside_low_high_band():
    # 同样的原始数字，但如果 low/high 明显不包含10.3（比如成交量单位换算错了
    # 导致算出来的vwap是物理上不可能的值），必须返回 None 而不是带着错误值下游用
    assert _compute_vwap(1_030_000.0, 1000.0, low=20.0, high=21.0) is None
    assert _compute_vwap(1_030_000.0, 1000.0, low=5.0, high=9.9) is None


def test_vwap_skips_band_check_when_low_high_missing():
    # low/high 缺失时（比如上游行情字段部分缺失）不做区间校验，只做基础校验
    assert _compute_vwap(1_030_000.0, 1000.0) == 10.3
