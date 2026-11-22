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

    def test_撤销取消观察_加入日期和星标都是原来的(self, db):
        store.add_watch(db, U, "600001", D1, name="东方海洋", deep=True, note="主线")
        store.remove_watch(db, U, "600001", D3)
        assert store.restore_watch(db, U, "600001", D3)
        w = store.watch_pool(db, U, D3)
        assert [(x.stock_code, x.added_date, x.deep, x.note) for x in w] == [("600001", D1, True, "主线")], \
            "不是重新加一行：加入日期还是 D1"
        assert not store.restore_watch(db, U, "600001", D3), "已经在池里就不动"

    def test_只撤销那一天的取消(self, db):
        store.add_watch(db, U, "600001", D1)
        store.remove_watch(db, U, "600001", D2)
        assert not store.restore_watch(db, U, "600001", D3), "D3 没取消过它"
        store.add_watch(db, U, "600001", D3)
        assert not store.restore_watch(db, U, "600001", D2), "之后又加回来过，再撤销就成两行了"
        assert len(store.watch_pool(db, U, D3)) == 1

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
        assert f["limit_up_days_60d"] is None

    def test_近60日涨停天数取当天快照_不拿股票表当前值顶(self, db, cal):
        st = Stock(code="600229", name="城市传媒", market="SH", limit_up_days_60d=9)
        db.add(st)
        db.flush()
        db.add(StockDailySnapshot(stock_id=st.id, date=D2, pct_change=1.0, limit_up_days_60d=4))
        db.commit()
        assert ctx.stock_facts(db, ["600229"], D2)["600229"]["limit_up_days_60d"] == 4
        assert ctx.stock_facts(db, ["600229"], D3)["600229"]["limit_up_days_60d"] is None, "D3 没快照：不知道，不是 9"

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
                            ("get", "/market-recap/leader-relations/002086?date=2026-11-06"),
                            ("post", "/market-recap/watch/600001/restore?date=2026-11-06")):
            r = getattr(c, method)(url, **({"json": {}} if method in ("put", "post") else {}))
            assert r.status_code in (401, 403), f"{method} {url} 没登录也能访问"


class TestV2:
    def test_三个观察点上限(self, db):
        store.save_recap(db, U, D3, {"key_points": ["一", "二", "三"]})
        with pytest.raises(RecapError, match="最多 3"):
            store.save_recap(db, U, D3, {"key_points": ["一", "二", "三", "四"]})

    def test_按高标分别存情景_旧字段不丢(self, db):
        store.save_recap(db, U, D3, {"scenarios": {"A": {"trigger": "全局"}, "by_leader": {
            "002086": {"A": {"weak_resonance": "关联股不跟"}}, "bad": {"A": {"trigger": "x"}}}}})
        sc = store.get_recap(db, U, D3)["recap"]["scenarios"]
        assert sc["A"]["trigger"] == "全局"
        assert sc["by_leader"]["002086"]["A"]["weak_resonance"] == "关联股不跟"
        assert "bad" not in sc["by_leader"]

    def test_预案的关联依据和观察触发(self, db):
        store.save_recap(db, U, D3, {"plans": [_plan(relation_note="同属农业概念", watch_trigger="东方海洋回封")]})
        p = store.get_recap(db, U, D3)["plans"][0]
        assert p["relation_note"] == "同属农业概念" and p["watch_trigger"] == "东方海洋回封"

    def test_批量忽略就是记成不成立_默认不返回(self, db):
        store.upsert_relation(db, U, _rel(confidence_status="rejected"), D3)
        assert store.relations_for(db, U, ["002086"]) == []
        assert len(store.relations_for(db, U, ["002086"], include_rejected=True)) == 1

    def test_锁定带上当时的核心池_次日准备一屏(self, db):
        store.add_watch(db, U, "002086", D2, name="东方海洋", deep=True)
        store.save_recap(db, U, D2, {"market_posture": "probe", "key_points": ["看回封"], "plans": [_plan(leader_code="002086")]})
        cores = {"items": [{"stock_code": "002086", "selection_sources": [{"source": "lu_10"}]}],
                 "events": [{"leader_code": "002086", "event_type": "limit_up"}]}
        store.lock(db, U, D2, CAL, datetime(2026, 11, 5, 20, 0), cores=cores)
        p = store.prepare(db, U, D2, CAL)
        assert p["status"] == "ok" and p["pre_open"] is True and p["posture"] == "probe"
        assert p["key_points"] == ["看回封"] and [w["code"] for w in p["focus_leaders"]] == ["002086"]
        assert p["events"][0]["event_type"] == "limit_up"
        first = db.query(RecapLockedVersion).one()
        assert first.payload["cores"]["items"][0]["stock_code"] == "002086"


