"""
成交额前列存档的落库日期（2026-11-08 修）。

原来 sync_turnover_pool 一律写 date.today()：周末、节假日、开盘前触发同步，拿到的是上一
交易日收盘的名单，却记在周日/节假日名下——本地库 60 行记在 2026-11-08（周日）。按交易日
取数的下游（复盘核心池、数据体检的当天快照项）都对不上。

现在落库日期 = 这批数据所属的交易日：
  · daily_update 传自己的 target_date，不另算
  · 页面自愈/手动刷新按市场时间（腾讯字段30）+ 交易日历现算
  · 两条路都过同一道闸：日历说不是交易日、或晚于市场时钟，一律不写
"""
import json
from datetime import date, datetime

import pytest

from app.models.app_config import AppConfig
from app.models.turnover_pool import TurnoverPoolDaily
from app.services import trading_calendar as tc
from app.services import turnover_service as ts
from app.services.eastmoney_fetcher import SH_TZ

THU, FRI, SAT, SUN, MON = (date(2026, 11, d) for d in (5, 6, 7, 8, 9))
CAL = [date(2026, 11, d) for d in (2, 3, 4, 5, 6)]           # 日历拉取于周末：只到周五

# 2026-11-08（周日）20:17 实测 probe_market_now()：停在周五收盘后
SUNDAY_MARKET_CLOCK = datetime(2026, 11, 6, 16, 14, 48, tzinfo=SH_TZ)


def _at(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm)


# ── 纯函数：market_data_day ──────────────────────────────────────────────────

class TestMarketDataDay:
    def test_周日的市场时钟停在周五收盘后_就是周五(self):
        assert tc.market_data_day(CAL, SUNDAY_MARKET_CLOCK.replace(tzinfo=None)) == FRI

    def test_本机时钟周日_日历往前取到周五(self):
        assert tc.market_data_day(CAL, _at(SUN, 20)) == FRI
        assert tc.market_data_day(CAL, _at(SAT, 10)) == FRI

    def test_没有日历也能退过周末(self):
        assert tc.market_data_day(None, _at(SUN, 20)) == FRI
        assert tc.market_data_day([], _at(SAT, 10)) == FRI

    def test_开盘前行情还停在上一交易日(self):
        assert tc.market_data_day(CAL, _at(MON, 8, 30)) == FRI
        assert tc.market_data_day(CAL, _at(FRI, 9, 0)) == THU

    def test_竞价开始后算今天_日历没覆盖到今天是常态(self):
        assert tc.market_data_day(CAL, _at(MON, 9, 15)) == MON
        assert tc.market_data_day(CAL, _at(MON, 14)) == MON

    def test_交易日当天收盘后还是当天(self):
        assert tc.market_data_day(CAL, _at(FRI, 15, 30)) == FRI

    def test_节假日按日历往前取(self):
        cal = [date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 8), date(2026, 10, 9)]
        assert tc.market_data_day(cal, _at(date(2026, 10, 5), 15, 30)) == date(2026, 9, 30)

    def test_日历里没有更早的交易日返回None(self):
        assert tc.market_data_day(CAL, _at(date(2026, 11, 1), 12)) is None

    def test_最近工作日(self):
        assert tc.last_weekday_before(MON) == FRI
        assert tc.last_weekday_before(SUN) == FRI
        assert tc.last_weekday_before(FRI) == THU


# ── 带库：resolve_trade_date / sync_turnover_pool / get_turnover_overview ────

def _seed_calendar(db, days=CAL):
    db.add(AppConfig(key=tc.CACHE_KEY, value=json.dumps(
        {"fetched_at": "x", "days": [d.isoformat() for d in days]})))
    db.commit()


def _add_rows(db, d: date, codes, amount=1e9):
    for i, c in enumerate(codes, start=1):
        db.add(TurnoverPoolDaily(date=d, stock_code=c, stock_name=f"股{c}", rank=i,
                                 amount=amount, pct_change=1.0, turnover_rate=2.0,
                                 sector_name="板块", market="SH"))
    db.commit()


def _dates(db):
    return sorted({d for (d,) in db.query(TurnoverPoolDaily.date).distinct()})


API_ROWS = [{"code": "600001", "name": "甲", "pct_change": 3.0, "amount": 5e9,
             "turnover_rate": 4.0, "market": "SH"},
            {"code": "600002", "name": "乙", "pct_change": -1.0, "amount": 4e9,
             "turnover_rate": 3.0, "market": "SH"}]


@pytest.fixture
def env(db, monkeypatch):
    """不出网：日历只读缓存，选股 API / F10 / 市场时间都换成桩。"""
    monkeypatch.setattr(tc, "get_api_key", lambda: None)
    calls = {"fetch": 0}

    def fake_fetch(_kw):
        calls["fetch"] += 1
        return [dict(r) for r in API_ROWS]

    monkeypatch.setattr(ts, "fetch_turnover_top_stocks", fake_fetch)
    monkeypatch.setattr(ts, "_resolve_sector_names", lambda _db, codes: {c: "新板块" for c in codes})
    monkeypatch.setattr(ts, "probe_market_now", lambda: SUNDAY_MARKET_CLOCK)
    monkeypatch.setitem(ts._last_sync_attempt, "ts", None)
    _seed_calendar(db)
    return calls


def _freeze_local_clock(monkeypatch, at: datetime):
    """本机时钟（get_turnover_overview 的读路径、探测失败时的兜底）。"""
    class _Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.replace(tzinfo=tz) if tz else at
    monkeypatch.setattr(ts, "datetime", _Fixed)


