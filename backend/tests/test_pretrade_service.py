"""
买入检查的事实层与接口（2026-09-11）：as_of 之后的东西一样都不能进来。
"""
import logging
from datetime import date, datetime, time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth import require_auth
from app.database import get_db
from app.models.leader_cycle import LeaderCycleSnapshot
from app.models.limit_up_detail import BrokenBoardDailyDetail, LimitUpDailyDetail
from app.models.pre_trade_check import PreTradeCheck
from app.models.stock import Stock, StockDailySnapshot
from app.models.trade_journal import TradeJournal
from app.routers import pre_trade_check as router_mod
from app.services import intraday_context_service as ic
from app.services import pre_trade_check_service as svc
from app.services.eastmoney_fetcher import MinuteBar

AS_OF = datetime(2026, 9, 11, 9, 46, 37)
PREV = date(2026, 9, 10)
CAL = [date(2026, 9, d) for d in (1, 2, 3, 4, 7, 8, 9, 10, 11)]


def _t(db, action, dt, code="600354", pos=10.0, pnl=None, owner="me"):
    db.add(TradeJournal(owner=owner, stock_code=code, stock_name=code, action=action,
                        trade_time=dt, price=11.0, position_pct=pos, realized_pnl=pnl))


# ── 交易纪律：只数 as_of 之前的记录 ─────────────────────────────────────────────

def test_今天的笔数只数as_of之前_被复盘的这一笔本身不算(db):
    _t(db, "买入", datetime(2026, 9, 11, 9, 40), code="000001")
    _t(db, "买入", AS_OF)                                          # 被复盘的这一笔
    _t(db, "买入", datetime(2026, 9, 11, 10, 30), code="000002")   # as_of 之后
    _t(db, "买入", datetime(2026, 9, 11, 9, 35), code="000003", owner="别人")
    db.flush()
    dc = svc._discipline_block(db, "me", "600354", AS_OF, CAL)
    assert [b["stock_code"] for b in dc["today_buys"]] == ["000001"]


def test_5个交易日窗口按交易日历算(db):
    _t(db, "卖出", datetime(2026, 9, 4, 10, 0), pnl=-100)    # 第 6 个交易日前：不算
    _t(db, "卖出", datetime(2026, 9, 8, 10, 0), pnl=500)     # 窗口内
    db.flush()
    dc = svc._discipline_block(db, "me", "600354", AS_OF, CAL)
    assert [r["trade_time"][:10] for r in dc["recent_same_stock"]] == ["2026-09-08"]
    assert dc["last_same_stock_pnl"] == 500


def test_连续亏损从最近一笔往回数(db):
    _t(db, "卖出", datetime(2026, 9, 1, 10, 0), code="A00001", pnl=300)
    _t(db, "卖出", datetime(2026, 9, 3, 10, 0), code="A00002", pnl=-200)
    _t(db, "卖出", datetime(2026, 9, 9, 10, 0), code="A00003", pnl=-100)
    _t(db, "卖出", datetime(2026, 9, 11, 11, 0), code="A00004", pnl=-50)    # as_of 之后，不算
    db.flush()
    assert svc._discipline_block(db, "me", "600354", AS_OF, CAL)["consecutive_losses"] == 2


def test_是否已持有按净仓位(db):
    _t(db, "买入", datetime(2026, 9, 8, 10, 0), pos=20)
    _t(db, "卖出", datetime(2026, 9, 9, 10, 0), pos=10, pnl=100)
    db.flush()
    h = svc._discipline_block(db, "me", "600354", AS_OF, CAL)["holding"]
    assert h["holding"] is True and h["net_position_pct"] == 10.0


def test_未登录就说没对照(db):
    assert svc._discipline_block(db, None, "600354", AS_OF, CAL)["available"] is False


# ── 生命周期只读前一交易日及以前 ──────────────────────────────────────────────

def _stock(db, code="600354"):
    st = Stock(code=code, name="敦煌种业", market="SH")
    db.add(st); db.flush()
    return st