class TestApiSave:
    def test_三个观察点经接口能存下来(self, db, cal):
        """接口的请求模型漏了字段，Pydantic 会静默丢掉——本机走查时 key_points 就这么没了。"""
        from app.auth import require_auth
        from app.database import get_db
        from app.routers import market_recap
        app = FastAPI()
        app.include_router(market_recap.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_auth] = lambda: U
        c = TestClient(app)
        body = {"key_points": ["看回封"], "plans": [_plan(relation_note="同概念", watch_trigger="高标回封")],
                "scenarios": {"by_leader": {"002086": {"A": {"trigger": "x"}}}}}
        assert c.put("/market-recap/2026-11-06", json=body).status_code == 200
        g = store.get_recap(db, U, D3)
        assert g["recap"]["key_points"] == ["看回封"]
        assert g["plans"][0]["watch_trigger"] == "高标回封"
        assert g["recap"]["scenarios"]["by_leader"]["002086"]["A"]["trigger"] == "x"


class TestSectorPeers:
    """高标所在每个板块里当天的活跃股：强势池 / 涨停 / 跌停，按当天算。"""

    def _seed(self, db):
        from app.models.leader_cycle import LeaderCycleSnapshot
        from app.models.sector import Sector, StockSectorRelation
        ind = Sector(code="BK1", name="家用电器", sector_type="industry", is_watched=True)
        con = Sector(code="BK2", name="智能家居", sector_type="concept", is_watched=True)
        hidden = Sector(code="BK3", name="没关注的", sector_type="concept", is_watched=False)
        db.add_all([ind, con, hidden])
        db.flush()
        st = {c: Stock(code=c, name=f"股{c}", market="SZ") for c in ("002242", "000001", "000002", "000003", "000004")}
        db.add_all(st.values())
        db.flush()
        st["002242"].primary_sector_id = ind.id
        rel = [("002242", ind), ("002242", con), ("002242", hidden),
               ("000001", ind), ("000001", con), ("000002", con), ("000003", ind), ("000004", hidden)]
        for c, s in rel:
            db.add(StockSectorRelation(stock_id=st[c].id, sector_id=s.id))
        # 000001 强势池（D3），000002 当天涨停，000003 只在 D2 是强势池（D3 不活跃），000004 在没关注的板块
        db.add(LeaderCycleSnapshot(date=D3, stock_id=st["000001"].id, stock_code="000001"))
        db.add(LeaderCycleSnapshot(date=D2, stock_id=st["000003"].id, stock_code="000003"))
        db.add(LimitUpDailyDetail(stock_id=st["000002"].id, stock_code="000002", stock_name="股000002",
                                  trade_date=D3, first_limit_time=dtime(10, 1), pct_change=10.0, board_count=2))
        db.add(LimitUpDailyDetail(stock_id=st["002242"].id, stock_code="002242", stock_name="九阳股份",
                                  trade_date=D3, first_limit_time=dtime(9, 40), pct_change=10.0, board_count=1))
        db.add(LimitUpDailyDetail(stock_id=st["000004"].id, stock_code="000004", stock_name="股000004",
                                  trade_date=D3, pct_change=10.0))
        db.commit()

    def test_每个板块各列当天活跃股(self, db):
        self._seed(db)
        r = ctx.sector_peers(db, "002242", D3)
        groups = {g["sector"]["name"]: g for g in r["groups"]}
        assert list(groups) == ["智能家居", "家用电器"], "涨停家数多的板块在前（哪怕不是主板块）；没关注的板块不出现"
        assert groups["家用电器"]["sector"]["primary"] is True
        assert [s["code"] for s in groups["家用电器"]["stocks"]] == ["000001"], "000003 只在前一天活跃"
        assert [s["code"] for s in groups["智能家居"]["stocks"]] == ["000002", "000001"], "涨停的排前面"
        one = next(s for s in groups["智能家居"]["stocks"] if s["code"] == "000001")
        assert one["also_in"] == ["家用电器"] and one["active"] == ["strong"]
        assert all(s["code"] != "002242" for g in r["groups"] for s in g["stocks"]), "不列高标自己"
        assert r["peers_total"] == 2

    def test_板块按涨停家数再按活跃家数排(self, db):
        from app.models.leader_cycle import LeaderCycleSnapshot
        from app.models.sector import Sector, StockSectorRelation
        # 板块名 → (类型, 当天涨停的成员, 只在强势池的成员)
        spec = {"主板块甲": ("industry", ["L1"], []),
                "概念乙": ("concept", ["L2"], ["S1", "S2"]),
                "概念丙": ("concept", ["L3", "L4"], []),
                "概念丁": ("concept", [], []),
                "概念戊": ("concept", ["L5"], [])}
        secs = {n: Sector(code=f"BK{i}", name=n, sector_type=t, is_watched=True)
                for i, (n, (t, _, _)) in enumerate(spec.items())}
        db.add_all(secs.values())
        lead = Stock(code="600000", name="高标", market="SH")
        codes = sorted({c for _, lu, sp in spec.values() for c in lu + sp})
        st = {c: Stock(code=c, name=f"股{c}", market="SZ") for c in codes}
        db.add_all([lead, *st.values()])
        db.flush()
        lead.primary_sector_id = secs["主板块甲"].id
        for n, (_, lu, sp) in spec.items():
            db.add(StockSectorRelation(stock_id=lead.id, sector_id=secs[n].id))
            for c in lu + sp:
                db.add(StockSectorRelation(stock_id=st[c].id, sector_id=secs[n].id))
            for c in lu:
                db.add(LimitUpDailyDetail(stock_id=st[c].id, stock_code=c, stock_name=c,
                                          trade_date=D3, pct_change=10.0))
            for c in sp:
                db.add(LeaderCycleSnapshot(date=D3, stock_id=st[c].id, stock_code=c))
        db.commit()
        r = ctx.sector_peers(db, "600000", D3)
        assert [g["sector"]["name"] for g in r["groups"]] == ["概念丙", "概念乙", "主板块甲", "概念戊", "概念丁"], \
            "涨停 2 > 涨停 1（活跃 3 > 活跃 1）；涨停、活跃都一样时主板块在前；没有活跃股的垫底"

    def test_近N日涨停天数_跟当天涨停同一判法_说不准就是None(self, db):
        days = [date(2026, 11, d) for d in (2, 3, 4, 5, 6)]
        st = {c: Stock(code=c, name=c, market="SZ") for c in ("000001", "000002", "000003")}
        db.add_all(st.values())
        db.flush()
        for d in days[:4]:      # 11-06 没有涨停明细
            db.add(LimitUpDailyDetail(stock_id=st["000003"].id, stock_code="000003", stock_name="x",
                                      trade_date=d, pct_change=10.0))
        db.add(LimitUpDailyDetail(stock_id=st["000001"].id, stock_code="000001", stock_name="x",
                                  trade_date=days[1], pct_change=10.0))
        # 11-06 没明细：000001 有快照（涨停），000002 什么都没有
        db.add(StockDailySnapshot(stock_id=st["000001"].id, date=days[4], pct_change=10.0, is_limit_up=True))
        db.add(StockDailySnapshot(stock_id=st["000003"].id, date=days[4], pct_change=-2.0, is_limit_up=False))
        db.commit()
        got = ctx.limit_up_day_counts(db, ["000001", "000002", "000003"], days)
        assert got == {"000001": 2, "000002": None, "000003": 4}
        assert ctx.limit_up_day_counts(db, ["000002"], days[:4]) == {"000002": 0}, "明细全市场：不在里面就是没涨停"

    def test_没有板块归属要说清楚(self, db):
        db.add(Stock(code="600001", name="孤零零", market="SH"))
        db.commit()
        r = ctx.sector_peers(db, "600001", D3)
        assert r["status"] == "missing" and r["groups"] == [] and "手动录入" in r["note"]


