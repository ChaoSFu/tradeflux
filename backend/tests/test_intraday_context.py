"""
日内上下文：分钟数据按 as_of 截断，结构回放复用弱转强状态机（2026-09-11）。

核心护栏：**as_of 之后的任何成交都不能进来**。分钟 bar 按结束时刻标时间，
as_of 落在一根 bar 中间，这一根整根不用。
"""
from datetime import date, datetime, timedelta

from app.services import intraday_context_service as ic
from app.services.eastmoney_fetcher import MinuteBar, StockQuote, _parse_tencent_minute_day

D = date(2026, 9, 11)


def _bars(d, prices, start=(9, 31), step=1, ohlc=True, vol=None):
    t0 = datetime(d.year, d.month, d.day, *start)
    out = []
    for i, p in enumerate(prices):
        dt = t0 + timedelta(minutes=i * step)
        out.append(MinuteBar(dt=dt, close=p, open=p if ohlc else None, high=p + 0.05 if ohlc else None,
                             low=p - 0.05 if ohlc else None, volume=vol, amount=(vol * p if vol else None)))
    return out


def _wire(monkeypatch, *, tencent=None, sina1=None, sina5=None, quote=None):
    monkeypatch.setattr(ic, "fetch_minute_days_tencent", lambda *a, **k: tencent or {})
    monkeypatch.setattr(ic, "fetch_minute_bars_sina",
                        lambda code, market, scale=1, **k: (sina1 if scale == 1 else sina5) or [])
    monkeypatch.setattr(ic, "fetch_stock_quotes_batch", lambda pairs: {pairs[0][0]: quote} if quote else {})


def test_腾讯累计量换成每分钟增量_手换成股():
    bars = _parse_tencent_minute_day("20260911", ["0930 11.65 100 116500.00", "0931 11.04 300 339300.00"])
    assert [b.dt.strftime("%H:%M") for b in bars] == ["09:30", "09:31"]
    assert bars[1].volume == 200 * 100 and bars[1].amount == 339300 - 116500


def test_只用as_of之前结束的bar(monkeypatch):
    _wire(monkeypatch, sina1=_bars(D, [10 + i * 0.01 for i in range(60)]))
    as_of = datetime(2026, 9, 11, 9, 46, 37)
    c = ic.get_intraday_context("600354", 1, as_of, live=False)
    assert c.bars[-1].dt == datetime(2026, 9, 11, 9, 46)
    assert all(b.dt <= as_of for b in c.bars), "as_of 之后的 bar 一根都不能进来"
    assert c.price == c.bars[-1].close and c.quality == ic.EXACT


def test_5分钟bar里as_of所在那根整根不用(monkeypatch):
    _wire(monkeypatch, sina1=_bars(date(2026, 9, 10), [10.0] * 5),
          sina5=_bars(date(2026, 8, 20), [10 + i * 0.1 for i in range(20)], start=(9, 35), step=5))
    c = ic.get_intraday_context("600354", 1, datetime(2026, 8, 20, 10, 12, 30), live=False)
    assert c.bars[-1].dt == datetime(2026, 8, 20, 10, 10), "10:10~10:15 那根还没走完，里面有 as_of 之后的成交"
    assert c.resolution == "5m" and c.quality == ic.APPROX


def test_新浪1分钟那天被截断就不用(monkeypatch):
    """1023 根最早那天从中间截断——从 13:48 开始的一天，上午的高低点和均价全是错的。"""
    truncated = _bars(D, [10.0] * 30, start=(13, 48))
    tencent = {D: (9.9, _bars(D, [10.0 + i * 0.01 for i in range(30)], start=(9, 30), ohlc=False))}
    _wire(monkeypatch, sina1=truncated, tencent=tencent)
    c = ic.get_intraday_context("600354", 1, datetime(2026, 9, 11, 9, 50), live=False)
    assert c.source == "腾讯 5 日分钟" and c.quality == ic.APPROX


