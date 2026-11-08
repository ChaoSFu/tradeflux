"""
每日盘面复盘：锁定只插不改、盘前 / 事后分得清、没有分时证据不判领先跟随、名称联想只能是 C、
观察池跨日保留、事实按指定交易日取不拿最新顶替、一个模块挂了不拖垮整页、按用户隔离。
"""
from datetime import date, datetime, time as dtime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.limit_up_detail import LimitUpDailyDetail
from app.models.market_index import IndexDailySnapshot
from app.models.market_recap import RecapLockedVersion
from app.models.stock import Stock, StockDailySnapshot
from app.services import market_recap_service as ctx
from app.services import market_recap_store as store
from app.services.market_recap_store import RecapError

D1, D2, D3 = date(2026, 11, 4), date(2026, 11, 5), date(2026, 11, 6)
CAL = [D1, D2, D3]
U = "chao"


def _plan(code="600001", **kw):
    return {"stock_code": code, "stock_name": "测试", "entry_trigger": "回封且板块领涨",
            "invalidation": "跌破 10.2", "scenario": "A", "planned_stop": 10.2, "risk_budget": 1.0, **kw}


@pytest.fixture
def cal(db):
    from app.services.trading_calendar import _write_cache
    _write_cache(db, CAL)
    return CAL


# ── 草稿 ────────────────────────────────────────────────────────────────────

class TestDraft:
    def test_保存与读取(self, db):
        store.save_recap(db, U, D3, {"market_posture": "probe", "invalidation": ["沪指跌破 MA20", " "],
                                     "scenarios": {"A": {"trigger": "高标回封"}}, "plans": [_plan()]})
        g = store.get_recap(db, U, D3)
        assert g["recap"]["market_posture"] == "probe"
        assert g["recap"]["invalidation"] == ["沪指跌破 MA20"], "空条件不存"
        assert g["recap"]["scenarios"]["A"]["trigger"] == "高标回封"
        assert g["recap"]["scenarios"]["B"] == {k: None for k in store.SCENARIO_FIELDS["B"]}, "三个情景分开存"
        assert len(g["plans"]) == 1

    def test_最多5只(self, db):
        with pytest.raises(RecapError, match="最多 5"):
            store.save_recap(db, U, D3, {"plans": [_plan(f"60000{i}") for i in range(6)]})

    def test_入场和失效条件都要写(self, db):
        with pytest.raises(RecapError, match="入场触发和失效条件"):
            store.save_recap(db, U, D3, {"plans": [_plan(invalidation="")]})

    def test_空仓预案不能再有候选股(self, db):
        with pytest.raises(RecapError, match="空仓"):
            store.save_recap(db, U, D3, {"flat_plan": True, "plans": [_plan()]})

    def test_姿态只能是四种(self, db):
        with pytest.raises(RecapError):
            store.save_recap(db, U, D3, {"market_posture": "梭哈"})

    def test_重存是覆盖不是追加(self, db):
        store.save_recap(db, U, D3, {"plans": [_plan("600001"), _plan("600002")]})
        store.save_recap(db, U, D3, {"plans": [_plan("600003")]})
        assert [p["stock_code"] for p in store.get_recap(db, U, D3)["plans"]] == ["600003"]


# ── 观察池 ──────────────────────────────────────────────────────────────────

class TestWatchPool:
    def test_跨日保留直到主动取消(self, db):
        store.add_watch(db, U, "600001", D1, name="东方海洋")
        assert [w.stock_code for w in store.watch_pool(db, U, D3)] == ["600001"], "第二天还在"
        store.remove_watch(db, U, "600001", D3)
        assert store.watch_pool(db, U, D3) == []
        assert [w.stock_code for w in store.watch_pool(db, U, D2)] == ["600001"], "之前那些天照样查得到"

    def test_按用户隔离(self, db):
        store.add_watch(db, "alice", "600001", D1)
        assert store.watch_pool(db, U, D3) == []


# ── 关联 ────────────────────────────────────────────────────────────────────