class TestSectorPicks:
    """主线板块归类：手动加进列表的板块可以先不归类；板块趋势没列进主线的关注板块也给事实。"""

    def test_手动加的可以先不归类_其余不归类就报错(self, db):
        store.save_recap(db, U, D3, {"sector_picks": [
            {"code": "BK1", "name": "芯片概念", "tier": None, "manual": True},
            {"code": "BK2", "name": "影视概念", "tier": "drop", "state_label": "转弱"},
            {"code": "BK1", "name": "芯片概念", "tier": "core", "manual": True},     # 重复的只留第一条
        ]})
        picks = store.get_recap(db, U, D3)["recap"]["sector_picks"]
        assert picks == [{"code": "BK1", "name": "芯片概念", "tier": None, "state_label": None, "manual": True},
                         {"code": "BK2", "name": "影视概念", "tier": "drop", "state_label": "转弱", "manual": False}]
        s = store.summary(db, U, D3)["core"]
        assert s["core_sectors"] == [] and s["secondary_sectors"] == [], "没归类的不算核心 / 次级"
        with pytest.raises(RecapError, match="板块归类"):
            store.save_recap(db, U, D3, {"sector_picks": [{"code": "BK3", "name": "x", "tier": None}]})

    def test_没列进主线的关注板块放在_others(self, db, monkeypatch):
        from app.services import sector_mainline_service as sms

        def sec(code, state):
            return {"code": code, "name": f"板块{code}", "state": state, "state_label": state, "state_reason": "r",
                    "trail": [], "facts": {k: None for k in ("pct", "r5", "rs20", "lu", "height", "lu_trend")}}
        monkeypatch.setattr(sms, "get_sector_mainline_state", lambda db, as_of=None: {
            "state_date": D3.isoformat(), "counts": {}, "notes": [],
            "sectors": [sec("BK1", "MAIN"), sec("BK2", "NONE"), sec("BK3", "UNKNOWN")]})
        m = ctx.sector_module(db, D3)
        assert [s["code"] for s in m["items"]] == ["BK1"]
        assert [s["code"] for s in m["others"]] == ["BK2", "BK3"]


