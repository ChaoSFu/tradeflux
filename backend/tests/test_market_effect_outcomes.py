"""
昨日群体的今日结果：快照优先，快照没有的用日更补抓的当日行情（2026-09-11）。

根因：快照只有当天候选池里的票才有那一行，掉出池子的（多半走弱）要到第二天才补上，
于是当天的反馈只算了幸存者——09-10 昨日涨停缓存 +3.83%（28/48），全部 48 只是 -1.04%。
"""
from datetime import date, datetime

from app.models.market_effect import CohortOutcomeQuote
from app.models.stock import Stock, StockDailySnapshot
from app.services import market_effect_service as mes

P, T = date(2026, 9, 10), date(2026, 9, 11)   # 群体冻结日 / 反馈日


def _stocks(db, codes):
    out = {}
    for c in codes:
        st = Stock(code=c, name=c, market="SH")
        db.add(st); out[c] = st
    db.flush()
    return out


def _snap(db, st, d, **kw):
    db.add(StockDailySnapshot(stock_id=st.id, date=d, is_settled=True, **kw))


def _quote(db, st, pct, lu=False):
    db.add(CohortOutcomeQuote(trade_date=T, stock_id=st.id, pct_change=pct, is_limit_up=lu,
                              is_settled=True, fetched_at=datetime(2026, 9, 11, 15, 30)))


def _seed(db):
    s = _stocks(db, ["600001", "600002", "600003"])
    for st in s.values():                                    # 三只都是昨日首板
        _snap(db, st, P, is_limit_up=True, board_count=1, pct_change=10.0, close_price=11.0)
    _snap(db, s["600001"], T, is_limit_up=True, board_count=2, pct_change=10.0, close_price=12.1)
    _quote(db, s["600002"], -5.0)                            # 掉出池子：只有行情
    db.flush()                                               # 600003：两边都没有（停牌）
    return s


def test_掉出池子的票用当日行情补进样本(db):
    _seed(db)
    r = mes.compute_cohort_outcome(db, P, T, "limit_up")
    assert (r["member_count"], r["valid_count"], r["quote_count"]) == (3, 2, 1)
    assert r["median_pct_change"] == 2.5, "中位数要把走弱的那只算进去：(10 + -5) / 2"
    assert r["advance_ratio"] == 0.5 and r["broken_ratio"] == 0.5


def test_快照优先_行情不覆盖快照(db):
    s = _seed(db)
    _quote(db, s["600001"], -3.0)        # 同一只票两边都有：以快照（收盘终值）为准
    db.flush()
    r = mes.compute_cohort_outcome(db, P, T, "limit_up")
    assert r["quote_count"] == 1 and r["median_pct_change"] == 2.5


def test_快照有行但涨跌幅为空时用行情(db):
    s = _seed(db)
    _snap(db, s["600003"], T, pct_change=None)
    _quote(db, s["600003"], -2.0)
    db.flush()
    r = mes.compute_cohort_outcome(db, P, T, "limit_up")
    assert (r["valid_count"], r["quote_count"]) == (3, 2)


def test_明细跟统计走同一个来源(db):
    _seed(db)
    rows = {r["code"]: r for r in mes.list_cohort_members(db, P, T, "limit_up")}
    assert rows["600001"]["outcome_source"] == "snapshot" and rows["600001"]["outcome_board_count"] == 2
    assert rows["600002"]["outcome_source"] == "quote" and rows["600002"]["outcome_board_count"] == 0
    assert rows["600003"]["has_outcome"] is False and rows["600003"]["outcome_source"] is None
    r = mes.compute_cohort_outcome(db, P, T, "limit_up")
    assert sum(1 for x in rows.values() if x["has_outcome"]) == r["valid_count"]


def test_行情补的涨停算晋级(db):
    s = _stocks(db, ["600009"])
    _snap(db, s["600009"], P, is_limit_up=True, board_count=1, pct_change=10.0, close_price=11.0)
    _quote(db, s["600009"], 10.0, lu=True)
    db.flush()
    r = mes.compute_cohort_outcome(db, P, T, "limit_up")
    assert r["advance_ratio"] == 1.0 and r["broken_ratio"] == 0.0


def test_全部成员是六个群体的并集(db):
    s = _stocks(db, ["600001", "600002", "600003"])
    _snap(db, s["600001"], P, is_limit_up=True, board_count=1, pct_change=10.0)
    _snap(db, s["600002"], P, is_limit_down=True, pct_change=-10.0)
    _snap(db, s["600003"], P, pct_change=1.0)                # 什么群体都不是
    db.flush()
    assert mes.cohort_member_ids(db, P) == {s["600001"].id, s["600002"].id}


def test_保存行情按日期和股票覆盖(db):
    s = _stocks(db, ["600001"])
    sid = s["600001"].id
    mes.save_outcome_quotes(db, T, [(sid, 10.0, 1.0, False, False)], False, datetime(2026, 9, 11, 11))
    mes.save_outcome_quotes(db, T, [(sid, 9.5, -4.0, False, False)], True, datetime(2026, 9, 11, 15, 30))
    q = db.query(CohortOutcomeQuote).one()
    assert (q.pct_change, q.is_settled) == (-4.0, True), "收盘后那一跑覆盖盘中那一跑"
