"""
补历史日快照的公共写入函数（2026-09-11 从 daily_update._backfill_history_from_dump 抽出）。

10 日 dump 补历史和 10 年存档补洞走的是它。三条规则一条都不能松：
只写历史日、已有行绝不覆盖（原为空的量额除外）、只写 K 线原始字段。
"""
from datetime import date

from app.models.stock import Stock, StockDailySnapshot
from app.services.eastmoney_fetcher import build_kline_bar
from app.services.snapshot_history import insert_history_bars

TODAY = date(2026, 9, 11)
D1, D2, D3 = date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)


def _bar(d, close=10.0, vol=1e6):
    return build_kline_bar(dt=d, open_p=close, close_p=close, high_p=close, low_p=close,
                           pct=0.0, turnover=None, prev_close=close,
                           volume=vol, amount=(vol * close if vol else None),
                           volume_source="dump" if vol else None)


def _stock(db, code="600984"):
    st = Stock(code=code, name="建设机械", market="SH")
    db.add(st); db.flush()
    return st


def test_只补历史日不碰当日(db):
    st = _stock(db)
    added, _ = insert_history_bars(db, {"600984": [_bar(D3), _bar(TODAY)]}, {"600984": st.id}, TODAY)
    assert added == 1
    assert [r.date for r in db.query(StockDailySnapshot).all()] == [D3]


def test_已有行绝不覆盖(db):
    st = _stock(db)
    db.add(StockDailySnapshot(stock_id=st.id, date=D3, close_price=99.0, is_limit_up=True,
                              volume=5.0, is_settled=True))
    db.flush()
    added, vol = insert_history_bars(db, {"600984": [_bar(D3)]}, {"600984": st.id}, TODAY)
    row = db.query(StockDailySnapshot).one()
    assert (added, vol) == (0, 0)
    assert row.close_price == 99.0 and row.is_limit_up is True and row.volume == 5.0


def test_原为空的量额才补(db):
    st = _stock(db)
    db.add(StockDailySnapshot(stock_id=st.id, date=D3, close_price=10.0, is_settled=True))
    db.flush()
    added, vol = insert_history_bars(db, {"600984": [_bar(D3, vol=2e6)]}, {"600984": st.id}, TODAY)
    db.expire_all()
    row = db.query(StockDailySnapshot).one()
    assert (added, vol) == (0, 1)
    assert row.volume == 2e6 and row.volume_source == "dump"
    assert row.close_price == 10.0, "只补量额，价格不动"


def test_新行的字段(db):
    st = _stock(db)
    insert_history_bars(db, {"600984": [_bar(D3, close=4.66)]}, {"600984": st.id}, TODAY)
    r = db.query(StockDailySnapshot).one()
    assert r.close_price == 4.66 and (r.open_price, r.high_price, r.low_price) == (4.66, 4.66, 4.66)
    assert r.turnover_rate is None, "dump 和存档都没有换手率，写 0 会被读成真实的 0%"
    assert r.is_settled is True, "收盘后生成的数据，历史日必然是终值"
    assert r.volume_source == "dump"


def test_only_dates卡住范围(db):
    st = _stock(db)
    added, _ = insert_history_bars(db, {"600984": [_bar(D1), _bar(D2), _bar(D3)]},
                                   {"600984": st.id}, TODAY, only_dates={D2})
    assert added == 1
    assert [r.date for r in db.query(StockDailySnapshot).all()] == [D2]


def test_dry_run只数不写(db):
    st = _stock(db)
    counts = {}
    added, _ = insert_history_bars(db, {"600984": [_bar(D1), _bar(D2)]}, {"600984": st.id},
                                   TODAY, dry_run=True, counts=counts)
    assert added == 2 and counts == {"600984": 2}
    assert db.query(StockDailySnapshot).count() == 0


def test_不在库里的票跳过(db):
    st = _stock(db)
    added, _ = insert_history_bars(db, {"600984": [_bar(D3)], "000001": [_bar(D3)]},
                                   {"600984": st.id}, TODAY)
    assert added == 1


def test_同一天重复出现只建一行(db):
    st = _stock(db)
    added, _ = insert_history_bars(db, {"600984": [_bar(D3), _bar(D3)]}, {"600984": st.id}, TODAY)
    assert added == 1 and db.query(StockDailySnapshot).count() == 1