def _rel(**kw):
    return {"leader_code": "002086", "leader_name": "东方海洋", "attribute_type": "name",
            "attribute_value": "东方", "related_code": "600278", "related_name": "东方创业", **kw}


class TestRelations:
    def test_名称联想只能是C(self, db):
        r = store.upsert_relation(db, U, _rel(relation_basis="A"), D3)
        assert r.relation_basis == "C", "依据性质由类型决定，名称联想不能被标成产业关联"

    def test_类型决定依据(self, db):
        assert store.upsert_relation(db, U, _rel(attribute_type="industry", attribute_value="海洋渔业"), D3) \
            .relation_basis == "A"
        assert store.upsert_relation(db, U, _rel(attribute_type="concept", attribute_value="农业"), D3) \
            .relation_basis == "B"

    def test_跨日继承_同一条不重复建(self, db):
        a = store.upsert_relation(db, U, _rel(), D1)
        b = store.upsert_relation(db, U, _rel(note="再看一眼"), D3)
        assert a.id == b.id and b.first_recorded == D1

    def test_只输代码会补上名字(self, db):
        db.add(Stock(code="600278", name="东方创业", market="SH"))
        db.commit()
        r = store.upsert_relation(db, U, _rel(related_name=None), D3)
        assert r.related_name == "东方创业"

    def test_不能关联自己(self, db):
        with pytest.raises(RecapError):
            store.upsert_relation(db, U, _rel(related_code="002086"), D3)

    def test_删除是软删(self, db):
        r = store.upsert_relation(db, U, _rel(), D3)
        store.remove_relation(db, U, r.id)
        assert store.relations_for(db, U, ["002086"]) == []


def _limit_up(db, code, d, t):
    st = db.query(Stock).filter_by(code=code).first()
    if st is None:
        st = Stock(code=code, name=f"股{code}", market="SZ")
        db.add(st)
        db.flush()
    db.add(LimitUpDailyDetail(stock_id=st.id, stock_code=code, stock_name=st.name, trade_date=d,
                              first_limit_time=t, pct_change=10.0, board_count=1))
    db.commit()


class TestResponses:
    def test_没有分时证据不能判领先跟随(self, db):
        r = store.upsert_relation(db, U, _rel(), D3)
        with pytest.raises(RecapError, match="首封时间"):
            store.upsert_response(db, U, D3, r.id, "leading")
        assert store.upsert_response(db, U, D3, r.id, "no_response").response_status == "no_response"

    def test_两边都有首封时间才能判(self, db):
        r = store.upsert_relation(db, U, _rel(), D3)
        _limit_up(db, "002086", D3, dtime(9, 31))
        _limit_up(db, "600278", D3, dtime(10, 5))
        resp = store.upsert_response(db, U, D3, r.id, "lagging")
        assert resp.leader_trigger_time == dtime(9, 31) and resp.first_trigger_time == dtime(10, 5)
        assert resp.limit_up is True

    def test_响应按天记_昨天的不延续成今天的(self, db):
        r = store.upsert_relation(db, U, _rel(), D2)
        store.upsert_response(db, U, D2, r.id, "no_response")
        assert store.responses_on(db, U, D3, [r.id]) == {}


# ── 锁定与验证 ──────────────────────────────────────────────────────────────

