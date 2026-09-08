"""
连板梯队热力图点格子 → 当天该档位的股票。

**这里唯一重要的性质是：列表长度必须等于格子里的数字。**
两边各写一套查询，迟早出现"格子写 3 只、点开列出 4 只"，而且没人能一眼说出
哪个对。这个仓库为「同一个市场事实两套判定函数」栽过 10 次。
"""
from datetime import date, timedelta

import pytest

from app.models.stock import Stock, StockDailySnapshot
from app.services.speculation_radar_service import (
    compute_height_series, get_ladder_members, ladder_bucket, LADDER_MAX,
)

D = [date(2026, 9, 1) + timedelta(days=i) for i in range(5)]


def _mk(db, code, name, day, board, lu=True):
    st = db.query(Stock).filter(Stock.code == code).first()
    if st is None:
        st = Stock(code=code, name=name, market="SH")
        db.add(st); db.flush()
    db.add(StockDailySnapshot(stock_id=st.id, date=day, close_price=10.0,
                              board_count=board, is_limit_up=lu, is_settled=True))
    return st


class TestBucket:
    def test_封顶档用加号(self):
        assert ladder_bucket(LADDER_MAX) == str(LADDER_MAX)
        assert ladder_bucket(LADDER_MAX + 1) == f"{LADDER_MAX}+"
        assert ladder_bucket(3) == "3"


class TestMembersMatchCounts:

    def test_列表长度等于格子里的数字(self, db):
        for i, (code, board) in enumerate(
                [("600001", 5), ("600002", 5), ("600003", 3), ("600004", 9)]):
            _mk(db, code, f"股{i}", D[2], board)
        db.commit()

        points, _ = compute_height_series(db, days=60)
        pt = next(p for p in points if p.date == str(D[2]))
        members, _ = get_ladder_members(db, D[2], "5")
        assert len(members) == pt.ladder.get("5"), \
            "格子数和明细必须同源，否则两个数会各说各的"
        assert {m["code"] for m in members} == {"600001", "600002"}

    def test_封顶档也对得上(self, db):
        _mk(db, "600010", "高标", D[1], LADDER_MAX + 3)
        _mk(db, "600011", "另一只", D[1], LADDER_MAX + 1)
        db.commit()
        points, _ = compute_height_series(db, days=60)
        pt = next(p for p in points if p.date == str(D[1]))
        members, _ = get_ladder_members(db, D[1], f"{LADDER_MAX}+")
        assert len(members) == pt.ladder.get(f"{LADDER_MAX}+") == 2

    def test_按板数降序且顺序稳定(self, db):
        _mk(db, "600020", "甲", D[0], LADDER_MAX + 1)
        _mk(db, "600021", "乙", D[0], LADDER_MAX + 5)
        db.commit()
        a, _ = get_ladder_members(db, D[0], f"{LADDER_MAX}+")
        b, _ = get_ladder_members(db, D[0], f"{LADDER_MAX}+")
        assert [m["code"] for m in a] == ["600021", "600020"]
        assert a == b, "同一天点两次结果必须一样"

    def test_没有连板数的行不算进去(self, db):
        """**board_count 缺失是「不知道」，不是 1 板。** 这条历史上栽过。"""
        _mk(db, "600030", "无连板数", D[3], 0, lu=True)
        db.commit()
        assert get_ladder_members(db, D[3], "1")[0] == []

    def test_空档位给空列表不报错(self, db):
        _mk(db, "600040", "甲", D[0], 2)
        db.commit()
        members, warns = get_ladder_members(db, D[0], "7")
        assert members == [] and warns == []

    def test_库里什么都没有时不报错(self, db):
        assert get_ladder_members(db, D[0], "3") == ([], ["没有任何涨停快照"])


class TestPointInTimeFields:
    """
    **各项指标取那一天那行快照的值，不是 Stock 表的当前值。**

    点开 7 月某天的格子，要看到的是它当时的「近60日涨停 5 次」，不是今天的。
    走 Stock.* 会静默串期——数字看着完全正常，但描述的是另一个时点，而这种错
    在界面上没有任何迹象。
    """

    def test_用当天快照的滚动统计(self, db):
        st = Stock(code="600050", name="甲", market="SH",
                   limit_up_days_60d=99, board_count_60d=99,   # Stock 上的“今天”值
                   pct_change_60d=999.0)
        db.add(st); db.flush()
        db.add(StockDailySnapshot(
            stock_id=st.id, date=D[1], close_price=10.0, board_count=4,
            is_limit_up=True, is_settled=True,
            limit_up_days_10d=2, limit_up_days_20d=3, limit_up_days_60d=5,
            board_count_60d=6, pct_change_10d=11.0, pct_change_20d=22.0,
            pct_change_60d=33.0, turnover_rate=8.5, pct_change=10.0))
        db.commit()

        m = get_ladder_members(db, D[1], "4")[0][0]
        assert m["limit_up_days_60d"] == 5, "取到了 Stock 表的当前值 99，串期了"
        assert m["board_count_60d"] == 6 and m["pct_change_60d"] == 33.0
        assert m["turnover_rate"] == 8.5 and m["pct_change"] == 10.0

    def test_那天没拿到换手就是None不是0(self, db):
        _mk(db, "600051", "乙", D[2], 3)
        db.commit()
        m = get_ladder_members(db, D[2], "3")[0][0]
        assert m["turnover_rate"] is None and m["amount"] is None, \
            "用 0 顶替「不知道」，页面上就是「换手 0%」——那是个观测，不是缺失"

    def test_没有主板块也不报错(self, db):
        _mk(db, "600052", "丙", D[2], 3)
        db.commit()
        assert get_ladder_members(db, D[2], "3")[0][0]["sector_name"] is None
