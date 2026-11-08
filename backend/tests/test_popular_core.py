"""
人气核心池 / 活跃事件 / 关联响应分类（每日复盘 V2）。

  · 没有全市场快照的日子，区间涨幅来源是 missing，不是「0 只」
  · 涨停次数、连板只在已跟踪股票范围内排，范围要写出来
  · 数据缺了不出事件：今天没快照不判断板，昨天没快照不判再次涨停
  · 冻结是覆盖不是叠加；历史日期现算时不回填当前的「涨停次日均」
"""
from datetime import date, datetime, time as dtime
from types import SimpleNamespace

import pytest

from app.models.large_cap import LargeCapMarketDaily, LargeCapSyncBatch
from app.models.limit_up_detail import LimitUpDailyDetail
from app.models.market_recap import LeaderActivationEvent, MarketPopularCoreDaily
from app.models.stock import Stock, StockDailySnapshot
from app.services import popular_core_service as pc

D1, D2 = date(2026, 11, 5), date(2026, 11, 6)
CAL = [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), D1, D2]


@pytest.fixture(autouse=True)
def no_lifecycle(monkeypatch):
    monkeypatch.setattr(pc, "_lifecycle", lambda db, d: {})


def _stock(db, code, name=None, **kw):
    st = Stock(code=code, name=name or f"股{code}", market="SZ", **kw)
    db.add(st)
    db.flush()
    return st


def _snap(db, st, d, **kw):
    kw.setdefault("is_settled", True)
    db.add(StockDailySnapshot(stock_id=st.id, date=d, **kw))


def _fm(db, d, rows):
    b = LargeCapSyncBatch(trade_date=d, source="eastmoney_clist", started_at=datetime(2026, 11, 6, 16),
                          is_complete=True, is_settled=True, applied=True, status="ok")
    db.add(b)
    db.flush()
    for code, p5, p20, vr in rows:
        db.add(LargeCapMarketDaily(trade_date=d, stock_code=code, stock_name=f"股{code}", total_market_cap=100,
                                   pct_change=1.0, pct_change_5d=p5, pct_change_20d=p20, volume_ratio=vr,
                                   is_st=False, source="eastmoney_clist", batch_id=b.id))


class TestRank:
    def test_并列同名次_下一个跳号(self):
        assert pc.competition_rank({"a": 9, "b": 7, "c": 7, "d": 5}, 10) == {"a": 1, "b": 2, "c": 2, "d": 4}

    def test_只留前N_空和非正数不参与(self):
        assert pc.competition_rank({"a": 9, "b": None, "c": 0, "d": 5, "e": -3}, 1) == {"a": 1}

    def test_板块(self):
        assert (pc.board_of("688981"), pc.board_of("300750"), pc.board_of("600519")) == ("科创板", "创业板", "主板")


class TestSources:
    def test_没有全市场快照_区间涨幅是missing不是0只(self, db):
        st = _stock(db, "002086")
        _snap(db, st, D2, limit_up_days_10d=7, board_count=2, is_limit_up=True, board_count_60d=4)
        db.commit()
        r = pc.compute(db, D2, CAL)
        assert r["sources"]["gain_20"]["status"] == "missing" and "全市场收盘快照" in r["sources"]["gain_20"]["note"]
        assert r["sources"]["lu_10"]["scope"] == pc.SCOPE_TRACKED
        it = r["items"]["002086"]
        assert it["pct_change_20d"] is None and it["rank_20d"] is None, "缺就是缺，不拿已跟踪口径的相加涨幅顶上"
        assert {s["source"] for s in it["selection_sources"]} == {"lu_10", "board", "high_board"}

    def test_全市场涨幅排名(self, db):
        _fm(db, D2, [("A", 30.0, 80.0, 1.0), ("B", 20.0, None, 1.0), ("C", -5.0, 10.0, 1.0)])
        db.commit()
        r = pc.compute(db, D2, CAL)
        assert r["items"]["A"]["rank_20d"] == 1 and r["items"]["B"]["rank_5d"] == 2
        assert r["items"]["B"]["rank_20d"] is None, "新股被置空的区间涨幅不参与排名"
        assert "C" not in r["items"] or r["items"]["C"]["rank_5d"] is None, "负的不算核心"

    def test_同一只多个来源只算一只(self, db):
        _fm(db, D2, [("002086", 30.0, 80.0, 1.0)])
        st = _stock(db, "002086")
        _snap(db, st, D2, limit_up_days_10d=7)
        db.commit()
        r = pc.compute(db, D2, CAL)
        assert list(r["items"]) == ["002086"]
        assert len(r["items"]["002086"]["selection_sources"]) >= 3


