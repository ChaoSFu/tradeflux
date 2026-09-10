"""
冻结群体的逐日曲线（2026-09-10）+ 「这一天收盘了没有」的共用判定。

这张曲线画在「昨日群体 · 今日反馈」那张表的正上方，所以它有一条硬约束：
**曲线最后一点必须等于表里那一行**。两边都读 cohorts_json 里的
median_pct_change，中间不许再有一层加工——一旦有，就是同一个事实两套口径。
"""
from datetime import date

import pytest

from app.models.stock import Stock, StockDailySnapshot
from app.services.market_effect_service import build_cohort_series
from app.services.snapshot_settlement import date_is_settled, settled_by_date

D1, D2, D3 = date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 10)


def _snap(db, stock, d, settled):
    db.add(StockDailySnapshot(stock_id=stock.id, date=d, close_price=10.0,
                              pct_change=1.0, is_settled=settled))


@pytest.fixture
def stocks(db):
    out = []
    for i in range(3):
        st = Stock(code=f"60000{i}", name=f"票{i}", market="SH")
        db.add(st); out.append(st)
    db.flush()
    return out


class TestSettledByDate:
    """判定规则本身。每一条都是踩出来的，不是推出来的。"""

    def test_全部结算才算结算(self, db, stocks):
        for st in stocks:
            _snap(db, st, D1, True)
        db.flush()
        assert date_is_settled(db, D1) is True

    def test_有一行是盘中值整天就不算结算(self, db, stocks):
        """盘中跑过日更、收盘后只补回一部分——剩下那几行让整天不可信。"""
        _snap(db, stocks[0], D1, True)
        _snap(db, stocks[1], D1, True)
        _snap(db, stocks[2], D1, False)
        db.flush()
        assert date_is_settled(db, D1) is False

    def test_NULL按未结算算(self, db, stocks):
        """is_settled 2026-05-28 才加，之前的历史行全是 NULL。不知道 ≠ 已结算。"""
        _snap(db, stocks[0], D1, True)
        _snap(db, stocks[1], D1, None)
        db.flush()
        assert date_is_settled(db, D1) is False

    def test_库里没这天是None不是False(self, db, stocks):
        """「这天没数据」和「这天没收盘」是两回事，前者不该被渲染成"盘中"。"""
        assert date_is_settled(db, D1) is None

    def test_一次问多天每天各判各的(self, db, stocks):
        _snap(db, stocks[0], D1, True)
        _snap(db, stocks[0], D2, False)
        db.flush()
        assert settled_by_date(db, [D1, D2, D3]) == {D1: True, D2: False, D3: None}

    def test_空输入返回空(self, db):
        assert settled_by_date(db, []) == {}

    def test_涨跌停名单和曲线用的是同一个判定(self):
        """
        strong_stock_service 里原来抄了一份同样的逻辑。它现在必须调共用函数——
        本项目为「同一个市场事实两套判定函数」栽过十次。
        """
        import inspect
        from app.services import strong_stock_service as sss
        src = inspect.getsource(sss)
        assert "date_is_settled(db, target_date)" in src
        assert "all(bool(f) for f in settled_flags)" not in src, "又抄了一份"


def _cohorts(**kw):
    """{cohort_type: {median, member, valid}} 的速记。"""
    return {ct: {"cohort_type": ct, "label": ct,
                 "median_pct_change": m, "member_count": mem, "valid_count": val}
            for ct, (m, mem, val) in kw.items()}


class TestBuildCohortSeries:
    def test_逐日铺开并保留成员数(self, db):
        out = build_cohort_series(
            [(D1, _cohorts(limit_up=(1.5, 40, 38))),
             (D2, _cohorts(limit_up=(-2.0, 30, 29)))],
            {D1: True, D2: True})
        assert [p["trade_date"] for p in out["points"]] == [D1, D2]
        assert out["points"][0]["values"]["limit_up"]["median_pct_change"] == 1.5
        assert out["points"][1]["values"]["limit_up"]["valid_count"] == 29
        assert out["as_of"] == D2

    def test_中位收益缺失时保持None而不是0(self, db):
        """
        valid_count 不足 → 后端本来就给 None。填成 0 的话，「这群票不涨不跌」
        和「没有这群票」在图上长得一模一样。
        """
        out = build_cohort_series(
            [(D1, _cohorts(limit_down=(None, 7, 1)))], {D1: True})
        v = out["points"][0]["values"]["limit_down"]
        assert v["median_pct_change"] is None
        assert (v["member_count"], v["valid_count"]) == (7, 1)

    def test_当天没有这个群体就没有这个key(self, db):
        """跌停 0 只的日子不该在图上留一个 0% 的点。"""
        out = build_cohort_series(
            [(D1, _cohorts(limit_up=(1.0, 10, 10)))], {D1: True})
        assert "limit_down" not in out["points"][0]["values"]

    def test_图例只给出现过的群体(self, db):
        out = build_cohort_series(
            [(D1, _cohorts(limit_up=(1.0, 10, 10))),
             (D2, _cohorts(limit_up=(1.0, 10, 10), limit_down=(-3.0, 5, 5)))],
            {D1: True, D2: True})
        assert [c["cohort_type"] for c in out["cohorts"]] == ["limit_up", "limit_down"]

    def test_图例顺序按COHORT_LABELS不按出现先后(self, db):
        """跟下面那张表的行序对齐，读的人不用在两处重新找位置。"""
        out = build_cohort_series(
            [(D1, _cohorts(broken_board=(-1.0, 5, 5), limit_up=(1.0, 10, 10)))],
            {D1: True})
        assert [c["cohort_type"] for c in out["cohorts"]] == ["limit_up", "broken_board"]

    def test_cohorts_json为空不炸(self, db):
        out = build_cohort_series([(D1, None)], {D1: True})
        assert out["points"][0]["values"] == {} and out["cohorts"] == []

    def test_没数据时as_of为None(self, db):
        out = build_cohort_series([], {})
        assert out["as_of"] is None and out["points"] == []

    def test_未收盘的最后一点被标出来并说清覆盖率(self, db):
        """
        盘中最后一点是「盘中价 × 部分成员」算的。不标出来，它就在图上冒充
        当日结果——这个仓库有一整个 test_settled_snapshot.py 在讲这件事。
        """
        out = build_cohort_series(
            [(D1, _cohorts(limit_up=(1.0, 40, 40))),
             (D2, _cohorts(limit_up=(3.83, 48, 28)))],
            {D1: True, D2: False})
        assert out["points"][-1]["is_settled"] is False
        note = " ".join(out["notes"])
        assert "还没收盘" in note and "28/48" in note

    def test_已收盘就不提盘中(self, db):
        out = build_cohort_series(
            [(D1, _cohorts(limit_up=(1.0, 40, 40)))], {D1: True})
        assert "还没收盘" not in " ".join(out["notes"])

    def test_不知道收没收盘时不谎称盘中(self, db):
        """settled=None 是「不知道」。把它当成 False 就是拿猜测吓唬人。"""
        out = build_cohort_series(
            [(D1, _cohorts(limit_up=(1.0, 40, 40)))], {D1: None})
        assert out["points"][-1]["is_settled"] is None
        assert "还没收盘" not in " ".join(out["notes"])