def test_超出回溯范围是UNKNOWN_不拿别的去填(monkeypatch):
    _wire(monkeypatch)
    c = ic.get_intraday_context("600354", 1, datetime(2026, 7, 1, 10, 0), live=False)
    assert c.quality == ic.UNKNOWN and c.price is None
    assert any("不拿收盘数据去填" in n for n in c.notes)


def test_昨收优先用腾讯给的官方昨收(monkeypatch):
    sina1 = _bars(date(2026, 9, 10), [11.40]) + _bars(D, [11.5] * 20)
    _wire(monkeypatch, sina1=sina1, tencent={D: (11.45, _bars(D, [11.5], start=(9, 30)))})
    c = ic.get_intraday_context("600354", 1, datetime(2026, 9, 11, 9, 45), live=False)
    assert c.prev_close == 11.45


def test_实时模式用行情补上此刻这一点(monkeypatch):
    now = datetime(2026, 9, 11, 10, 0, 5)
    q = StockQuote(code="600354", name="x", price=11.7, pct_change=2.2, open=11.6, high=11.8, low=11.3,
                   prev_close=11.45, volume=1e6, amount=1.15e7, trade_date=D, trade_dt=now)
    _wire(monkeypatch, sina1=_bars(D, [11.5] * 29), quote=q)
    c = ic.get_intraday_context("600354", 1, now, live=True)
    assert c.price == 11.7 and c.high == 11.8 and c.quality == ic.EXACT
    assert c.bars[-1].dt == now and c.bars[-1].close == 11.7


def test_实时行情不是今天就标STALE(monkeypatch):
    q = StockQuote(code="600354", name="x", price=11.0, prev_close=11.45, trade_date=date(2026, 9, 10))
    _wire(monkeypatch, sina1=_bars(D, [11.5] * 10), quote=q)
    c = ic.get_intraday_context("600354", 1, datetime(2026, 9, 11, 9, 45), live=True)
    assert c.quality == ic.STALE


# ── 结构回放：复用 compute_structural_transition ─────────────────────────────

def _ctx(prices):
    c = ic.IntradayContext(code="600354", as_of=datetime(2026, 9, 11, 10, 30), trade_date=D, prev_close=10.0)
    c.bars = _bars(D, prices)          # 不给量 → 没有 VWAP，修复关键位就是昨收 10.0
    return c


def test_修复_H1_回踩_再突破_CONFIRMED():
    st = ic.replay_structure(_ctx([9.8, 9.9, 10.1, 10.3, 10.5, 10.3, 10.25, 10.6]), 1.5)
    assert st["status"] == "CONFIRMED"
    assert (st["h1"], st["l1"]) == (10.5, 10.25) and st["breakout_at"]


def test_回踩了还没再突破_PARTIAL():
    st = ic.replay_structure(_ctx([9.8, 10.1, 10.5, 10.3, 10.35]), 1.5)
    assert st["status"] == "PARTIAL" and st["state"] == "PULLBACK" and st["breakout_at"] is None


def test_跌破修复关键位_NOT_CONFIRMED():
    st = ic.replay_structure(_ctx([10.1, 10.5, 9.9]), 1.5)
    assert st["status"] == "NOT_CONFIRMED" and st["state"] == "FAILED" and st["failed_at"]


def test_没有分钟数据_UNKNOWN():
    c = ic.IntradayContext(code="600354", as_of=datetime(2026, 9, 11, 10, 0), trade_date=D, prev_close=10.0)
    assert ic.replay_structure(c, 1.5)["status"] == "UNKNOWN"


# ── 停牌：腾讯照样给一排「价格 = 昨收、没有量」的分钟行 ───────────────────────────

from datetime import date as _d, datetime as _dt  # noqa: E402

from app.services import intraday_context_service as _ic  # noqa: E402
from app.services.eastmoney_fetcher import MinuteBar as _Bar  # noqa: E402


