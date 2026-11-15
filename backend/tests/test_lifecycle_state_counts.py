"""
逐日各生命周期状态的只数（2026-11-15）：只是计数，跟池子大小一起给；只算当天在高标池里的票；
分得清存量和当天新进；最新那天还没收盘就不出点。
"""
from datetime import date

import pytest

from app.models.leader_cycle import LeaderCycleSnapshot
from app.models.stock import Stock, StockDailySnapshot
from app.services.leader_cycle_effect_service import POOL_SCOPE, compute_effect

DAYS = [date(2026, 9, i) for i in (1, 2, 3, 4)]


@pytest.fixture(autouse=True)
def _cal(db):
    from app.services.trading_calendar import _write_cache
    _write_cache(db, DAYS)


def _mk(db, code, days, closes, *, breaks=None, settled_last=True):
    """breaks: {日期: 断板日} —— 那天的快照记着断板。"""
    st = Stock(code=code, name=f"股{code}", market="SH", in_strong_pool=True, board_count_60d=5)
    db.add(st)
    db.flush()
    for i, day in enumerate(days):
        last = i == len(days) - 1
        db.add(StockDailySnapshot(stock_id=st.id, date=day, close_price=closes[i],
                                  is_settled=settled_last or not last))
        b = (breaks or {}).get(day)
        db.add(LeaderCycleSnapshot(
            stock_id=st.id, stock_code=code, date=day, board_count_60d=5,
            cycle_start_date=DAYS[0], cycle_peak_date=DAYS[0], peak_board_count=5,
            latest_close=closes[i], data_fresh=True, bar_settled=(settled_last or not last),
            break_date=b, days_since_break=((day - b).days if b else None)))
    return st


def _series(r):
    return {p["trade_date"]: p for p in r["state_counts"]["series"]}


def test_只数跟池子大小一起给_各状态加起来不超过池子(db):
    _mk(db, "600001", DAYS, [10, 11, 12, 13])
    _mk(db, "600002", DAYS, [10, 10, 10, 10])
    db.commit()
    r = compute_effect(db, DAYS[-1])
    sc = r["state_counts"]
    assert sc["scope"] == POOL_SCOPE
    for p in sc["series"]:
        assert p["pool"] == 2
        assert sum(p["counts"].values()) <= p["pool"]


def test_掉出高标池的票不再按旧状态算(db):
    """那天没有快照行 = 不在池子里。不能拿最后一次有效状态把它一直算下去。"""
    _mk(db, "600001", DAYS[:2], [10, 11])          # 09-02 之后掉出池子
    _mk(db, "600002", DAYS, [10, 10, 10, 10])
    db.commit()
    r = compute_effect(db, DAYS[-1])
    s = _series(r)
    for d in ("2026-09-03", "2026-09-04"):
        assert s[d]["pool"] == 1
        assert sum(s[d]["counts"].values()) <= 1, "掉出去的票不该还算在某个状态里"
    # 赚钱效应那边同一条规则：09-03 掉出去的票，09-04 不该再贡献「昨天该状态」的收益
    by_date = {p["trade_date"]: p["values"] for p in r["series"]}
    assert sum(v["n"] for v in by_date.get("2026-09-04", {}).values()) <= 1


def test_当天新进跟存量分开(db):
    _mk(db, "600001", DAYS, [10, 11, 10, 10], breaks={DAYS[2]: DAYS[2], DAYS[3]: DAYS[2]})
    db.commit()
    s = _series(compute_effect(db, DAYS[-1]))
    assert [s[d.isoformat()]["counts"] for d in DAYS] == [
        {"STREAKING": 1}, {"STREAKING": 1}, {"BROKEN": 1}, {"BROKEN": 1}]
    assert s["2026-09-03"]["entered"] == {"BROKEN": 1}, "断板那天：新进刚断板"
    assert s["2026-09-04"]["entered"] == {}, "第二天还是刚断板，是存量不是新进"
    assert s["2026-09-02"]["entered"] == {}


def test_最新那天还没收盘不出点(db):
    """未结算的行判 UNKNOWN、退回昨天的状态——画出来就是拿昨天冒充今天。"""
    _mk(db, "600001", DAYS, [10, 11, 12, 13], settled_last=False)
    db.commit()
    r = compute_effect(db, DAYS[-1])
    assert "2026-09-04" not in _series(r)
    assert r["state_counts"]["skipped_unsettled"] == ["2026-09-04"]
    assert "2026-09-03" in _series(r)


def test_没有数据(db):
    assert compute_effect(db, None)["state_counts"] is None