def test_生命周期和RS不读as_of当天的行(db):
    st = _stock(db)
    db.add(LeaderCycleSnapshot(stock_id=st.id, stock_code=st.code, date=PREV, rs_market_20=5.0))
    db.add(LeaderCycleSnapshot(stock_id=st.id, stock_code=st.code, date=AS_OF.date(), rs_market_20=99.0))
    db.flush()
    sctx = ic.IntradayContext(code=st.code, as_of=AS_OF, trade_date=AS_OF.date(), prev_close=11.45, price=11.53)
    ld = svc._leader_block(db, st, PREV, CAL, {}, sctx, {"limit_up_price": 12.6, "is_st": False})
    assert ld["rs_market_20"] == 5.0, "as_of 当天那行是盘中/收盘后才有的，不能进来"
    assert ld["lifecycle"]["date"] == PREV.isoformat()


# ── 截至 as_of 触及涨停的家数：从首封时间还原 ──────────────────────────────────

def _detail(db, model, code, ft, refreshed):
    st = _stock(db, code)
    db.add(model(stock_id=st.id, stock_code=code, trade_date=AS_OF.date(), first_limit_time=ft,
                 board_count=2, refreshed_at=refreshed))


def test_触板家数只数首封在as_of之前的(db):
    after_close = datetime(2026, 9, 11, 15, 35)
    _detail(db, LimitUpDailyDetail, "600001", time(9, 40), after_close)
    _detail(db, LimitUpDailyDetail, "600002", time(10, 30), after_close)     # as_of 之后才封
    _detail(db, BrokenBoardDailyDetail, "600003", time(9, 35), after_close)  # 触过后开板
    db.flush()
    lt = svc._limit_touch_from_db(db, AS_OF.date(), AS_OF)
    assert lt["touched"] == 2 and lt["codes"] == ["600001", "600003"]
    assert lt["meta"]["quality"] == "EXACT"


def test_明细刷新早于as_of_标STALE(db):
    _detail(db, LimitUpDailyDetail, "600001", time(9, 40), datetime(2026, 9, 11, 9, 42))
    db.flush()
    assert svc._limit_touch_from_db(db, AS_OF.date(), AS_OF)["meta"]["quality"] == "STALE"


# ── 接口：保存、历史、后续走势不改判定 ───────────────────────────────────────