def _tencent_only(monkeypatch, prev, bars):
    monkeypatch.setattr(_ic, "fetch_minute_days_tencent", lambda code, market, **k: {_d(2026, 9, 11): (prev, bars)})
    monkeypatch.setattr(_ic, "fetch_minute_bars_sina", lambda *a, **k: [])


def test_停牌_整段没成交按拿不到处理_不当成平盘(monkeypatch):
    _tencent_only(monkeypatch, 18.67, [_Bar(dt=_dt(2026, 9, 11, 9, 30 + i), close=18.67) for i in range(17)])
    c = _ic.get_intraday_context("605577", 1, _dt(2026, 9, 11, 9, 46, 37), live=False)
    assert c.quality == _ic.UNKNOWN and c.price is None and c.pct is None
    assert any("停牌" in n for n in c.notes)


def test_一字板有成交_不是停牌(monkeypatch):
    _tencent_only(monkeypatch, 18.67, [_Bar(dt=_dt(2026, 9, 11, 9, 30 + i), close=20.54, volume=1000.0, amount=20540.0)
                                       for i in range(17)])
    c = _ic.get_intraday_context("605577", 1, _dt(2026, 9, 11, 9, 46, 37), live=False)
    assert c.price == 20.54 and c.quality != _ic.UNKNOWN


# ── 实时行情的时间戳带时区（2026-09-11 本地联调：LIVE 模式全部 TypeError） ─────────

def test_实时行情时间戳带时区_也能叠到分钟bar上(monkeypatch):
    from app.services.eastmoney_fetcher import SH_TZ, StockQuote
    bars = [_Bar(dt=_dt(2026, 9, 11, 9, 30 + i), close=11.4 + i * 0.01, volume=1e5, amount=1.14e6) for i in range(17)]
    _tencent_only(monkeypatch, 11.45, bars)
    q = StockQuote(code="600354", name="x", price=11.7, open=11.6, high=11.8, low=11.3, prev_close=11.45,
                   volume=1e6, amount=1.15e7, trade_date=_d(2026, 9, 11),
                   trade_dt=_dt(2026, 9, 11, 9, 47, 5, tzinfo=SH_TZ))
    monkeypatch.setattr(_ic, "fetch_stock_quotes_batch", lambda items, **k: {"600354": q})
    c = _ic.get_intraday_context("600354", 1, _dt(2026, 9, 11, 9, 47, 10), live=True)
    assert c.observed_at == _dt(2026, 9, 11, 9, 47, 5) and c.observed_at.tzinfo is None
    assert c.bars[-1].close == 11.7 and c.quality == _ic.EXACT


# ── 取数路径（2026-09-11 提速）：新浪 1 分钟只在历史复盘要精确开高低时用，而且限时 ─────

import pytest as _pytest  # noqa: E402


def _no_sina1(monkeypatch):
    def sina(code, market, scale=1, **k):
        if scale == 1:
            raise AssertionError("这条路径不该拉新浪 1 分钟")
        return []
    monkeypatch.setattr(_ic, "fetch_minute_bars_sina", sina)


def _tencent_day(monkeypatch, prev=11.45):
    bars = [_Bar(dt=_dt(2026, 9, 11, 9, 30 + i), close=11.4 + i * 0.01, volume=1e5, amount=1.14e6) for i in range(17)]
    monkeypatch.setattr(_ic, "fetch_minute_days_tencent", lambda code, market, **k: {_d(2026, 9, 11): (prev, bars)})


