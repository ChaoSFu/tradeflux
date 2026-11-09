"""
每日复盘 · 盘中跟踪：只读不写、盘中事件全是临时的、拿不到行情是「未知」不是 0、
时段按市场时间分、执行的是上一交易日锁定的预案。
"""
from datetime import date, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.market_recap import (
    LeaderActivationEvent, LeaderResponseDaily, MarketPopularCoreDaily, MarketPopularCoreRun, RecapLockedVersion,
)
from app.models.stock import Stock, StockDailySnapshot
from app.services import market_recap_live_service as L
from app.services import market_recap_store as store
from app.services.eastmoney_fetcher import SH_TZ, StockQuote

D1, D2, D3 = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
T = date(2026, 11, 9)          # 周一，盘中还不在日历里（常态）
CAL = [D1, D2, D3]
U = "chao"


@pytest.fixture(autouse=True)
def _fresh_cache():
    L.clear_cache()
    yield
    L.clear_cache()


@pytest.fixture
def cal(db):
    from app.services.trading_calendar import _write_cache
    _write_cache(db, CAL)
    return CAL


def _q(code, price, prev=10.0, high=None, low=None, d=T, hm=(10, 0), volume=1e6):
    dt = datetime(d.year, d.month, d.day, *hm, tzinfo=SH_TZ)
    return StockQuote(code=code, name="", price=price, pct_change=round((price - prev) / prev * 100, 2),
                      open=prev, high=high if high is not None else max(price, prev), low=low or min(price, prev),
                      prev_close=prev, volume=volume, amount=1e8, turnover_rate=None, trade_date=d, trade_dt=dt)


# ── 时段 ────────────────────────────────────────────────────────────────────

class TestSession:
    @pytest.mark.parametrize("hm,phase", [((9, 20), "auction"), ((9, 27), "auction_done"), ((10, 0), "trading"),
                                          ((12, 0), "lunch"), ((14, 0), "trading"), ((14, 58), "closing_auction")])
    def test_盘中各时段(self, hm, phase):
        md = datetime(2026, 11, 9, *hm)
        s = L.session_of(md, md, CAL)
        assert s["phase"] == phase
        assert s["ref_date"] == D3, "盘中执行的是上一交易日锁定的预案"
        assert s["data_date"] == T and s["target_date"] == T

    def test_收盘看市场时间不看本机时钟(self):
        local = datetime(2026, 11, 9, 15, 0, 30)
        assert L.session_of(datetime(2026, 11, 9, 14, 59, 58), local, CAL)["phase"] == "closing_auction", \
            "行情时间还没到 15:00，不算收盘"
        assert L.session_of(datetime(2026, 11, 9, 15, 0, 3), local, CAL)["phase"] == "closed"

    def test_开盘前执行昨天锁的预案_价格是昨天收盘(self):
        s = L.session_of(datetime(2026, 11, 6, 15, 0, 5), datetime(2026, 11, 9, 8, 50), CAL)
        assert s["phase"] == "pre_open"
        assert s["ref_date"] == D3 and s["data_date"] == D3
        assert s["target_date"] == T

    def test_周末是休市(self):
        s = L.session_of(datetime(2026, 11, 6, 16, 0), datetime(2026, 11, 8, 10, 0), CAL)
        assert s["phase"] == "non_trading" and s["ref_date"] == D3

    def test_开盘时间过了行情还停在上个交易日(self):
        assert L.session_of(datetime(2026, 11, 6, 16, 0), datetime(2026, 11, 9, 10, 0), CAL)["phase"] == "stale"

    def test_拿不到行情时间(self):
        s = L.session_of(None, datetime(2026, 11, 9, 10, 0), CAL)
        assert s["phase"] == "unknown"
        assert s["ref_date"] == D3, "按本机时钟推：今天是交易日，执行前一交易日的预案"

    def test_刷新间隔(self):
        assert L.poll_seconds("trading", datetime(2026, 11, 9, 10, 0)) == 30
        assert 30 <= L.poll_seconds("lunch", datetime(2026, 11, 9, 12, 58)) <= 300
        assert L.poll_seconds("closed", datetime(2026, 11, 9, 15, 30)) is None, "收盘了不用自动刷"
        assert L.poll_seconds("non_trading", datetime(2026, 11, 8, 10, 0)) is None
        assert L.poll_seconds("pre_open", datetime(2026, 11, 8, 8, 0)) is None, "周末开盘前不刷"
        assert L.poll_seconds("pre_open", datetime(2026, 11, 9, 9, 10)) == 300, "等到 09:15 竞价开始"


# ── 个股事实与临时事件 ───────────────────────────────────────────────────────