class TestPoolSectorLink:
    """重点观察池里同属一个关注板块的两只，自动互相记为关联，不用一只只点「记为关联」。"""

    A, B, C = "002148", "002685", "000678"

    def _seed(self, db):
        from app.models.sector import Sector, StockSectorRelation
        chip = Sector(code="BK1", name="芯片概念", sector_type="concept", is_watched=True)
        comm = Sector(code="BK2", name="通信设备", sector_type="industry", is_watched=True)
        margin = Sector(code="BK3", name="融资融券", sector_type="concept", is_watched=False)
        db.add_all([chip, comm, margin])
        st = {c: Stock(code=c, name=n, market="SZ")
              for c, n in ((self.A, "北纬科技"), (self.B, "华东重机"), (self.C, "襄阳轴承"), ("000001", "池外"))}
        db.add_all(st.values())
        db.flush()
        for c, s in ((self.A, chip), (self.A, comm), (self.A, margin), (self.B, chip), (self.B, margin),
                     (self.C, comm), ("000001", chip)):
            db.add(StockSectorRelation(stock_id=st[c].id, sector_id=s.id))
        db.commit()

    @staticmethod
    def _links(db, code):
        return sorted((r.attribute_type, r.attribute_value, r.related_code, r.relation_basis, r.source)
                      for r in store.relations_for(db, U, [code]))

    def test_加入观察池就互相关联(self, db):
        self._seed(db)
        store.add_watch(db, U, self.A, D2)
        assert self._links(db, self.A) == [], "池里只有一只，没得关联"
        store.add_watch(db, U, self.B, D3)
        store.add_watch(db, U, self.C, D3)
        assert self._links(db, self.A) == [("concept", "芯片概念", self.B, "B", "pool_sector"),
                                           ("industry", "通信设备", self.C, "A", "pool_sector")], \
            "两个方向、每个共同关注板块一条；没关注的「融资融券」不算；池外的同板块股不算"
        assert self._links(db, self.B) == [("concept", "芯片概念", self.A, "B", "pool_sector")]
        assert self._links(db, self.C) == [("industry", "通信设备", self.A, "A", "pool_sector")]
        r = store.relations_for(db, U, [self.B])[0]
        assert r.confidence_status == "unverified" and r.first_recorded == D3
        assert r.leader_name == "华东重机" and r.related_name == "北纬科技" and "芯片概念" in r.evidence

    def test_重复跑不重复记_删掉否掉的不加回来(self, db):
        self._seed(db)
        store.add_watch(db, U, self.A, D3)
        store.add_watch(db, U, self.B, D3)
        ab = store.relations_for(db, U, [self.A])[0]
        ba = store.relations_for(db, U, [self.B])[0]
        ab.removed_at = datetime.now()       # 改版前删掉的（现在自动记的不给删了）
        db.commit()
        store.upsert_relation(db, U, {**store.relation_dict(ba), "confidence_status": "rejected"}, D3)
        assert store.link_pool_sectors(db, U, D3) == 0
        store.add_watch(db, U, self.C, D3)
        assert self._links(db, self.A) == [("industry", "通信设备", self.C, "A", "pool_sector")]
        assert self._links(db, self.B) == []

    def test_人工记过的不重复记(self, db):
        self._seed(db)
        store.upsert_relation(db, U, {"leader_code": self.A, "related_code": self.B, "attribute_type": "concept",
                                      "attribute_value": "芯片概念", "source": "sector_relation"}, D2)
        store.add_watch(db, U, self.A, D3)
        store.add_watch(db, U, self.B, D3)
        assert self._links(db, self.A) == [("concept", "芯片概念", self.B, "B", "sector_relation")]
        assert self._links(db, self.B) == [("concept", "芯片概念", self.A, "B", "pool_sector")]

    def test_移出观察池的不再新关联_已记的保留(self, db):
        self._seed(db)
        store.add_watch(db, U, self.A, D2)
        store.add_watch(db, U, self.B, D2)
        store.remove_watch(db, U, self.A, D3)
        store.add_watch(db, U, self.C, D3)
        assert self._links(db, self.C) == [], "A 那天已经不在池里"
        assert len(self._links(db, self.B)) == 1, "移出观察池不删已经记下的关联"

    def test_打开复盘页补上功能上线前已在池里的(self, db, cal):
        from app.auth import require_auth
        from app.database import get_db
        from app.models.market_recap import RecapWatchStock
        from app.routers import market_recap
        self._seed(db)
        db.add_all([RecapWatchStock(owner=U, stock_code=c, added_date=D1) for c in (self.A, self.B)])
        db.commit()
        # A 对 B 再记一条别的属性：关联股还是 B 这一只，计数按股票去重
        store.upsert_relation(db, U, {"leader_code": self.A, "related_code": self.B, "attribute_type": "event",
                                      "attribute_value": "同一事件", "source": "manual"}, D2)
        app = FastAPI()
        app.include_router(market_recap.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_auth] = lambda: U
        res = TestClient(app).get(f"/market-recap/{D3.isoformat()}")
        assert res.status_code == 200, res.text
        assert {w["code"]: w["relations"] for w in res.json()["watch_pool"]} == {self.A: 1, self.B: 1}, \
            "A 对 B 有两条（芯片概念 + 事件），关联股只算 1 只"
        assert len(self._links(db, self.A)) == 2