class TestLock:
    def test_锁定只插不改(self, db):
        store.save_recap(db, U, D2, {"market_posture": "probe", "plans": [_plan()]})
        v1 = store.lock(db, U, D2, CAL, datetime(2026, 11, 5, 20, 0))
        store.save_recap(db, U, D2, {"market_posture": "defend", "plans": [_plan(entry_trigger="改过的条件")]})
        v2 = store.lock(db, U, D2, CAL, datetime(2026, 11, 5, 21, 0))
        assert (v1.version, v2.version) == (1, 2)
        first = db.query(RecapLockedVersion).filter_by(version=1).one()
        assert first.payload["recap"]["market_posture"] == "probe"
        assert first.payload["plans"][0]["entry_trigger"] == "回封且板块领涨", "第一版原样留着"

    def test_没有候选也没选空仓_不让锁(self, db):
        store.save_recap(db, U, D2, {"market_posture": "probe"})
        with pytest.raises(RecapError, match="空仓"):
            store.lock(db, U, D2, CAL, datetime(2026, 11, 5, 20, 0))

    def test_空仓预案可以锁(self, db):
        store.save_recap(db, U, D2, {"flat_plan": True})
        assert store.lock(db, U, D2, CAL, datetime(2026, 11, 5, 20, 0)).version == 1

    def test_开盘后锁的算事后记录(self, db):
        store.save_recap(db, U, D2, {"plans": [_plan()]})
        store.lock(db, U, D2, CAL, datetime(2026, 11, 5, 20, 0))
        store.lock(db, U, D2, CAL, datetime(2026, 11, 6, 10, 0))
        vs = store.locked_versions(db, U, D2, CAL)
        assert [v["pre_open"] for v in vs] == [True, False]
        assert vs[0]["target_trade_date"] == D3

    def test_验证用最后一份盘前版本_不用事后改的(self, db):
        st = Stock(code="600001", name="测试", market="SH")
        db.add(st)
        db.flush()
        db.add(StockDailySnapshot(stock_id=st.id, date=D3, close_price=10.0, pct_change=-3.0,
                                  is_settled=True))
        db.commit()
        store.save_recap(db, U, D2, {"plans": [_plan()]})
        store.lock(db, U, D2, CAL, datetime(2026, 11, 5, 20, 0))
        store.save_recap(db, U, D2, {"plans": [_plan(entry_trigger="事后改的")]})
        store.lock(db, U, D2, CAL, datetime(2026, 11, 6, 15, 30))
        v = store.verification(db, U, D2, CAL, datetime(2026, 11, 6, 18, 0))
        assert v["status"] == "ok" and v["version"]["version"] == 1 and v["backfilled"] is False
        row = v["plans"][0]
        assert row["entry_trigger"] == "回封且板块领涨"
        assert row["actual"]["pct"] == -3.0 and row["below_stop"] is True

    def test_只有事后版本时标明事后(self, db):
        store.save_recap(db, U, D2, {"plans": [_plan()]})
        store.lock(db, U, D2, CAL, datetime(2026, 11, 6, 15, 30))
        v = store.verification(db, U, D2, CAL, datetime(2026, 11, 6, 18, 0))
        assert v["backfilled"] is True

    def test_目标日没收盘就等(self, db):
        store.save_recap(db, U, D2, {"plans": [_plan()]})
        store.lock(db, U, D2, CAL, datetime(2026, 11, 5, 20, 0))
        assert store.verification(db, U, D2, CAL, datetime(2026, 11, 6, 11, 0))["status"] == "pending"

    def test_日历没覆盖目标日_说清楚原因(self, db):
        store.save_recap(db, U, D3, {"plans": [_plan()]})
        store.lock(db, U, D3, CAL, datetime(2026, 11, 6, 20, 0))
        v = store.verification(db, U, D3, CAL, datetime(2026, 11, 10, 18, 0))
        assert v["status"] == "pending" and "交易日历" in v["message"]

    def test_目标日还不在日历里就是推测的(self, db):
        target, provisional = store.next_trading_day(CAL, D3)
        assert provisional is True and target == date(2026, 11, 9), "周五的下一个是周一"


# ── 市场事实 ────────────────────────────────────────────────────────────────

def _index_bars(db, code, days, start=3000.0):
    for i, d in enumerate(days):
        db.add(IndexDailySnapshot(index_code=code, date=d, close=start + i, pct_change=0.1))
    db.commit()


