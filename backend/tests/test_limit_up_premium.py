"""
股性 · 涨停次日溢价（limit_up_premium_service）。

盯的是口径纪律：
  · 平均的是每一次涨停的「次一交易日」涨跌幅，连板也算
  · 次一交易日没有快照（停牌 / 缺口）的样本不要——不当 0，也不拿后面的日子顶
  · 只排除最新一天没结算的行；更早没标 is_settled 的是收盘价，照算
  · 没有样本是 None 不是 0，刷新会把旧值清掉
  · 只用 as_of 之前的数据
"""
from datetime import date, timedelta

from app.models.stock import Stock, StockDailySnapshot
from app.schemas.stock import StockResponse
from app.services.limit_up_premium_service import (
    compute_limit_up_premium, refresh_limit_up_premium,
)

D = [date(2026, 9, 1) + timedelta(days=i) for i in range(10)]   # 当作连续的交易日


def add_stock(db, code):
    s = Stock(code=code, name=f"股{code}")
    db.add(s)
    db.flush()
    return s


def add_bars(db, s, rows, settled=True):
    """rows: [(第几天, 当日涨跌幅, 是否涨停)]"""
    for i, pct, lu in rows:
        db.add(StockDailySnapshot(stock_id=s.id, date=D[i], pct_change=pct, is_limit_up=lu,
                                  is_settled=settled, close_price=10.0))
    db.flush()


def test_平均的是每次涨停的次日涨跌幅_连板也算(db):
    s = add_stock(db, "600001")
    add_bars(db, s, [(0, 1.0, False), (1, 10.0, True), (2, 10.0, True), (3, 3.0, False),
                     (4, -2.0, False), (5, 10.0, True), (6, -4.0, False)])
    r = compute_limit_up_premium(db, cal=D)
    assert r["by_stock"][s.id] == (3.0, 3), "三次涨停的次日：+10（连板）、+3、-4"


def test_次日停牌_不拿后面的日子顶(db):
    s = add_stock(db, "600002")
    add_bars(db, s, [(1, 10.0, True), (3, 5.0, False), (4, 10.0, True), (5, 2.0, False)])  # 第 2 天没行
    r = compute_limit_up_premium(db, cal=D)
    assert r["by_stock"][s.id] == (2.0, 1)
    assert r["skipped_gap"] == 1


def test_只排除最新一天没结算的行_老的没标也照算(db):
    old = add_stock(db, "600003")
    add_bars(db, old, [(0, 10.0, True), (1, 4.0, False)], settled=False)   # 字段还没有值那几个月写的收盘价
    add_bars(db, old, [(2, 1.0, False)])
    live = add_stock(db, "600004")
    add_bars(db, live, [(1, 10.0, True)])
    add_bars(db, live, [(2, 6.0, False)], settled=False)                   # 今天盘中那一跑写的
    r = compute_limit_up_premium(db, cal=D)
    assert r["as_of"] == D[2]
    assert r["by_stock"][old.id] == (4.0, 1)
    assert live.id not in r["by_stock"] and r["skipped_live"] == 1


def test_不偷看as_of之后的数据(db):
    s = add_stock(db, "600005")
    add_bars(db, s, [(1, 10.0, True), (2, 5.0, False), (4, 10.0, True), (5, 7.0, False)])
    assert compute_limit_up_premium(db, as_of=D[3], cal=D)["by_stock"][s.id] == (5.0, 1)


def test_没传日历时用快照日期凑(db):
    s = add_stock(db, "600006")
    add_bars(db, s, [(0, 10.0, True), (1, -3.0, False)])
    r = compute_limit_up_premium(db)
    assert r["calendar"] == "snapshot_dates" and r["by_stock"][s.id] == (-3.0, 1)


def test_刷新写回Stock_没样本是None不是0_旧值会清掉(db):
    has = add_stock(db, "600007")
    add_bars(db, has, [(0, 10.0, True), (1, 6.0, False), (2, 10.0, True), (3, -1.0, False)])
    none = add_stock(db, "600008")
    none.limit_up_next_avg_pct, none.limit_up_next_samples = 5.0, 2       # 上一次算的旧值
    add_bars(db, none, [(0, 1.0, False), (1, 2.0, False)])
    db.commit()
    r = refresh_limit_up_premium(db, cal=D)
    assert r["updated"] == 2
    db.refresh(has)
    db.refresh(none)
    assert (has.limit_up_next_avg_pct, has.limit_up_next_samples) == (2.5, 2)
    assert (none.limit_up_next_avg_pct, none.limit_up_next_samples) == (None, 0)
    assert refresh_limit_up_premium(db, cal=D)["updated"] == 0, "值没变就不写"
    out = StockResponse.model_validate(has)
    assert (out.limit_up_next_avg_pct, out.limit_up_next_samples) == (2.5, 2), "所有股票列表接口都带出来"