class TestRelationReason:
    """新增关联的原因只有两种：同属某个板块（行业 A / 概念 B），或者自己写一句话（manual，不分 A/B/C）。"""

    def test_共同板块_关注的在前_行业在概念前(self, db):
        TestPoolSectorLink()._seed(db)
        A, B, C = TestPoolSectorLink.A, TestPoolSectorLink.B, TestPoolSectorLink.C
        assert [(x["name"], x["type"], x["watched"]) for x in ctx.shared_sectors(db, A, B)] == \
            [("芯片概念", "concept", True), ("融资融券", "concept", False)]
        assert [(x["name"], x["type"]) for x in ctx.shared_sectors(db, A, C)] == [("通信设备", "industry")]
        assert ctx.shared_sectors(db, B, C) == [], "没有共同板块"

    def test_自己写的原因单独一类_依据记M(self, db):
        r = store.upsert_relation(db, U, {"leader_code": "002148", "related_code": "002685",
                                          "attribute_type": "manual", "attribute_value": "同一个卫星互联网订单"}, D2)
        assert (r.attribute_type, r.relation_basis, r.source) == ("manual", "M", "manual")
        with pytest.raises(store.RecapError, match="关联原因不能为空"):
            store.upsert_relation(db, U, {"leader_code": "002148", "related_code": "002685",
                                          "attribute_type": "manual", "attribute_value": "  "}, D2)

    def test_以前写过的原因_按次数再按最近排_删掉的不算(self, db):
        def rel(leader, related, t, v, d):
            return store.upsert_relation(db, U, {"leader_code": leader, "related_code": related,
                                                 "attribute_type": t, "attribute_value": v}, d)
        rel("002148", "002685", "manual", "卫星互联网订单", D1)
        rel("000678", "002685", "manual", "卫星互联网订单", D2)
        rel("002148", "000678", "event", "同一次并购", D3)
        rel("002148", "600000", "concept", "芯片概念", D3)          # 板块不算「原因」
        gone = rel("002148", "600001", "manual", "删掉的原因", D3)
        store.remove_relation(db, U, gone.id)
        assert [(x["value"], x["count"], x["last_used"]) for x in store.reason_history(db, U)] == [
            ("卫星互联网订单", 2, D2.isoformat()), ("同一次并购", 1, D3.isoformat())]
        assert store.reason_history(db, "someone-else") == []