class TestContext:
    def test_未收盘不出复盘(self, db, cal):
        out = ctx.build_context(db, D3, now=datetime(2026, 11, 6, 11, 0))
        assert out["status"] == "not_settled"

    def test_非交易日(self, db, cal):
        assert ctx.build_context(db, date(2026, 11, 7), now=datetime(2026, 11, 8))["status"] == "not_trading_day"

    def test_一个模块挂了不拖垮整页(self, db, cal):
        """测试库没有 market_breadth_daily（JSONB 建不了），宽度这一块必挂，其它照常。"""
        out = ctx.build_context(db, D3, now=datetime(2026, 11, 6, 18, 0))
        assert out["status"] == "ok"
        assert out["breadth"]["meta"]["status"] == "error"
        assert out["indices"]["meta"]["status"] in ("missing", "partial", "ok")

    def test_指数只读到这一天_不拿之后的(self, db, cal):
        import datetime as _dt
        days = [D3 - _dt.timedelta(days=80 - i) for i in range(81)] + [date(2026, 11, 9)]
        _index_bars(db, "000001", days)
        m = ctx.index_module(db, D3)
        sh = next(x for x in m["items"] if x["code"] == "000001")
        assert sh["close"] == 3080.0, "11-09 那根不能混进 11-06 的复盘"
        assert sh["ma60"] is not None and sh["arrangement"] == "多头" and sh["ma20_dir"] == "up"
        assert next(x for x in m["items"] if x["code"] == "399001")["status"] == "missing"
        assert m["meta"]["status"] == "partial"

    def test_候选板块优先用当天口径(self, db, cal):
        st = Stock(code="002086", name="东方海洋", market="SZ", primary_sector_name="当前主板块")
        db.add(st)
        db.flush()
        db.add(StockDailySnapshot(stock_id=st.id, date=D3, pct_change=10.0, is_limit_up=True,
                                  board_count=4, is_settled=True))
        db.add(LimitUpDailyDetail(stock_id=st.id, stock_code="002086", stock_name="东方海洋",
                                  trade_date=D3, em_industry="渔业", limit_stat_days=4, limit_stat_count=4))
        db.commit()
        m = ctx.candidate_module(db, D3)
        it = next(x for x in m["items"] if x["code"] == "002086")
        assert it["sector"] == "渔业" and it["sector_basis"] == "当日涨停明细"
        assert "board" in it["groups"] and it["board_count"] == 4
        assert "interval" not in it["groups"], "4天4板是连续连板，不进 N天M板 那一组"

    def test_快照里默认的0不当20日涨幅(self, db, cal):
        st = Stock(code="600229", name="城市传媒", market="SH")
        db.add(st)
        db.flush()
        db.add(StockDailySnapshot(stock_id=st.id, date=D3, pct_change=3.16, is_settled=True))
        db.commit()
        f = ctx.stock_facts(db, ["600229"], D3)["600229"]
        assert f["pct"] == 3.16 and f["pct_20d"] is None

    def test_个股事实_没有就是missing(self, db, cal):
        f = ctx.stock_facts(db, ["600999"], D3)["600999"]
        assert f["status"] == "missing" and f["pct"] is None and f["is_limit_up"] is None

    def test_名称联想是客观字符串匹配(self, db):
        db.add_all([Stock(code="600278", name="东方创业", market="SH"),
                    Stock(code="600999", name="招商证券", market="SH")])
        db.commit()
        s = ctx.name_suggestions(db, "002086", "东方海洋", D3)
        assert [x["related_code"] for x in s] == ["600278"]
        assert s[0]["attribute_type"] == "name" and "东方" in s[0]["evidence"]


class TestApiAuth:
    def test_全部要登录(self, db):
        from app.database import get_db
        from app.routers import market_recap
        app = FastAPI()
        app.include_router(market_recap.router)
        app.dependency_overrides[get_db] = lambda: db
        c = TestClient(app)
        for method, url in (("get", "/market-recap/dates"), ("get", "/market-recap/2026-11-06"),
                            ("get", "/market-recap/context?date=2026-11-06"),
                            ("put", "/market-recap/2026-11-06"), ("post", "/market-recap/2026-11-06/lock"),
                            ("get", "/market-recap/leader-relations/002086?date=2026-11-06")):
            r = getattr(c, method)(url, **({"json": {}} if method in ("put", "post") else {}))
            assert r.status_code in (401, 403), f"{method} {url} 没登录也能访问"
