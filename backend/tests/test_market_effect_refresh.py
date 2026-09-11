"""
市场效应缓存重算（2026-09-11）。

缓存一经算出就不再更新，而它依赖的快照之后还会变。生产核对：09-10 昨日涨停缓存
+3.83%（28/48 只有次日结果），全部 48 只算是 -1.04%——**符号是反的**，因为缓存
只算了当天还留在候选池里的票。market_effect_daily 有 JSONB 列，SQLite 建不了表，
所以这里钉住的是「重算哪些天」，重算本身走的就是现成的 compute_and_cache。
"""
from datetime import date

from app.models.stock import Stock, StockDailySnapshot
from app.services import market_effect_service as mes

DAYS = [date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10), date(2026, 9, 11)]


def _seed(db):
    st = Stock(code="600984", name="建设机械", market="SH")
    db.add(st); db.flush()
    for d in DAYS:
        db.add(StockDailySnapshot(stock_id=st.id, date=d, close_price=10.0, is_settled=True))
    db.flush()


def test_重算给定的每一天(monkeypatch):
    seen = []
    monkeypatch.setattr(mes, "compute_and_cache", lambda db, d: seen.append(d))
    assert mes.refresh_effects(None, DAYS[:3]) == 3
    assert seen == DAYS[:3]


def test_最近N天含今天_从旧到新(db, monkeypatch):
    _seed(db)
    seen = []
    monkeypatch.setattr(mes, "compute_and_cache", lambda db_, d: seen.append(d))
    assert mes.refresh_recent(db, 3) == 3
    assert seen == DAYS[-3:], "今天那行也得重算：第一次被请求时是盘中，缓存就停在盘中"


def test_库里没数据就什么都不算(db, monkeypatch):
    seen = []
    monkeypatch.setattr(mes, "compute_and_cache", lambda db_, d: seen.append(d))
    assert mes.refresh_recent(db, 12) == 0 and seen == []