class TestEvents:
    def test_再次涨停要昨天没涨停且有过高标记录(self, db):
        st = _stock(db, "002086")
        _snap(db, st, D1, is_limit_up=False, board_count=0, board_count_60d=6, limit_up_days_10d=3)
        _snap(db, st, D2, is_limit_up=True, board_count=1, board_count_60d=6, limit_up_days_10d=4)
        db.add(LimitUpDailyDetail(stock_id=st.id, stock_code="002086", stock_name="东方海洋", trade_date=D2,
                                  first_limit_time=dtime(9, 42)))
        db.commit()
        ev = {e["event_type"]: e for e in pc.compute(db, D2, CAL)["events"]}
        assert {"limit_up", "relimit"} <= set(ev)
        assert ev["limit_up"]["event_time"] == dtime(9, 42)

    def test_昨天没快照不判再次涨停(self, db):
        st = _stock(db, "002086")
        _snap(db, st, D2, is_limit_up=True, board_count=1, board_count_60d=6, limit_up_days_10d=4)
        db.commit()
        ev = {e["event_type"] for e in pc.compute(db, D2, CAL)["events"]}
        assert "limit_up" in ev and "relimit" not in ev

    def test_今天没快照不出假断板(self, db):
        st = _stock(db, "002086")
        _snap(db, st, D1, is_limit_up=True, board_count=4, board_count_60d=4, limit_up_days_10d=4)
        _snap(db, _stock(db, "600001"), D2, limit_up_days_10d=1)
        db.commit()
        assert pc.compute(db, D2, CAL)["events"] == []

    def test_断板与连板延续(self, db):
        a, b = _stock(db, "000001"), _stock(db, "000002")
        _snap(db, a, D1, is_limit_up=True, board_count=3, board_count_60d=3, limit_up_days_10d=3)
        _snap(db, a, D2, is_limit_up=False, board_count=0, board_count_60d=3, limit_up_days_10d=3, pct_change=-4.0)
        _snap(db, b, D1, is_limit_up=True, board_count=2, board_count_60d=2, limit_up_days_10d=2)
        _snap(db, b, D2, is_limit_up=True, board_count=3, board_count_60d=3, limit_up_days_10d=3)
        db.commit()
        ev = {(e["leader_code"], e["event_type"]) for e in pc.compute(db, D2, CAL)["events"]}
        assert ("000001", "streak_break") in ev and ("000002", "streak_continue") in ev

    def test_修复启动来自生命周期转移(self, db, monkeypatch):
        st = _stock(db, "002086")
        _snap(db, st, D2, limit_up_days_10d=3)
        db.commit()
        monkeypatch.setattr(pc, "_lifecycle", lambda db, d: {"002086": SimpleNamespace(
            code="002086", name="东方海洋", transitioned_today=True, previous_lifecycle_state="BROKEN",
            lifecycle_state="REPAIRING")})
        r = pc.compute(db, D2, CAL)
        assert any(e["event_type"] == "repair_start" for e in r["events"])
        assert "lifecycle_restart" in {s["source"] for s in r["items"]["002086"]["selection_sources"]}


class TestFreeze:
    def test_冻结是覆盖_读的是冻结那份(self, db):
        st = _stock(db, "002086", limit_up_next_avg_pct=4.2, limit_up_next_samples=12)
        _snap(db, st, D2, is_limit_up=True, board_count=2, board_count_60d=4, limit_up_days_10d=7)
        db.commit()
        pc.freeze(db, D2, CAL, latest_snapshot_date=D2)
        pc.freeze(db, D2, CAL, latest_snapshot_date=D2)
        assert db.query(MarketPopularCoreDaily).count() == 1
        assert db.query(LeaderActivationEvent).filter_by(event_type="limit_up").count() == 1
        got = pc.load(db, D2, CAL, D2)
        assert got["frozen"] is True and got["items"][0]["limit_up_next_samples"] == 12

    def test_历史日期现算不回填当前的涨停次日均(self, db):
        st = _stock(db, "002086", limit_up_next_avg_pct=4.2, limit_up_next_samples=12)
        _snap(db, st, D1, limit_up_days_10d=7)
        db.commit()
        got = pc.load(db, D1, CAL, latest_snapshot_date=D2)
        assert got["frozen"] is False and got["items"][0]["limit_up_next_avg_pct"] is None


class TestResponse:
    @pytest.mark.parametrize("rel,leader,events,expected", [
        (None, {"pct": 5}, [], "insufficient"),
        ({"status": "missing", "pct": None}, {"pct": 5}, [], "insufficient"),
        ({"status": "ok", "pct": 10, "is_limit_up": True}, {"pct": 5}, [], "limit_up"),
        ({"status": "ok", "pct": 6, "is_limit_up": False}, {"pct": 5}, [], "up_response"),
        ({"status": "ok", "pct": 6, "is_limit_up": False}, {"pct": -4}, [], "independent_strong"),
        ({"status": "ok", "pct": 6, "is_limit_up": False}, {"pct": 1}, ["streak_break"], "independent_strong"),
        ({"status": "ok", "pct": 2, "is_limit_up": False}, {"pct": 5}, [], "not_met"),
    ])
    def test_分类(self, rel, leader, events, expected):
        assert pc.classify_response(rel, leader, events) == expected

    def test_观察视角不互斥(self):
        item = {"stock_code": "A", "selection_sources": [{"source": "gain_60"}], "lifecycle_state": "REPAIRING"}
        assert set(pc.view_of(item, {"A"}, {"A"})) == {"all", "active", "historical", "repair", "mine"}
