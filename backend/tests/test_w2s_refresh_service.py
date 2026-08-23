"""
w2s_refresh_service 里可脱离 DB session 独立测试的纯函数（round3 review 新增
的数据合理性校验：VWAP 必须落在 [当日最低价, 当日最高价] 区间内）。

2026-08-23修复真实bug后更新：_compute_vwap 的 volume 参数现在统一约定为
"股"（不是"手"）——各数据源（东财/腾讯的"手"、新浪的"股"）的单位换算已经
在各自的解析函数里做完，_compute_vwap 内部不再做*100，这里的用例数值
也相应改成"股"为单位，不再是历史上的"手"。
"""
from app.services.w2s_refresh_service import _compute_vwap


def test_vwap_none_on_missing_or_zero_inputs():
    assert _compute_vwap(None, 100000.0) is None
    assert _compute_vwap(1000.0, None) is None
    assert _compute_vwap(1000.0, 0) is None
    assert _compute_vwap(0, 100000.0) is None
    assert _compute_vwap(-1000.0, 100000.0) is None


def test_vwap_plausible_value_within_low_high_band():
    # amount=1,030,000 元，volume=100,000股（不是"手"，是_fetch_quotes_*已经
    # 换算过的股数）→ vwap=10.3
    vwap = _compute_vwap(1_030_000.0, 100_000.0, low=10.0, high=10.6)
    assert vwap == 10.3


def test_vwap_rejected_when_outside_low_high_band():
    # 同样的原始数字，但如果 low/high 明显不包含10.3（比如上游数据源单位没有
    # 统一换算成股、这里又拿到了个错误的volume），必须返回 None 而不是带着
    # 错误值下游用——这正是2026-08-23那次真实bug（新浪volume本来就是"股"，
    # 旧代码却又*100，算出来的vwap缩小100倍，被这个校验正确拦下退化成MA5）
    # 想要保护的场景。
    assert _compute_vwap(1_030_000.0, 100_000.0, low=20.0, high=21.0) is None
    assert _compute_vwap(1_030_000.0, 100_000.0, low=5.0, high=9.9) is None


def test_vwap_skips_band_check_when_low_high_missing():
    # low/high 缺失时（比如上游行情字段部分缺失）不做区间校验，只做基础校验
    assert _compute_vwap(1_030_000.0, 100_000.0) == 10.3


def test_vwap_no_longer_double_converts_lots_to_shares():
    # 2026-08-23修复的真实bug回归测试：这个函数以前会把传入的volume再*100，
    # 隐含假设调用方永远传"手"。现在调用方（各数据源解析函数）已经统一在
    # 自己的解析层把"手"换算成"股"再传进来，这里如果还偷偷*100，本用例会
    # 因为100手=100000股按新逻辑被误当成10000000再除，vwap会缩小100倍到
    # 0.103，明显不在[10.0,10.6]区间内会被拒绝返回None——用这个反向验证
    # 确认*100真的已经被去掉。
    vwap = _compute_vwap(1_030_000.0, 100_000.0, low=10.0, high=10.6)
    assert vwap == 10.3  # 不是 0.103
