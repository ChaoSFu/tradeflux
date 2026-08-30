"""
交易日历（2026-08-28 接入 fuyao）。

在此之前全仓库的"上一交易日/近N个交易日"都是从已有数据反推的：取
stock_daily_snapshots 里出现过的 distinct 日期。那个推法的前提是"我们那天跑过
且写进去了"——漏跑一天、或某天全市场拉取失败，那一天就会从"交易日"里凭空消失，
`近10个交易日`实际只覆盖 9 天，**而且不会报错**。

两条硬约束（用户 2026-08-28 提的第一条，第二条是本仓库的一贯原则）：
  · 调用频率尽量低 —— 稳态一天最多 1 次，多数日子 0 次
  · 不能变成新的单点 —— 拿不到就退回原来的反推法
"""
import json
from datetime import date

import pytest

from app.models.app_config import AppConfig
from app.services import trading_calendar as tc

DAYS = [date(2026, 8, d) for d in (21, 24, 25, 26, 27, 28)]   # 中间跨了个周末


def _seed(db, days):
    db.add(AppConfig(key=tc.CACHE_KEY,
                     value=json.dumps({"fetched_at": "x",
                                       "days": [d.isoformat() for d in days]})))
    db.commit()


class TestPureHelpers:
    def test_上一交易日跨周末(self):
        assert tc.prev_trading_day(DAYS, date(2026, 8, 24)) == date(2026, 8, 21)

    def test_上一交易日普通情形(self):
        assert tc.prev_trading_day(DAYS, date(2026, 8, 28)) == date(2026, 8, 27)

    def test_周末不是交易日(self):
        assert tc.is_trading_day(DAYS, date(2026, 8, 29)) is False
        assert tc.is_trading_day(DAYS, date(2026, 8, 28)) is True

    def test_最近N个交易日按交易日数而不是自然日(self):
        """这正是反推法会出错的地方：跨周末时自然日和交易日差好几天。"""
        assert tc.last_n_trading_days(DAYS, date(2026, 8, 25), 3) == \
            [date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25)]

    def test_不足N个有多少给多少(self):
        assert len(tc.last_n_trading_days(DAYS, date(2026, 8, 24), 10)) == 2


class TestCallFrequency:
    """用户 2026-08-28："这个调用的频率应该尽量低。" """

    def test_缓存覆盖到所需日期则零请求(self, db, monkeypatch):
        _seed(db, DAYS)
        called = []
        monkeypatch.setattr(tc, "get_api_key", lambda: called.append(1) or "k")
        monkeypatch.setattr(tc, "fetch_trading_days",
                            lambda *a, **k: called.append(1) or DAYS)
        got = tc.get_trading_days(db, need_through=date(2026, 8, 28))
        assert got == DAYS and called == [], "缓存已覆盖，一个请求都不该发"

    def test_问历史日期永远命中缓存(self, db, monkeypatch):
        _seed(db, DAYS)
        monkeypatch.setattr(tc, "fetch_trading_days",
                            lambda *a, **k: pytest.fail("历史日期不该触发拉取"))
        assert tc.get_trading_days(db, need_through=date(2026, 8, 25))

    def test_只有跨到缓存覆盖不到的日期才拉(self, db, monkeypatch):
        _seed(db, DAYS)
        called = []
        newer = DAYS + [date(2026, 8, 31)]
        monkeypatch.setattr(tc, "get_api_key", lambda: "k")
        monkeypatch.setattr(tc, "fetch_trading_days",
                            lambda *a, **k: called.append(1) or newer)
        got = tc.get_trading_days(db, need_through=date(2026, 8, 31))
        assert called == [1] and got == newer
        # 拉完写回缓存，同一天再问就不该再拉
        called.clear()
        assert tc.get_trading_days(db, need_through=date(2026, 8, 31)) == newer
        assert called == [], "同一天第二次必须命中缓存"


class TestNeverASinglePointOfFailure:
    """拿不到就退回原来的反推法，不让日历变成新的单点。"""

    def test_拉取失败时沿用旧缓存(self, db, monkeypatch):
        _seed(db, DAYS)
        monkeypatch.setattr(tc, "get_api_key", lambda: "k")
        monkeypatch.setattr(tc, "fetch_trading_days",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("网络不通")))
        assert tc.get_trading_days(db, need_through=date(2026, 9, 30)) == DAYS

    def test_没配key且无缓存返回None(self, db, monkeypatch):
        monkeypatch.setattr(tc, "get_api_key", lambda: None)
        assert tc.get_trading_days(db, need_through=date(2026, 8, 28)) is None

    def test_雷达在日历不可用时退回反推法(self, db, monkeypatch):
        """_trading_days 里那个 try/except 的意义：日历挂了雷达照样打得开。"""
        from app.services import limit_up_radar_service as radar
        from app.models.stock import Stock, StockDailySnapshot
        st = Stock(code="600000", name="浦发银行", market="SH")
        db.add(st); db.flush()
        for d in (date(2026, 8, 27), date(2026, 8, 28)):
            db.add(StockDailySnapshot(stock_id=st.id, date=d, close_price=10.0))
        db.commit()
        monkeypatch.setattr(tc, "get_trading_days",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("日历炸了")))
        got = radar._trading_days(db, date(2026, 8, 28), limit=60)
        assert got == [date(2026, 8, 28), date(2026, 8, 27)], "该退回从快照反推"