class TestResolveTradeDate:
    def test_周日现算_记在周五且已收盘(self, db, env):
        assert ts.resolve_trade_date(db) == (FRI, True, None)

    def test_给定日期是非交易日_拒绝(self, db, env):
        day, settled, why = ts.resolve_trade_date(db, trade_date=SUN, market_now=_at(SUN, 20))
        assert day is None and "不是交易日" in why

    def test_给定日期晚于市场时钟_拒绝(self, db, env):
        """节假日 K 线全拉挂时 daily_update 的 target_date 会停在 date.today()。"""
        day, _, why = ts.resolve_trade_date(db, trade_date=MON, market_now=SUNDAY_MARKET_CLOCK)
        assert day is None and "晚于市场时间" in why

    def test_daily_update_的_target_date_照用(self, db, env):
        assert ts.resolve_trade_date(db, trade_date=FRI, market_now=SUNDAY_MARKET_CLOCK) == (FRI, True, None)

    def test_盘中写的是未收盘的当天(self, db, env):
        assert ts.resolve_trade_date(db, market_now=_at(MON, 10)) == (MON, False, None)

    def test_探测失败_本机时钟周日_日历能确认周五(self, db, env, monkeypatch):
        monkeypatch.setattr(ts, "probe_market_now", lambda: None)
        _freeze_local_clock(monkeypatch, _at(SUN, 20))
        assert ts.resolve_trade_date(db) == (FRI, True, None)

    def test_探测失败_日历又没覆盖到今天_不写(self, db, env, monkeypatch):
        """说不准是交易日还是节假日（日历盘中本来就不含今天），宁可这次不写。"""
        monkeypatch.setattr(ts, "probe_market_now", lambda: None)
        _freeze_local_clock(monkeypatch, _at(MON, 10))
        day, _, why = ts.resolve_trade_date(db)
        assert day is None and "说不准" in why


class TestSync:
    def test_周日同步记在周五名下_不产生周日的行(self, db, env):
        r = ts.sync_turnover_pool(db)
        assert r["ok"] and r["date"] == FRI.isoformat() and r["settled"] is True
        assert _dates(db) == [FRI]

    def test_同一交易日重跑覆盖不叠加(self, db, env):
        _add_rows(db, FRI, ["600009", "600001"])
        ts.sync_turnover_pool(db)
        rows = db.query(TurnoverPoolDaily).filter(TurnoverPoolDaily.date == FRI).all()
        assert sorted(r.stock_code for r in rows) == ["600001", "600002"]

    def test_daily_update_传入的日期优先(self, db, env):
        r = ts.sync_turnover_pool(db, trade_date=THU, market_now=SUNDAY_MARKET_CLOCK)
        assert r["date"] == THU.isoformat() and _dates(db) == [THU]

    def test_拒绝时不调选股API也不动库(self, db, env):
        _add_rows(db, FRI, ["600009"])
        r = ts.sync_turnover_pool(db, trade_date=SUN, market_now=_at(SUN, 20))
        assert not r["ok"] and r["date"] is None and env["fetch"] == 0
        assert _dates(db) == [FRI]

    def test_板块归属沿用旧行(self, db, env):
        _add_rows(db, THU, ["600001"])
        ts.sync_turnover_pool(db)
        got = {r.stock_code: r.sector_name for r in
               db.query(TurnoverPoolDaily).filter(TurnoverPoolDaily.date == FRI)}
        assert got == {"600001": "板块", "600002": "新板块"}


class TestOverview:
    def test_周日打开页面_周五有存档就不再同步(self, db, env, monkeypatch):
        _add_rows(db, THU, ["600001"])
        _add_rows(db, FRI, ["600001", "600002"])
        _freeze_local_clock(monkeypatch, _at(SUN, 20))
        out = ts.get_turnover_overview(db)
        assert out["date"] == FRI.isoformat() and env["fetch"] == 0
        assert _dates(db) == [THU, FRI]
        assert {s["code"]: s["is_new"] for s in out["stocks"]} == {"600001": False, "600002": True}

    def test_旧版本记在周日的行_不当最新也不当前一天(self, db, env, monkeypatch):
        _add_rows(db, THU, ["600001"])
        _add_rows(db, FRI, ["600001", "600002"])
        _add_rows(db, SUN, ["600001", "600002"])          # 修复前留下的：周五的名单记在周日
        _freeze_local_clock(monkeypatch, _at(MON, 8, 30))
        out = ts.get_turnover_overview(db)
        assert out["date"] == FRI.isoformat() and env["fetch"] == 0
        # 前一天是周四（600002 新进），不是周日那份周五名单的复制品（那样就全都不新）
        assert out["new_count"] == 1

    def test_周日周五没有存档_自愈同步补到周五(self, db, env, monkeypatch):
        _add_rows(db, THU, ["600001"])
        _freeze_local_clock(monkeypatch, _at(SUN, 20))
        out = ts.get_turnover_overview(db)
        assert env["fetch"] == 1 and out["date"] == FRI.isoformat()
        assert _dates(db) == [THU, FRI]

    def test_盘前同步按市场时间写_展示同步写进的那天(self, db, env, monkeypatch):
        """本机时钟以为是周一盘中，市场时钟还停在周五：按市场时钟写，展示写进去的那天。"""
        _freeze_local_clock(monkeypatch, _at(MON, 9, 20))
        out = ts.get_turnover_overview(db)
        assert env["fetch"] == 1 and out["date"] == FRI.isoformat()
        assert _dates(db) == [FRI]
