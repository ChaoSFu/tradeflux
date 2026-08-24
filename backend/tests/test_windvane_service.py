"""
windvane_service._fetch_updown() 回归测试（2026-08-24新增）：这个函数曾经
真实故障过——UpDownData 后来加了必填的 date 字段，但这里的构造调用点没同步
补上，导致每次调用都直接抛 pydantic ValidationError。异常被上层
sync_market_breadth() 的 try/except 吞掉记进 errors，"涨跌统计"这一步天天
静默失败，MarketBreadthDaily.up_count 停在最后一次成功写入的日期整整7天没
人发现——问题的关键是"不崩溃、只静默退化"，所以必须用真正跑一遍 _fetch_updown()
的测试守住，不能只测"模型接受这些参数"这种同义反复的弱测试。

用 httpx.MockTransport 假造 quotederivates.eastmoney.com 的响应，不发起真实
网络请求。
"""
import httpx
import pytest

from app.services.windvane_service import _fetch_updown, UPDOWN_URL


def _fake_updown_payload(errid=0):
    return {
        "errid": errid,
        "2": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],   # 上涨(不含涨停)10档
        "3": [5, 10, 15, 20, 25, 30, 35, 40, 45, 50],       # 下跌(不含跌停)10档，对称
        "4": 300,   # 平盘
        "5": 54,    # 涨停
        "6": 13,    # 跌停
        "7": 4,     # 非自然涨停
        "8": 2,     # 非自然跌停
    }


def test_fetch_updown_succeeds_with_real_market_response_shape():
    """
    回归测试核心：三市响应都正常时，_fetch_updown() 必须能成功返回 UpDownData，
    不能因为内部构造对象漏传必填字段而抛异常——这正是2026-08-24那次真实故障
    发生的方式，异常在这一层，不在网络请求那一层。
    """
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(UPDOWN_URL)
        return httpx.Response(200, json=_fake_updown_payload())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _fetch_updown(client)

    # UPDOWN_MARKETS 是沪/深/京三市，三市响应都用同一份假数据时结果是3倍求和
    assert result.up == (sum(_fake_updown_payload()["2"]) + 54) * 3
    assert result.down == (sum(_fake_updown_payload()["3"]) + 13) * 3
    assert result.limit_up == 54 * 3
    assert result.limit_down == 13 * 3
    assert result.natural_limit_up == (54 - 4) * 3
    assert result.natural_limit_down == (13 - 2) * 3


def test_fetch_updown_raises_when_all_three_markets_fail():
    """三市全部失败（比如errid非0）时必须抛异常，不能悄悄返回一个全零的假结果。"""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_fake_updown_payload(errid=-1))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError):
            _fetch_updown(client)


def test_fetch_updown_tolerates_partial_market_failure():
    """单市场失败（比如北交所盘前无数据）不阻塞整体，其余两市正常求和即可。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=_fake_updown_payload(errid=-1))
        return httpx.Response(200, json=_fake_updown_payload())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = _fetch_updown(client)

    # 3市里1个失败、2个成功，结果是2倍求和，不是0（没有整体抛异常）
    assert result.limit_up == 54 * 2