class TestLiveFact:
    def test_封板_炸板_跌停走同一套判定(self):
        assert L.live_fact("600001", _q("600001", 11.0), T, False, "a")["limit"] == "sealed"
        f = L.live_fact("600001", _q("600001", 10.5, high=11.0), T, False, "a")
        assert f["limit"] == "broken" and f["is_limit_up"] is False
        assert L.live_fact("600001", _q("600001", 9.0), T, False, "a")["limit"] == "limit_down"
        assert L.live_fact("300001", _q("300001", 11.0), T, False, "a")["limit"] is None, "创业板 20%，涨 10% 不是涨停"

    def test_没行情是未知不是0(self):
        f = L.live_fact("600001", None, T, False, "a")
        assert f["status"] == "missing" and f["pct"] is None and f["is_limit_up"] is None

    def test_旧日期的行情不当今天用(self):
        f = L.live_fact("600001", _q("600001", 11.0, d=D3), T, False, "a")
        assert f["status"] == "stale" and f["pct"] is None and f["limit"] is None

    def test_没成交不是涨跌0(self):
        f = L.live_fact("600001", _q("600001", 10.0, volume=0), T, False, "a")
        assert f["status"] == "no_trade" and f["pct"] is None


class _Prev:
    def __init__(self, lu, board, b60=0, pct=10.0):
        self.is_limit_up, self.board_count, self.board_count_60d, self.pct_change = lu, board, b60, pct


class TestLiveEvents:
    def test_连板延续_全部临时(self):
        f = L.live_fact("600001", _q("600001", 11.0), T, False, "a")
        evs = L.live_events("600001", f, _Prev(True, 2), "09:35:00")
        assert [e["event_type"] for e in evs] == ["limit_up", "streak_continue"]
        assert "2 → 3 板" in evs[1]["evidence"]
        assert all(e["provisional"] for e in evs)

    def test_断板_炸板(self):
        f = L.live_fact("600001", _q("600001", 10.5, high=11.0), T, False, "a")
        assert {e["event_type"] for e in L.live_events("600001", f, _Prev(True, 3), None)} == {"broken", "streak_break"}

    def test_再次涨停(self):
        f = L.live_fact("600001", _q("600001", 11.0), T, False, "a")
        assert "relimit" in {e["event_type"] for e in L.live_events("600001", f, _Prev(False, 0, b60=4, pct=-2), None)}

    def test_昨天没快照不判连板断板(self):
        f = L.live_fact("600001", _q("600001", 10.2), T, False, "a")
        assert L.live_events("600001", f, None, None) == []
        f = L.live_fact("600001", _q("600001", 11.0), T, False, "a")
        assert [e["event_type"] for e in L.live_events("600001", f, None, None)] == ["limit_up"]


# ── 整页 ────────────────────────────────────────────────────────────────────

def _setup(db):
    for code, name, mk in (("600001", "测试", "SH"), ("002086", "东方海洋", "SZ"), ("002001", "关联甲", "SZ"),
                           ("002002", "关联乙", "SZ"), ("002003", "没行情", "SZ")):
        db.add(Stock(code=code, name=name, market=mk))
    db.commit()
    lead = db.query(Stock).filter(Stock.code == "002086").one()
    db.add(StockDailySnapshot(stock_id=lead.id, date=D3, close_price=10.0, pct_change=10.0, is_limit_up=True,
                              board_count=2))
    db.commit()
    store.save_recap(db, U, D3, {"market_posture": "probe", "plans": [
        {"stock_code": "600001", "stock_name": "测试", "entry_trigger": "回封", "invalidation": "跌破 10.2",
         "scenario": "A", "planned_stop": 10.2}]})
    store.add_watch(db, U, "002086", D3, name="东方海洋", deep=True)
    for rel in ("002001", "002002", "002003"):
        store.upsert_relation(db, U, {"leader_code": "002086", "related_code": rel, "attribute_type": "concept",
                                      "attribute_value": "海洋经济"}, D3)
    store.lock(db, U, D3, CAL, now=datetime(2026, 11, 6, 20, 0))


def _patch_network(monkeypatch, quotes):
    monkeypatch.setattr(L, "_index_quotes", lambda: {"000001": _q("000001", 3010, prev=3000)})
    monkeypatch.setattr(L, "fetch_stock_quotes_batch", lambda cm, timeout=15: {c: quotes[c] for c, _ in cm if c in quotes})
    monkeypatch.setattr(L, "_breadth", lambda: {"up": 3000, "down": 2000, "flat": 100, "limit_up": 60,
                                                "limit_down": 3, "observed_at": "2026-11-09T10:00:00"})
    monkeypatch.setattr(L, "_limit_pools", lambda d: {
        "sealed": [{"code": "002001", "name": "关联甲", "board": 1, "first_time": "09:41:00", "last_time": "09:41:00",
                    "broken_times": 0, "industry": None, "pct": 10.0}],
        "broken": [{"code": "002086", "name": "东方海洋", "first_time": "09:35:00", "broken_times": 2,
                    "industry": None, "pct": 4.0}], "observed_at": "2026-11-09T10:00:00"})
    monkeypatch.setattr(L, "_amount", lambda: {"date": "2026-11-09", "amount": 5e11, "estimate": 1.2e12, "is_trading": True})


def _counts(db):
    return {M.__name__: db.query(M).count() for M in (RecapLockedVersion, LeaderActivationEvent, MarketPopularCoreRun,
                                                       MarketPopularCoreDaily, LeaderResponseDaily, StockDailySnapshot)}


