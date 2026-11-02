"""
个股 → 板块关联同步。

2026-11-02 实测强势池 73 只有 37 只、当日涨停 125 只有 49 只一个板块都没有，而主板块
都还在：F10 拉空时被当成「这只票现在没有任何板块」，旧关联全被删了。
"""
from datetime import date

import pytest

from app.models.sector import Sector, StockSectorRelation
from app.models.stock import Stock, StockDailySnapshot
from scripts import sync_boards as sb


@pytest.fixture
def seeded(db):
    st = Stock(code="600001", name="测试", in_strong_pool=True)
    a = Sector(code="BK0001", name="甲", is_watched=True)
    b = Sector(code="BK0002", name="乙", is_watched=True)
    db.add_all([st, a, b])
    db.flush()
    db.add(StockDailySnapshot(stock_id=st.id, date=date(2026, 11, 2), close_price=10.0))
    db.add(StockSectorRelation(stock_id=st.id, sector_id=a.id))
    db.commit()
    return st, a, b


def _rels(db, st):
    return {r.sector_id for r in db.query(StockSectorRelation).filter_by(stock_id=st.id).all()}


def test_F10拉空不删旧关联(db, seeded, monkeypatch):
    st, a, _ = seeded
    monkeypatch.setattr(sb, "fetch_stock_bk_codes", lambda code, client=None: [])
    r = sb.sync_stock_sector_relations(db)
    assert _rels(db, st) == {a.id}
    assert r["fetch_empty"] == 1 and r["relations_removed"] == 0


def test_F10抛异常也不删(db, seeded, monkeypatch):
    st, a, _ = seeded

    def boom(code, client=None):
        raise RuntimeError("被掐了")
    monkeypatch.setattr(sb, "fetch_stock_bk_codes", boom)
    r = sb.sync_stock_sector_relations(db)
    assert _rels(db, st) == {a.id}
    assert r["fetch_failed"] == 1 and r["relations_removed"] == 0


def test_拿到真实结果时照常增删(db, seeded, monkeypatch):
    st, _, b = seeded
    monkeypatch.setattr(sb, "fetch_stock_bk_codes", lambda code, client=None: ["BK0002"])
    r = sb.sync_stock_sector_relations(db)
    assert _rels(db, st) == {b.id}
    assert (r["relations_added"], r["relations_removed"]) == (1, 1)


def test_板块都不在关注范围时清掉旧关联(db, seeded, monkeypatch):
    """拿到了结果、只是没有关注的板块：这是真实变化，不是没拿到。"""
    st, _, _ = seeded
    monkeypatch.setattr(sb, "fetch_stock_bk_codes", lambda code, client=None: ["BK9999"])
    sb.sync_stock_sector_relations(db)
    assert _rels(db, st) == set()


@pytest.mark.parametrize("kw, want", [
    ({}, True),                      # 手动全量 / 周六全量
    ({"stock_count": False}, False), # 日更
    ({"meta_only": True}, False),    # 板块行情同步
])
def test_成份股数量只在全量同步时拉(monkeypatch, kw, want):
    class _Db:
        def rollback(self): ...
        def close(self): ...
    seen = {}
    monkeypatch.setattr(sb, "SessionLocal", lambda: _Db())
    monkeypatch.setattr(sb, "init_db", lambda: None)
    monkeypatch.setattr(sb, "sync_board_metadata",
                        lambda db, update_stock_count: seen.update(c=update_stock_count))
    monkeypatch.setattr(sb, "sync_stock_sector_relations", lambda db: {
        "stocks_processed": 0, "skipped": 0, "relations_added": 0,
        "relations_removed": 0, "fetch_failed": 0})
    sb.run_sync_boards(**kw)
    assert seen["c"] is want
