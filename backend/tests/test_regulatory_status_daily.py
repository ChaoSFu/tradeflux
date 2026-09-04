"""
每日监管状态快照的落库语义。

为什么需要这张表：sync_regulatory_unusual 每次同步都 DELETE 整批再重建，昨天的
名单是被**物理删除**的。破局雷达要问"监管约束在变紧还是变松"，靠的全是相邻两天
相减——进入 / 解除 / 延长 / 刚解除又逼近。今天不开始记，一个月后还是答不出来。
"""
from datetime import date

import pytest

from app.models.regulatory import RegulatoryStatusDaily
from app.schemas.regulatory import (
    RegulatoryWatchlistResponse, RegulatoryItem, ApproachingItem,
)
from app.services import regulatory_service as rs

D1 = date(2026, 9, 2)
D2 = date(2026, 9, 3)


def _item(code, name, end, days, status="monitoring"):
    return RegulatoryItem(
        info_code=f"IC{code}", security_code=code, security_name=name,
        exchange="上交所", reason_type="连续三个交易日涨幅偏离值累计达100%",
        reason=None, direction="up", start_date=None, end_date=None,
        predict_start=date(2026, 8, 20), predict_end=end,
        notice_date=None, days_remaining=days, status=status, stock=None,
    )


def _approaching(code, name, approach, target):
    return ApproachingItem(
        security_code=code, security_name=name, direction="up", window="10d",
        cum_deviation=88.0, threshold=100.0, approach=approach, coverage=10,
        full_window=True, target_rate=target, rule_label="10日累计100%", stock=None,
    )


def _patch(monkeypatch, *, monitoring=(), ending=(), released=(), approaching=()):
    # 签名带 as_of 了（2026-09-04 修时间穿越）：snapshot_regulatory_status 必须按
    # trade_date 的视角判定，而不是 date.today()
    monkeypatch.setattr(rs, "get_regulatory_watchlist",
                        lambda db, as_of=None: RegulatoryWatchlistResponse(
                            as_of=as_of or D2, monitoring=list(monitoring),
                            ending_soon=list(ending), recently_released=list(released),
                            approaching=list(approaching)))