class TestMirrorRelations:
    """关联是双向的：A → B 记了，B → A 自动补上（以前记过 / 删掉 / 否掉的不动）。"""

    J, X, Y = "002242", "000678", "002086"

    def _rel(self, db, leader, related, v="名称联想", t="name", **kw):
        return store.upsert_relation(db, U, {"leader_code": leader, "related_code": related, "attribute_type": t,
                                             "attribute_value": v, "source": "manual", **kw}, D2)

    @staticmethod
    def _keys(db, code):
        return sorted((r.attribute_type, r.attribute_value, r.related_code, r.relation_basis, r.source, r.confidence_status)
                      for r in store.relations_for(db, U, [code], include_rejected=True))

    def test_记一条就补反向_照抄类别和核验状态(self, db):
        a = self._rel(db, self.J, self.X)
        b = self._rel(db, self.J, self.Y, v="同一个军工订单", t="manual", confidence_status="verified", evidence="同一份公告")
        assert store.mirror_relations(db, U, [a, b]) == 2
        assert self._keys(db, self.X) == [("name", "名称联想", self.J, "C", "reverse", "unverified")]
        assert self._keys(db, self.Y) == [("manual", "同一个军工订单", self.J, "M", "reverse", "verified")]
        assert store.relations_for(db, U, [self.Y])[0].evidence == "同一份公告", "依据照抄"
        assert store.relations_for(db, U, [self.X])[0].first_recorded == D2
        assert store.mirror_relations(db, U) == 0, "重复跑不重复记"

    def test_删人工新增的_反向一起删_不会再自动加回(self, db):
        a = self._rel(db, self.J, self.X)
        store.mirror_relations(db, U, [a])
        assert store.remove_relation(db, U, a.id) == 2, "另一边的反向一起删"
        assert self._keys(db, self.J) == [] and self._keys(db, self.X) == []
        assert store.mirror_relations(db, U) == 0, "删掉的不再自动加回来"

    def test_自动记的不能删(self, db):
        a = self._rel(db, self.J, self.X)
        store.mirror_relations(db, U, [a])
        back = store.relations_for(db, U, [self.X])[0]
        assert back.source == "reverse"
        with pytest.raises(RecapError, match="不能删"):
            store.remove_relation(db, U, back.id)
        auto = store.upsert_relation(db, U, {"leader_code": self.J, "related_code": self.Y, "attribute_type": "concept",
                                             "attribute_value": "芯片概念", "source": "pool_sector"}, D2)
        with pytest.raises(RecapError):
            store.remove_relation(db, U, auto.id)
        assert len(self._keys(db, self.X)) == 1 and len(self._keys(db, self.J)) == 2, "都还在"

    @pytest.mark.parametrize("src", ["sector_relation", "name_rule", "snapshot_concept"])
    def test_自己点出来的也能删(self, db, src):
        a = store.upsert_relation(db, U, {"leader_code": self.J, "related_code": self.X, "attribute_type": "concept",
                                          "attribute_value": "芯片概念", "source": src}, D2)
        store.mirror_relations(db, U, [a])
        assert store.remove_relation(db, U, a.id) == 2
        assert self._keys(db, self.J) == [] and self._keys(db, self.X) == []

    def test_否掉的不往回补(self, db):
        self._rel(db, self.J, self.Y, confidence_status="rejected")
        assert store.mirror_relations(db, U) == 0, "标了不成立的不往回补"
        assert self._keys(db, self.Y) == []

    def test_接口_记关联就双向_打开复盘页补上以前只记一边的(self, db, cal):
        from app.auth import require_auth
        from app.database import get_db
        from app.routers import market_recap
        self._rel(db, self.J, self.Y)                    # 功能上线前记的：只有一边
        app = FastAPI()
        app.include_router(market_recap.router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_auth] = lambda: U
        c = TestClient(app)
        res = c.put("/market-recap/leader-relations", json={
            "date": D3.isoformat(), "leader_code": self.J, "related_code": self.X,
            "attribute_type": "manual", "attribute_value": "同一个军工订单"})
        assert res.status_code == 200, res.text
        assert self._keys(db, self.X) == [("manual", "同一个军工订单", self.J, "M", "reverse", "unverified")]
        assert self._keys(db, self.Y) == []
        assert c.get(f"/market-recap/{D3.isoformat()}").status_code == 200
        assert self._keys(db, self.Y) == [("name", "名称联想", self.J, "C", "reverse", "unverified")]
        back = store.relations_for(db, U, [self.Y])[0]
        res = c.delete(f"/market-recap/leader-relations/{back.id}")
        assert res.status_code == 400 and "不能删" in res.text, "反向自动的不能删"
        fwd = next(r for r in store.relations_for(db, U, [self.J]) if r.related_code == self.Y)
        res = c.delete(f"/market-recap/leader-relations/{fwd.id}")
        assert res.status_code == 200 and res.json()["removed"] == 2
        assert self._keys(db, self.Y) == []


class TestSealRate:
    @pytest.mark.parametrize("lu,broken,expected", [
        (54, 0, (None, None)),      # 11-09：炸板池超时，涨停照常——不是 100%
        (0, 12, (None, None)),
        (54, 6, (6, 0.9)),
    ])
    def test_任一明细0行就算不出(self, lu, broken, expected):
        assert ctx.seal_rate_of(lu, broken) == expected