def test_实时模式不拉新浪1分钟_开高低来自行情(monkeypatch):
    from app.services.eastmoney_fetcher import SH_TZ, StockQuote
    _tencent_day(monkeypatch)
    _no_sina1(monkeypatch)
    q = StockQuote(code="600354", name="x", price=11.7, open=11.6, high=11.8, low=11.3, prev_close=11.45,
                   volume=1e6, amount=1.15e7, trade_date=_d(2026, 9, 11),
                   trade_dt=_dt(2026, 9, 11, 9, 47, 5, tzinfo=SH_TZ))
    monkeypatch.setattr(_ic, "fetch_stock_quotes_batch", lambda items, **k: {"600354": q})
    c = _ic.get_intraday_context("600354", 1, _dt(2026, 9, 11, 9, 47, 10), live=True)
    assert c.quality == _ic.EXACT and (c.high, c.low) == (11.8, 11.3)
    assert not any("最高/最低按每分钟" in n for n in c.notes)


def test_只要涨跌幅的票_历史模式不拉新浪1分钟(monkeypatch):
    _tencent_day(monkeypatch)
    _no_sina1(monkeypatch)
    c = _ic.get_intraday_context("000001", 1, _dt(2026, 9, 11, 9, 46, 37), live=False, is_index=True, detail=False)
    assert c.price == _pytest.approx(11.56) and c.quality == _ic.EXACT


def test_新浪太慢就不等_用腾讯的分钟价(monkeypatch):
    import time as _time
    _tencent_day(monkeypatch)

    def slow(code, market, scale=1, **k):
        _time.sleep(1.0)
        return []

    monkeypatch.setattr(_ic, "fetch_minute_bars_sina", slow)
    monkeypatch.setattr(_ic, "SINA_1M_BUDGET_S", 0.1)
    t = _time.perf_counter()
    c = _ic.get_intraday_context("600354", 1, _dt(2026, 9, 11, 9, 46, 37), live=False)
    assert _time.perf_counter() - t < 0.8
    assert c.source == "腾讯 5 日分钟" and c.quality == _ic.APPROX
    assert any("没回" in n for n in c.notes)


def test_实时批量行情_指数和高标一次取完(monkeypatch):
    from app.services.eastmoney_fetcher import StockQuote
    D = _d(2026, 9, 11)
    qs = {"000001": StockQuote(code="000001", name="", price=3888.0, prev_close=3935.0, trade_date=D),
          "600865": StockQuote(code="600865", name="", price=12.0, prev_close=13.19, volume=5e6, trade_date=D),
          "605577": StockQuote(code="605577", name="", price=18.67, prev_close=18.67, volume=0, trade_date=D),
          "002403": StockQuote(code="002403", name="", price=10.5, prev_close=10.6, volume=1e6,
                               trade_date=_d(2026, 9, 10))}
    calls = []
    monkeypatch.setattr(_ic, "fetch_stock_quotes_batch", lambda items, **k: calls.append(list(items)) or qs)
    out = _ic.get_quote_contexts([("000001", 1, True), ("600865", 1, False), ("605577", 1, False),
                                  ("002403", 0, False)], _dt(2026, 9, 11, 10, 0))
    assert len(calls) == 1
    assert out["000001"].pct == _pytest.approx(-1.19, abs=0.01) and out["000001"].quality == _ic.EXACT
    assert out["605577"].pct is None, "停牌不算平盘"
    assert out["002403"].pct is None and out["002403"].quality == _ic.STALE


def test_当天还没有分钟数据_不去白等新浪5分钟(monkeypatch):
    bars = [_Bar(dt=_dt(2026, 9, 11, 9, 30 + i), close=11.4, volume=1e5) for i in range(5)]
    monkeypatch.setattr(_ic, "fetch_minute_days_tencent", lambda code, market, **k: {_d(2026, 9, 11): (11.45, bars)})
    monkeypatch.setattr(_ic, "fetch_minute_bars_sina",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该请求新浪")))
    c = _ic.get_intraday_context("600354", 1, _dt(2026, 9, 12, 9, 20), live=False)   # 周六 / 盘前
    assert c.quality == _ic.UNKNOWN and any("还没有分钟数据" in n for n in c.notes)
    assert "新浪5m" not in c.timings and "新浪1m" not in c.timings