def _fake_ctx(code, as_of, price=11.53):
    c = ic.IntradayContext(code=code, as_of=as_of, trade_date=as_of.date(), prev_close=11.45, price=price,
                           open=11.65, high=11.8, low=10.5, quality=ic.EXACT, source="fake", resolution="1m")
    c.bars = [MinuteBar(dt=datetime(2026, 9, 11, 9, 45), close=11.49), MinuteBar(dt=datetime(2026, 9, 11, 9, 46), close=price)]
    c.observed_at = c.bars[-1].dt
    return c


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(svc, "get_trading_days", lambda *a, **k: CAL)
    monkeypatch.setattr(svc, "get_intraday_contexts",
                        lambda items, as_of, live, **k: {c: _fake_ctx(c, as_of) for c, _, _ in items})
    monkeypatch.setattr(svc, "get_intraday_context", lambda code, market, as_of, live, **k: _fake_ctx(code, as_of))
    app = FastAPI()
    app.include_router(router_mod.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[require_auth] = lambda: "me"
    return TestClient(app)


def _seed(db):
    st = _stock(db)
    db.add(StockDailySnapshot(stock_id=st.id, date=PREV, close_price=11.45, board_count=0, is_settled=True))
    db.commit()     # 事实层遇到测试库建不了的表会 rollback，种子数据要先提交
    return st


BODY = {"stock_code": "600354", "as_of": "2026-09-11T09:46:37", "intended_price": 11.53,
        "position_pct": 10, "planned_stop": 11.0,
        "manual_answers": {**{f"q{i}": False for i in range(1, 9)}, "q9": True}}


def test_检查结果连同规则版本一起存下来(client, db):
    _seed(db)
    r = client.post("/pre-trade-check/evaluate", json=BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "HISTORICAL" and body["decision"]["verdict"] in ("READY", "WAIT", "BLOCKED")
    row = db.query(PreTradeCheck).one()
    assert row.rule_version == "pretrade_v1" and row.verdict == body["decision"]["verdict"]
    assert row.facts_json["as_of"] == "2026-09-11T09:46:37"
    hist = client.get("/pre-trade-check/history").json()
    assert [h["id"] for h in hist] == [row.id]


def test_as_of在未来就拒绝(client, db):
    _seed(db)
    r = client.post("/pre-trade-check/evaluate", json={**BODY, "as_of": "2099-01-01T10:00:00"})
    assert r.status_code == 422


def test_后续走势单独算_不改当时的判定(client, db):
    _seed(db)
    first = client.post("/pre-trade-check/evaluate", json=BODY).json()
    out = client.get(f"/pre-trade-check/{first['id']}/outcome")
    assert out.status_code == 200, out.text
    row = db.query(PreTradeCheck).one()
    assert row.outcome_json is not None
    assert row.verdict == first["decision"]["verdict"], "揭晓后续走势绝不能回写判定"
    assert client.get(f"/pre-trade-check/{first['id']}").json()["decision"]["verdict"] == first["decision"]["verdict"]


def test_别人的检查记录看不到(client, db):
    _seed(db)
    first = client.post("/pre-trade-check/evaluate", json=BODY).json()
    db.query(PreTradeCheck).update({"owner": "别人"}); db.commit()
    assert client.get(f"/pre-trade-check/{first['id']}").status_code == 404


# ── 交易日历三态：盘中当天不在日历里 ≠ 非交易日 ─────────────────────────────────

@pytest.mark.parametrize("cal, d, itd, behind", [
    ([date(2026, 9, 10)], date(2026, 9, 11), None, False),     # 盘中：日历只到昨天，是常态
    ([date(2026, 9, 11)], date(2026, 9, 14), None, False),     # 周五 → 周一，中间只有周末
    ([date(2026, 9, 7)], date(2026, 9, 11), None, True),       # 中间夹着工作日：日历落后了
    ([date(2026, 9, 11)], date(2026, 9, 12), False, False),    # 周六
    ([date(2026, 9, 10), date(2026, 9, 11)], date(2026, 9, 11), True, False),
    ([date(2026, 9, 10), date(2026, 9, 14)], date(2026, 9, 11), False, False),  # 日历覆盖到了但没有它：休市
    (None, date(2026, 9, 11), None, None),
])
def test_日历状态(cal, d, itd, behind):
    st = svc._calendar_status(cal, d)
    assert st["is_trading_day"] is itd and st["behind"] is behind


def test_日历只要求覆盖到前一个工作日():
    assert svc._last_weekday_before(date(2026, 9, 14)) == date(2026, 9, 11)
    assert svc._last_weekday_before(date(2026, 9, 11)) == date(2026, 9, 10)


# ── 板块涨停数只有一个口径 ────────────────────────────────────────────────────

def test_板块涨停数只有一个口径_成分股日快照(db):
    from app.models.sector import Sector, SectorDailySnapshot, StockSectorRelation
    sec = Sector(code="BK001", name="乡村振兴")
    db.add(sec); db.flush()
    me = _stock(db)
    me.primary_sector_id = sec.id
    others = [_stock(db, f"60000{i}") for i in range(1, 4)]
    for st in [me, *others]:
        db.add(StockSectorRelation(stock_id=st.id, sector_id=sec.id))
    db.add(SectorDailySnapshot(sector_id=sec.id, date=PREV, limit_up_count=99))   # 另一套口径，不许出现
    lu = {date(2026, 9, 8): 1, date(2026, 9, 9): 0, PREV: 2}
    for d, n in lu.items():
        for k, st in enumerate(others):
            db.add(StockDailySnapshot(stock_id=st.id, date=d, is_limit_up=k < n, board_count=k + 1 if k < n else 0))
    db.flush()
    sctx = ic.IntradayContext(code=me.code, as_of=AS_OF, trade_date=AS_OF.date(), prev_close=11.45, price=11.53)
    out = svc._sector_block(db, False, AS_OF, AS_OF.date(), PREV, me, None, sctx, [], CAL)
    assert out["options"][0]["prev_limit_up"] == 2
    assert out["prev_day"]["limit_up_count"] == 2
    assert out["continuation"]["prev_limit_ups"] == 2
    trend = {t["date"]: t["limit_up_count"] for t in out["trend"]}
    assert (trend["2026-09-08"], trend["2026-09-09"], trend["2026-09-10"]) == (1, 0, 2)
    assert trend["2026-09-07"] is None, "那天库里没有日快照：是不知道，不是 0"


# ── 实时模式取数（2026-09-11 提速）：一起发、全市场的数半分钟内不重拉 ───────────────

def test_全市场实时数据半分钟内不重拉_取不到的不缓存():
    svc._live_cache.clear()
    n = {"ok": 0, "bad": 0}

    def ok():
        n["ok"] += 1
        return {"up": 1, "meta": {"quality": ic.EXACT}}

    def bad():
        n["bad"] += 1
        return {"meta": {"quality": ic.UNKNOWN}}

    svc._cached_live(("t", 1), ok)["up"] = 99
    assert svc._cached_live(("t", 1), ok)["up"] == 1 and n["ok"] == 1, "给的是副本，调用方改了不串"
    svc._cached_live(("b", 1), bad)
    svc._cached_live(("b", 1), bad)
    assert n["bad"] == 2, "取不到的下次还要再试"
    svc._live_cache.clear()


def test_实时模式_指数和高标走一次批量行情_不逐只拉分钟(db, monkeypatch, caplog):
    svc._live_cache.clear()
    _seed(db)
    now = datetime(2026, 9, 11, 10, 0, 0)
    monkeypatch.setattr(svc, "now_sh", lambda: now)
    monkeypatch.setattr(svc, "get_trading_days", lambda *a, **k: CAL)
    monkeypatch.setattr(svc, "get_intraday_context", lambda code, market, as_of, live, **k: _fake_ctx(code, as_of))
    seen = {}

    def quotes_ctx(items, as_of):
        seen["others"] = [c for c, _, _ in items]
        return {c: _fake_ctx(c, as_of) for c, _, _ in items}

    monkeypatch.setattr(svc, "get_quote_contexts", quotes_ctx)
    monkeypatch.setattr(svc, "get_intraday_contexts", lambda *a, **k: pytest.fail("实时模式不该逐只拉分钟数据"))
    monkeypatch.setattr(svc, "_limit_touch_live", lambda d: {"touched": 30, "codes": [], "meta": {"quality": ic.EXACT}})
    monkeypatch.setattr(svc, "_breadth_live", lambda: {"up": 3000, "down": 2000, "meta": {"quality": ic.EXACT}})
    monkeypatch.setattr(svc, "_cohort_quotes", lambda codes, prev_d, d: {"median_pct": 1.0, "meta": {"quality": ic.EXACT}})
    caplog.set_level(logging.INFO, logger="tradeflux.pretrade")
    ctx = svc.build_context(db, "me", "600354", None)
    assert ctx["mode"] == "LIVE" and ctx["as_of"] == "2026-09-11T10:00:00"
    assert seen["others"][:3] == ["000001", "399001", "399006"]
    assert ctx["market"]["breadth"]["up"] == 3000 and ctx["market"]["limit_touch"]["touched"] == 30
    line = [r.getMessage() for r in caplog.records if r.name == "tradeflux.pretrade"][-1]
    assert line.startswith("context LIVE 600354") and "涨跌分布" in line and " 总" in line
    assert "总" in ctx["timings"] and "个股" in ctx["timings"], "耗时也进事实快照"
    svc._live_cache.clear()


# ── 实战日志 ─────────────────────────────────────────────────────────────────

def test_实战日志写到独立文件_重复调用不重复挂(tmp_path):
    h = svc.setup_file_log(tmp_path)
    try:
        assert svc.setup_file_log(tmp_path) is h
        svc.logger.warning("hello 买入检查")
        h.flush()
        assert "hello 买入检查" in (tmp_path / svc.LOG_FILE).read_text(encoding="utf-8")
    finally:
        svc.logger.removeHandler(h)
        h.close()


def test_慢了或失败了记WARNING_缓存命中单独标(caplog):
    caplog.set_level(logging.INFO, logger="tradeflux.pretrade")
    ctx = {"mode": "LIVE", "as_of": "2026-09-12T10:00:00", "stock": {"code": "600354"},
           "timings": {"总": 9.1, "个股": 0.9, "涨停池": 0.0, "涨跌分布": 8.8,
                       "个股分钟源": {"腾讯": 0.4, "行情": 0.3}, "缓存命中": ["涨停池"]},
           "data_quality": [{"module": "日内（个股）", "quality": "EXACT", "notes": ["新浪 1 分钟 5 秒没回，不等了"]},
                            {"module": "板块", "quality": "UNKNOWN", "notes": []}]}
    svc._log_context("context", ctx)
    r = caplog.records[-1]
    m = r.getMessage()
    assert r.levelno == logging.WARNING
    assert "涨停池=缓存" in m and "慢[涨跌分布8.8s]" in m and "没回" in m and "未知[板块]" in m
    assert "个股源 腾讯0.4 行情0.3" in m


# ── 2026-09-12 生产日志：周六实时检查被涨停池 ConnectTimeout 拖了 20 秒 ─────────────

def _live_setup(db, monkeypatch, now):
    svc._live_cache.clear()
    _seed(db)
    monkeypatch.setattr(svc, "now_sh", lambda: now)
    monkeypatch.setattr(svc, "get_trading_days", lambda *a, **k: CAL)
    monkeypatch.setattr(svc, "get_intraday_context", lambda code, market, as_of, live, **k: _fake_ctx(code, as_of))
    monkeypatch.setattr(svc, "get_quote_contexts", lambda items, as_of: {c: _fake_ctx(c, as_of) for c, _, _ in items})


def test_非交易日做实时检查_不去取全市场实时数(db, monkeypatch):
    _live_setup(db, monkeypatch, datetime(2026, 9, 12, 12, 27, 49))       # 周六
    for name in ("_limit_touch_live", "_breadth_live", "_cohort_quotes"):
        monkeypatch.setattr(svc, name, lambda *a, **k: pytest.fail("非交易日不该取"))
    ctx = svc.build_context(db, "me", "600354", None)
    assert ctx["is_trading_day"] is False
    assert "不是交易日" in ctx["market"]["limit_touch"]["meta"]["notes"][0]
    svc._live_cache.clear()


def test_全市场某一路太慢_过时不候_晚到的结果照样进缓存(db, monkeypatch, caplog):
    import time as _t
    _live_setup(db, monkeypatch, datetime(2026, 9, 11, 10, 0, 0))
    monkeypatch.setattr(svc, "LIVE_SOURCE_BUDGET_S", 0.2)

    def slow_pool(d):
        _t.sleep(0.8)
        return {"touched": 30, "codes": [], "meta": {"quality": ic.EXACT}}

    monkeypatch.setattr(svc, "_limit_touch_live", slow_pool)
    monkeypatch.setattr(svc, "_breadth_live", lambda: {"up": 1, "meta": {"quality": ic.EXACT}})
    monkeypatch.setattr(svc, "_cohort_quotes", lambda codes, prev_d, d: {"meta": {"quality": ic.EXACT}})
    caplog.set_level(logging.INFO, logger="tradeflux.pretrade")
    t = _t.perf_counter()
    ctx = svc.build_context(db, "me", "600354", None)
    assert _t.perf_counter() - t < 0.7, "不能被最慢的一路拖住"
    touch = ctx["market"]["limit_touch"]
    assert touch["touched"] is None and "没回" in touch["meta"]["notes"][0]
    assert "失败[" in caplog.records[-1].getMessage()
    _t.sleep(1.0)
    assert ("touch", date(2026, 9, 11)) in svc._live_cache, "晚到的结果进缓存，下次直接用"
    svc._live_cache.clear()


def test_指数的同一句说明不重复(db):
    d = AS_OF.date()
    intr = {}
    for code, _, _ in svc.CORE_INDEXES:
        c = ic.IntradayContext(code=code, as_of=AS_OF, trade_date=d)
        c.notes.append("2026-09-12 还没有分钟数据（没开盘或不是交易日）")
        intr[code] = c
    mk = svc._market_block(db, False, AS_OF, d, PREV, intr, [])
    assert mk["indexes_meta"]["notes"] == ["2026-09-12 还没有分钟数据（没开盘或不是交易日）"]