class TestSnapshotRegulatoryStatus:

    def test_四种状态都落库并保留判定依据(self, db, monkeypatch):
        _patch(monkeypatch,
               monitoring=[_item("600001", "甲", date(2026, 9, 20), 17)],
               ending=[_item("600002", "乙", date(2026, 9, 5), 2)],
               released=[_item("600003", "丙", date(2026, 8, 28), -6)],
               approaching=[_approaching("600004", "丁", 0.88, 3.5)])
        st = rs.snapshot_regulatory_status(db, D2)
        assert st["total"] == 4 and st["added"] == 4

        got = {r.security_code: r for r in db.query(RegulatoryStatusDaily).all()}
        assert got["600001"].status == "MONITORING"
        assert got["600002"].status == "ENDING_SOON"
        assert got["600003"].status == "RELEASED"
        assert got["600004"].status == "APPROACHING"
        # 只存状态不够：日后要复核"为什么那天是 ENDING_SOON"，得看得到依据
        assert got["600002"].predict_end == date(2026, 9, 5)
        assert got["600002"].days_remaining == 2
        assert got["600004"].approach == 0.88 and got["600004"].target_rate == 3.5

    def test_同一天重复跑是覆盖不是追加(self, db, monkeypatch):
        """daily_update 一天跑 2~3 次，盘中那次写当时状态，盘后覆盖成终值。"""
        _patch(monkeypatch, monitoring=[_item("600001", "甲", date(2026, 9, 20), 17)])
        rs.snapshot_regulatory_status(db, D2)
        # 盘后再跑：这只票已经进入「即将解除」
        _patch(monkeypatch, ending=[_item("600001", "甲", date(2026, 9, 5), 2)])
        st = rs.snapshot_regulatory_status(db, D2)
        assert st["added"] == 0 and st["updated"] == 1
        rows = db.query(RegulatoryStatusDaily).all()
        assert len(rows) == 1, "同一天同一只股票不能出现两行"
        assert rows[0].status == "ENDING_SOON" and rows[0].days_remaining == 2

    def test_当天掉出名单的旧行必须删掉(self, db, monkeypatch):
        """留着就等于说"它今天还在监管中"——用陈旧值冒充当前事实。"""
        _patch(monkeypatch, monitoring=[_item("600001", "甲", date(2026, 9, 20), 17),
                                        _item("600002", "乙", date(2026, 9, 20), 17)])
        rs.snapshot_regulatory_status(db, D2)
        _patch(monkeypatch, monitoring=[_item("600001", "甲", date(2026, 9, 20), 17)])
        st = rs.snapshot_regulatory_status(db, D2)
        assert st["removed"] == 1
        assert {r.security_code for r in db.query(RegulatoryStatusDaily).all()} == {"600001"}

    def test_不同日期各自成行(self, db, monkeypatch):
        """相邻两天相减是这张表存在的全部意义，日期必须能分开。"""
        _patch(monkeypatch, approaching=[_approaching("600004", "丁", 0.88, 3.5)])
        rs.snapshot_regulatory_status(db, D1)
        _patch(monkeypatch, monitoring=[_item("600004", "丁", date(2026, 9, 20), 17)])
        rs.snapshot_regulatory_status(db, D2)
        rows = {(r.date, r.status) for r in db.query(RegulatoryStatusDaily).all()}
        assert rows == {(D1, "APPROACHING"), (D2, "MONITORING")}, \
            "APPROACHING→MONITORING 就是「进入监管」事件，两天都得在"

    def test_同时命中多个桶时去重而不是让唯一约束炸掉(self, db, monkeypatch):
        """理论上互斥，但"理论上"不等于"实际上"——撞了也不能拖垮整个步骤。"""
        _patch(monkeypatch,
               monitoring=[_item("600001", "甲", date(2026, 9, 20), 17)],
               released=[_item("600001", "甲", date(2026, 8, 28), -6)])
        st = rs.snapshot_regulatory_status(db, D2)
        assert st["dupes"] == 1 and st["total"] == 1
        assert db.query(RegulatoryStatusDaily).one().status == "MONITORING"


class TestAsOfSemantics:
    """
    2026-09-04 修的时间穿越：snapshot_regulatory_status(db, trade_date) 内部调
    get_regulatory_watchlist(db)，而后者写死 date.today()。用 `--date 2026-08-31`
    补跑时会写出「date=08-31，status 按 09-04 算」——历史行配当前时间语义，
    会静默污染整条监管时间序列，而那条序列正是为「进入/解除/延长」准备的。
    """

    def test_按trade_date的视角判定而不是今天(self, db, monkeypatch):
        seen = {}

        def _wl(db, as_of=None):
            seen["as_of"] = as_of
            return RegulatoryWatchlistResponse(
                as_of=as_of or D1, monitoring=[], ending_soon=[],
                recently_released=[], approaching=[])

        monkeypatch.setattr(rs, "get_regulatory_watchlist", _wl)
        rs.snapshot_regulatory_status(db, D1)
        assert seen["as_of"] == D1, "必须把 trade_date 传下去，不能用 date.today()"

    def test_按历史日期重建时approaching一律为空(self, db, monkeypatch):
        """
        approaching 来自东财实时严重异动预测，**没有历史版本**。
        拿今天的逼近名单冒充过去某天，是"用一个具体值表达不知道"的又一个变体。
        """
        from app.services import deviation_service as ds
        called = []

        def _appr(db, exclude_codes=None):
            called.append(1)
            return []

        monkeypatch.setattr(ds, "get_approaching_regulation", _appr)
        wl = rs.get_regulatory_watchlist(db, as_of=date(2020, 1, 2))
        assert wl.approaching == [] and called == [], \
            "历史日期不该去问实时接口，更不该把结果当成那天的事实"