class TestBuildLive:
    QUOTES = {"600001": _q("600001", 10.1), "002086": _q("002086", 10.4, high=11.0),
              "002001": _q("002001", 11.0), "002002": _q("002002", 10.6)}

    def test_盘中整页(self, db, cal, monkeypatch):
        _setup(db)
        _patch_network(monkeypatch, self.QUOTES)
        r = L.build_live(db, U, local_now=datetime(2026, 11, 9, 10, 0, 5))
        s = r["session"]
        assert s["phase"] == "trading" and s["live"] and s["ref_date"] == D3 and s["poll_seconds"] == 30

        plan = r["plan"]
        assert plan["source"] == "locked" and plan["pre_open"] is True
        row = plan["rows"][0]
        assert row["live"]["pct"] == 1.0
        assert row["below_stop"] is True and row["to_stop_pct"] < 0, "10.1 < 计划止损 10.2"

        lead = r["leaders"][0]
        assert lead["code"] == "002086" and lead["live"]["limit"] == "broken"
        assert lead["live"]["first_limit_time"] == "09:35:00", "首封时间来自实时炸板池"
        assert {e["event_type"] for e in lead["live"]["events"]} == {"broken", "streak_break"}
        resp = {x["code"]: x["system_response"] for x in lead["relations"]}
        assert resp == {"002001": "limit_up", "002002": "independent_strong", "002003": "insufficient"}, \
            "高标断板时关联股涨 6% 是独立走强；没行情的是数据不足，不是没响应"
        assert next(x for x in lead["relations"] if x["code"] == "002001")["live"]["first_limit_time"] == "09:41:00"

        m = r["market"]
        assert m["limit"]["sealed"] == 1 and m["limit"]["broken"] == 1 and m["limit"]["seal_rate"] == 0.5
        assert m["amount"]["estimate"] == 1.2e12
        assert all(x["meta"]["provisional"] for x in (m["breadth"], m["limit"], m["amount"]))
        assert r["quotes"]["missing"] == ["002003"]

    def test_只读_一行不写_锁定版本原样(self, db, cal, monkeypatch):
        _setup(db)
        _patch_network(monkeypatch, self.QUOTES)
        before = _counts(db)
        payload = db.query(RecapLockedVersion).one().payload
        L.build_live(db, U, local_now=datetime(2026, 11, 9, 10, 0, 5))
        db.expire_all()
        assert _counts(db) == before
        assert db.query(RecapLockedVersion).one().payload == payload

    def test_没锁定就用草稿_标明是草稿(self, db, cal, monkeypatch):
        store.save_recap(db, U, D3, {"plans": [{"stock_code": "600001", "entry_trigger": "x", "invalidation": "y"}]})
        _patch_network(monkeypatch, self.QUOTES)
        r = L.build_live(db, U, local_now=datetime(2026, 11, 9, 10, 0, 5))
        assert r["plan"]["source"] == "draft" and r["plan"]["rows"][0]["stock_code"] == "600001"

    def test_全市场一路挂了其余照常(self, db, cal, monkeypatch):
        _setup(db)
        _patch_network(monkeypatch, self.QUOTES)

        def boom(d):
            raise TimeoutError("涨停池超时")
        monkeypatch.setattr(L, "_limit_pools", boom)
        r = L.build_live(db, U, local_now=datetime(2026, 11, 9, 10, 0, 5))
        assert r["market"]["limit"]["meta"]["status"] == "missing"
        assert "seal_rate" not in r["market"]["limit"], "拿不到就没有封板率，不写 0 或 100%"
        assert r["market"]["breadth"]["up"] == 3000
        assert r["leaders"][0]["live"]["first_limit_time"] is None

    def test_开盘前不取全市场实时数_也不出盘中事件(self, db, cal, monkeypatch):
        _setup(db)
        q = {c: _q(c, v.price, high=v.high, d=D3, hm=(15, 0)) for c, v in self.QUOTES.items()}
        _patch_network(monkeypatch, q)
        monkeypatch.setattr(L, "_index_quotes", lambda: {"000001": _q("000001", 3010, prev=3000, d=D3, hm=(15, 0))})
        called = []
        monkeypatch.setattr(L, "_breadth", lambda: called.append(1))
        r = L.build_live(db, U, local_now=datetime(2026, 11, 9, 8, 50))
        assert r["session"]["phase"] == "pre_open" and not r["session"]["live"]
        assert r["session"]["ref_date"] == D3 and called == []
        assert "breadth" not in r["market"]
        assert r["leaders"][0]["live"]["events"] == [] and r["leaders"][0]["relations"][0]["system_response"] is None


class TestApi:
    def test_要登录(self, db):
        from app.database import get_db
        from app.routers import market_recap
        app = FastAPI()
        app.include_router(market_recap.router)
        app.dependency_overrides[get_db] = lambda: db
        assert TestClient(app).get("/market-recap/live").status_code in (401, 403)
