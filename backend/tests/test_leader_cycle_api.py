"""
/leader-cycle 接口。

核心护栏是**覆盖率的分母**：分母必须是整个强势池，不是"已识别出周期的那些"。
用后者当分母是幸存者偏差——解析不出周期的股票直接从分母里消失，覆盖率看起来
比实际好。这轮强势池排查里 14 只口径不符最后查出 11 只是我们自己算错的，
如果当时它们静默消失在分母外，就根本不会被发现。
"""
from datetime import date

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.models.leader_cycle import LeaderCycleSnapshot
from app.models.stock import Stock, StockDailySnapshot
from app.routers import leader_cycle

TODAY = date(2026, 9, 4)


@pytest.fixture(autouse=True)
def _seed_calendar(db):
    """
    接口层的状态机**必须拿到真实交易日历**才推得动（2026-09-06 起 MA5 上行判定
    也要求相邻交易日）。测试库里没有日历，所以在这里种一份。

    这也是刻意的设计：拿不到日历时状态机停在原地，而不是退回"库里有哪些日期"
    ——那正是要防的东西（假如某天 daily_update 整个挂掉，一行快照都没写，
    日期集合会把隔着一个开市日的两天判成相邻）。
    """
    from app.services.trading_calendar import _write_cache
    _write_cache(db, [date(2026, 9, i) for i in range(1, 8)])
    yield


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(leader_cycle.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _stock(db, code, board60=5, in_pool=True):
    st = Stock(code=code, name=f"股{code}", market="SH",
               in_strong_pool=in_pool, board_count_60d=board60)
    db.add(st); db.flush()
    return st


def _snap(db, st, **kw):
    # cycle_start_date 有没有值 = 这行有没有识别出周期。缺省当作有，
    # 想造"无周期"的行就显式传 cycle_start_date=None
    kw.setdefault("cycle_start_date", TODAY)
    kw.setdefault("cycle_peak_date", TODAY)
    kw.setdefault("peak_board_count", 5)
    row = LeaderCycleSnapshot(stock_id=st.id, stock_code=st.code, date=TODAY,
                              board_count_60d=st.board_count_60d, **kw)
    db.add(row); db.flush()
    return row


class TestCoverage:

    def test_分母是整个强势池而不是已识别的那些(self, db, client):
        """池子里 3 只，只有 2 只解析出周期，第 3 只不能从分母里消失。"""
        a, b = _stock(db, "600001"), _stock(db, "600002")
        _stock(db, "600003", board60=2)          # 在池里但识别不出周期
        _snap(db, a, rs_market_20=1.0)
        _snap(db, b)                             # 没有 RS
        cov = client.get("/leader-cycle").json()["coverage"]
        assert cov["pool_total"] == 3
        assert cov["cycle_identified"] == 2
        assert cov["cycle_unresolved"] == 1
        assert cov["total"] == 3, "覆盖率分母 = 整个池子"
        assert cov["rs_market"] == 1, "3 只里只有 1 只有 RS，不能算成 2 分之 1"

    def test_识别不出周期的股票如实列出而不是静默丢弃(self, db, client):
        _snap(db, _stock(db, "600001"))
        _stock(db, "600003", board60=2)
        data = client.get("/leader-cycle").json()
        codes = [u["code"] for u in data["unresolved"]]
        assert codes == ["600003"]
        assert data["unresolved"][0]["board_count_60d"] == 2
        assert data["unresolved"][0]["reason"], "要说明为什么解析不出来"

    def test_不在强势池的股票不进分母(self, db, client):
        _snap(db, _stock(db, "600001"))
        _stock(db, "600009", in_pool=False)
        cov = client.get("/leader-cycle").json()["coverage"]
        assert cov["pool_total"] == 1 and cov["cycle_unresolved"] == 0

    def test_没有数据时不报假的空结构(self, db, client):
        data = client.get("/leader-cycle").json()
        assert data["trade_date"] is None
        assert data["running"] == [] and data["broken"] == []
        assert data["unresolved"] == [] and data["coverage"] == {}


class TestLifecycleInApi:
    """
    状态是**派生**出来的，不落库。所以接口这一层要保证两件事：
    replay 用的历史不能越过 as_of，以及事实层依旧干净。
    """

    def _seq(self, db, st, days, **per_day):
        """按日期造一串快照。per_day 里每个键是列名，值是与 days 等长的列表。"""
        for i, d in enumerate(days):
            db.add(LeaderCycleSnapshot(
                stock_id=st.id, stock_code=st.code, date=d,
                peak_board_count=5, board_count_60d=5,
                cycle_start_date=days[0], cycle_peak_date=days[0],
                data_fresh=True, bar_settled=True,
                **{k: v[i] for k, v in per_day.items()}))
        db.flush()

    def test_历史查询不看未来(self, db, client):
        """
        Case N 的接口版：改了 T+1/T+2 的数据，T 那天的状态必须完全不变。
        这是 look-ahead guard 唯一能在接口层验证的地方。
        """
        st = _stock(db, "600001")
        # BROKEN → REPAIRING → CROSS_SUCCESS → 崩 → FADED，一天一步
        days = [date(2026, 9, i) for i in (1, 2, 3, 4, 5)]
        self._seq(db, st, days,
                  break_date=[date(2026, 8, 28)] * 5,
                  days_since_break=[1, 2, 3, 4, 5],
                  latest_close=[19.0, 20.0, 22.0, 10.0, 9.0],
                  ma5=[18.6, 18.8, 19.5, 19.0, 18.0],
                  ma10=[17.6, 17.8, 18.0, 18.0, 17.0],
                  ma20=[16.6, 17.0, 17.0, 17.0, 17.0],
                  ma30=[15.6, 16.0, 16.0, 16.0, 16.0],
                  new_post_break_high_today=[False, False, True, False, False],
                  new_post_break_low_today=[False, False, False, True, True])
        at_t = client.get("/leader-cycle",
                          params={"trade_date": "2026-09-03"}).json()
        later = client.get("/leader-cycle",
                           params={"trade_date": "2026-09-05"}).json()
        a = (at_t["running"] + at_t["broken"])[0]
        b = (later["running"] + later["broken"])[0]
        assert a["lifecycle_state"] == "CROSS_SUCCESS"
        assert a["transitioned_today"] is True
        assert b["lifecycle_state"] == "FADED", "后面两天确实崩了"
        assert a["state_since_date"] == "2026-09-03"
        from app.services.leader_cycle_state_service import FORMULA_VERSION
        assert a["lifecycle_formula_version"] == FORMULA_VERSION

    def test_每只都带得出原因和口径版本(self, db, client):
        _snap(db, _stock(db, "600001"), data_fresh=True, bar_settled=True,
              latest_close=10.0, break_date=None,
              cycle_start_date=TODAY, cycle_peak_date=TODAY)
        item = client.get("/leader-cycle").json()["broken"][0] \
            if False else (client.get("/leader-cycle").json()["running"] or
                           client.get("/leader-cycle").json()["broken"])[0]
        assert item["lifecycle_state"] == "STREAKING"
        assert item["transition_reasons"], "code 要能翻成人话"
        assert item["evaluation_status"] == "OK"

    def test_未结算时状态是UNKNOWN而不是硬判(self, db, client):
        _snap(db, _stock(db, "600001"), data_fresh=True, bar_settled=False,
              latest_close=10.0, break_date=date(2026, 8, 28), days_since_break=3,
              cycle_start_date=TODAY, cycle_peak_date=TODAY)
        item = (client.get("/leader-cycle").json()["broken"] +
                client.get("/leader-cycle").json()["running"])[0]
        assert item["lifecycle_state"] == "UNKNOWN"
        assert item["evaluation_status"] == "UNSETTLED"

    def test_覆盖率报出有多少只判得出状态(self, db, client):
        _snap(db, _stock(db, "600001"), data_fresh=True, bar_settled=True,
              latest_close=10.0, break_date=None,
              cycle_start_date=TODAY, cycle_peak_date=TODAY)
        _snap(db, _stock(db, "600002"), data_fresh=True, bar_settled=False,
              latest_close=10.0, break_date=None,
              cycle_start_date=TODAY, cycle_peak_date=TODAY)
        cov = client.get("/leader-cycle").json()["coverage"]
        assert cov["lifecycle_resolved"] == 1 and cov["settled"] == 1


class TestNothingSwallowed:
    """
    **一只股票因为归不了类而从界面上消失，是这个页面最不能容忍的失败。**
    不可见比判断错更糟——判断错还能被看见并纠正。

    所以接口层必须保证：running + broken + unresolved 是整个强势池的一个划分
    （不重不漏）。前端的分组桶再怎么改，都是在这三个列表之上切的。
    """

    def test_三个列表不重不漏地覆盖整个池(self, db, client):
        a, b = _stock(db, "600001"), _stock(db, "600002")
        _stock(db, "600003", board60=2)           # 识别不出周期
        _stock(db, "600004", board60=9)           # 60日够但没算出周期
        _snap(db, a, break_date=None, data_fresh=True, bar_settled=True,
              latest_close=10.0, cycle_start_date=TODAY, cycle_peak_date=TODAY)
        _snap(db, b, break_date=date(2026, 8, 28), days_since_break=3,
              data_fresh=True, bar_settled=True, latest_close=10.0,
              cycle_start_date=TODAY, cycle_peak_date=TODAY)
        d = client.get("/leader-cycle").json()
        codes = ([i["code"] for i in d["running"]] + [i["code"] for i in d["broken"]]
                 + [u["code"] for u in d["unresolved"]])
        assert len(codes) == len(set(codes)) == d["coverage"]["pool_total"] == 4, \
            "三个列表加起来必须正好是整个池，且不重复"
        assert set(codes) == {"600001", "600002", "600003", "600004"}

    def test_识别不出周期的也要说明原因(self, db, client):
        """
        只给一个代码等于把问题丢给人。前端要把它们渲染成表格里的行，
        原因就是那行唯一能显示的内容。
        """
        _snap(db, _stock(db, "600001"), break_date=None, data_fresh=True,
              bar_settled=True, latest_close=10.0,
              cycle_start_date=TODAY, cycle_peak_date=TODAY)
        _stock(db, "600003", board60=2)
        u = client.get("/leader-cycle").json()["unresolved"][0]
        assert u["reason"] and u["board_count_60d"] == 2

    def test_每只有快照的都判得出一个状态(self, db, client):
        """
        replay 永远返回一个状态（最差是 UNKNOWN），不会返回 None——前端的
        `?? 'UNKNOWN'` 只是防御，不该是常态。
        """
        for i, (fresh, settled) in enumerate(
                [(True, True), (True, False), (False, True), (True, None)]):
            st = _stock(db, f"60000{i + 1}")
            _snap(db, st, break_date=None, data_fresh=fresh, bar_settled=settled,
                  latest_close=10.0, cycle_start_date=TODAY, cycle_peak_date=TODAY)
        d = client.get("/leader-cycle").json()
        items = d["running"] + d["broken"]
        assert len(items) == 4
        assert all(i["lifecycle_state"] for i in items), "一个都不能是 null"
        assert sum(1 for i in items if i["lifecycle_state"] == "UNKNOWN") == 3


class TestNoCycleRowsAreRealRows:
    """
    2026-09-04：识别不出周期的股票也写行了。

    之前直接不建行，理由是"缺行比一行 NULL 清楚"——那是**拿一个字段的缺失惩罚了
    另外二十个**：收盘价、四条均线、成交量、换手率、RS 全都只需要 K 线，跟有没有
    连板周期无关。后果是这只票把每个覆盖率的天花板压到 60/61，界面上只能显示一行
    破折号。
    """

    def test_无周期的行状态是NO_CYCLE不是STREAKING(self, db, client):
        """
        没有周期时 break_date 也是 NULL，而 `break_date is None` 在状态机里的含义
        是"仍在连板中"——不先判有没有周期，就会把一只没有周期的票标成连板中，
        正好是反的。
        """
        _snap(db, _stock(db, "600003", board60=2), cycle_start_date=None,
              cycle_peak_date=None, peak_board_count=None,
              data_fresh=True, bar_settled=True, latest_close=10.0)
        it = (client.get("/leader-cycle").json()["running"]
              + client.get("/leader-cycle").json()["broken"])[0]
        assert it["lifecycle_state"] == "NO_CYCLE"
        assert it["evaluation_status"] == "NO_CYCLE"
        assert it["transition_reasons"], "要说明为什么没有周期"

    def test_无周期的行照样带价格事实(self, db, client):
        """这才是改这一版的理由——它不该再是一行破折号。"""
        _snap(db, _stock(db, "600003", board60=2), cycle_start_date=None,
              cycle_peak_date=None, peak_board_count=None,
              data_fresh=True, bar_settled=True, latest_close=12.34,
              ma5=11.0, volume=1e7, turnover_rate=8.8, rs_market_20=3.3)
        it = (client.get("/leader-cycle").json()["running"]
              + client.get("/leader-cycle").json()["broken"])[0]
        assert it["latest_close"] == 12.34 and it["ma5"] == 11.0
        assert it["turnover_rate"] == 8.8 and it["rs_market_20"] == 3.3

    def test_覆盖率把有事实和有周期分开报(self, db, client):
        a = _stock(db, "600001")
        _snap(db, a, data_fresh=True, bar_settled=True, latest_close=10.0,
              break_date=None)
        _snap(db, _stock(db, "600003", board60=2), cycle_start_date=None,
              cycle_peak_date=None, peak_board_count=None,
              data_fresh=True, bar_settled=True, latest_close=10.0)
        cov = client.get("/leader-cycle").json()["coverage"]
        assert cov["pool_total"] == 2
        assert cov["with_facts"] == 2, "两只都有价格事实"
        assert cov["cycle_identified"] == 1 and cov["cycle_unresolved"] == 1


class TestLifecycleEffect:
    """
    生命周期口径的赚钱效应。这个接口最容易犯的错是**用盘中价冒充当日结果**，
    以及**样本三五个也照给中位数**。
    """

    def _seed(self, db, code, states_by_date, closes):
        """states_by_date 用价格构造，这里直接落快照+日线。"""
        st = _stock(db, code)
        for d, close in closes.items():
            db.add(StockDailySnapshot(stock_id=st.id, date=d, close_price=close,
                                      is_settled=True))
        for d in states_by_date:
            db.add(LeaderCycleSnapshot(
                stock_id=st.id, stock_code=code, date=d, peak_board_count=5,
                board_count_60d=5, cycle_start_date=date(2026, 8, 20),
                cycle_peak_date=date(2026, 8, 20), break_date=None,
                data_fresh=True, bar_settled=True, latest_close=closes.get(d)))
        db.flush()
        return st

    def test_盘中未结算的收盘价不进统计(self, db, client):
        """
        赚钱效应统计里混进盘中价，等于让上午的浮动冒充当日结果。
        """
        st = _stock(db, "600001")
        d1, d2 = date(2026, 9, 3), date(2026, 9, 4)
        db.add(StockDailySnapshot(stock_id=st.id, date=d1, close_price=10.0,
                                  is_settled=True))
        db.add(StockDailySnapshot(stock_id=st.id, date=d2, close_price=11.0,
                                  is_settled=False))     # 盘中
        for d in (d1, d2):
            db.add(LeaderCycleSnapshot(
                stock_id=st.id, stock_code=st.code, date=d, peak_board_count=5,
                board_count_60d=5, cycle_start_date=date(2026, 8, 20),
                cycle_peak_date=date(2026, 8, 20), break_date=None,
                data_fresh=True, bar_settled=True, latest_close=10.0))
        db.flush()
        r = client.get("/leader-cycle/effect").json()
        for c in r["cohorts"]:
            assert c["count"] == 0 or c["median_pct_change"] is None, \
                "未结算那天的收盘价不该产出收益"

    def test_样本太少不给中位数(self, db, client):
        """个位数样本的中位数没有意义，给 None 并把 count 一起返回。"""
        r = client.get("/leader-cycle/effect").json()
        for h in r["history"]:
            for k in ("t1", "t3", "t5"):
                if h[f"{k}_n"] < 5:
                    assert h[k] is None and h[f"{k}_win"] is None

    def test_必须声明历史部分只是线索(self, db, client):
        """
        没做同日同池对照、没有置信区间的前瞻数字最容易被当成结论。
        接口自己要把这句话带上，不能指望界面记得写。
        """
        notes = " ".join(client.get("/leader-cycle/effect").json()["notes"])
        assert "线索不是结论" in notes and "evaluate_lifecycle" in notes

    def test_带口径版本(self, db, client):
        r = client.get("/leader-cycle/effect").json()
        from app.services.leader_cycle_state_service import FORMULA_VERSION
        assert r["formula_version"] == FORMULA_VERSION


class TestEvidenceEndpoint:
    """
    `/leader-cycle/evidence` 端出离线评估产物。**它最容易出的错是把「没跑过」
    伪装成「跑过了但没有证据」**——前者该提示去跑，后者会被读成「验证过，没
    Edge」。返回空表就是在说后者。
    """

    def test_没跑过时如实说没跑过(self, client, tmp_path, monkeypatch):
        from app.config import settings
        monkeypatch.setattr(settings, "LIFECYCLE_EVIDENCE_PATH",
                            str(tmp_path / "nope.json"))
        r = client.get("/leader-cycle/evidence").json()
        assert r["available"] is False and "evaluate_lifecycle" in r["reason"]
        assert "events" not in r, "空的 events 会被读成「跑过了但没有证据」"

    def test_产物读不出来跟没跑过要分开(self, client, tmp_path, monkeypatch):
        from app.config import settings
        bad = tmp_path / "broken.json"
        bad.write_text("{ not json", encoding="utf-8")
        monkeypatch.setattr(settings, "LIFECYCLE_EVIDENCE_PATH", str(bad))
        r = client.get("/leader-cycle/evidence").json()
        assert r["available"] is False and "读不出来" in r["reason"]

    def test_口径变了要标出来(self, client, tmp_path, monkeypatch):
        import json
        from app.config import settings
        f = tmp_path / "old.json"
        f.write_text(json.dumps({"formula_version": "price_v0_9", "events": []}),
                     encoding="utf-8")
        monkeypatch.setattr(settings, "LIFECYCLE_EVIDENCE_PATH", str(f))
        r = client.get("/leader-cycle/evidence").json()
        assert r["available"] is True and r["stale_formula"] is True, \
            "换了口径之后旧证据不再对应当前规则，界面必须能说出来"
        assert r["file_mtime"], "离线产物不带时间戳，过期了没人看得出来"


class TestLimitMovesFreshness:
    """
    `/stocks/limit-moves` 原来只返回 items/total/page——**服务端知道自己查的是
    哪一天，调用方却无从得知**。涨跌停分析页要把它跟涨停板块雷达、市场效应摆
    在一起，不给日期就只能默认三者同一天，而它们经常不是。
    """

    def test_返回自己查的是哪一天(self, db):
        from datetime import date as d
        from app.services.strong_stock_service import get_limit_moves_pool
        st = _stock(db, "600001")
        db.add(StockDailySnapshot(stock_id=st.id, date=d(2026, 9, 4),
                                  close_price=10.0, pct_change=10.0,
                                  is_limit_up=True, is_settled=True))
        db.commit()
        r = get_limit_moves_pool(db)
        assert r.trade_date == d(2026, 9, 4)
        assert r.is_settled is True

    def test_有一行是盘中值整份名单就不算收盘结果(self, db):
        from datetime import date as d
        from app.services.strong_stock_service import get_limit_moves_pool
        a, b = _stock(db, "600002"), _stock(db, "600003")
        db.add(StockDailySnapshot(stock_id=a.id, date=d(2026, 9, 4), close_price=10.0,
                                  pct_change=10.0, is_limit_up=True, is_settled=True))
        db.add(StockDailySnapshot(stock_id=b.id, date=d(2026, 9, 4), close_price=20.0,
                                  pct_change=10.0, is_limit_up=True, is_settled=False))
        db.commit()
        assert get_limit_moves_pool(db).is_settled is False, \
            "混着盘中值的名单不是收盘结果，不能标成已结算"

    def test_没有数据时不猜(self, db):
        from app.services.strong_stock_service import get_limit_moves_pool
        r = get_limit_moves_pool(db)
        assert r.trade_date is None and r.is_settled is None, "一行都没有就是不知道"


class TestSettledCaliberIsShared:
    """
    「哪根 bar 算数」只能有一套判定。**这个仓库为「同一个事实两套判定」栽过 10 次**，
    最近一次就是 is_settled：evaluate_lifecycle 的 Bars 和 leader_cycle_effect_service
    一个不滤一个硬滤，同一张页面上两张表用了两套样本。

    正确口径：只排除**最新日期上**未结算的行。更早日期的 False 是记账缺口——
    该字段 2026-05-28 才开始有值，之前 17 万行全是 False，那是字段还不存在时写
    进去的收盘价。
    """

    def _src(self, mod):
        import pathlib
        return pathlib.Path(mod.__file__).read_text(encoding="utf-8")

    def test_赚钱效应不再硬滤settled(self):
        from app.services import leader_cycle_effect_service as m
        src = self._src(m)
        assert "is_settled.is_(True)" not in src, \
            "硬滤会把半年前的历史一起扔掉，而且丢掉的样本跟时间强相关"
        assert "d_ == as_of and settled is not True" in src, \
            "今天未结算的那一行才是活价格，必须排除"

    def test_评估脚本用同一套口径(self):
        import scripts.evaluate_lifecycle as m
        src = self._src(m)
        assert "live_date" in src and "unsettled_kept" in src
        assert 'r.date == live_date' in src

    def test_保留了多少未结算行要说出来(self):
        from app.services import leader_cycle_effect_service as m
        assert "没标 is_settled" in self._src(m), \
            "口径的代价要跟数字一起走，不能只写在注释里"


class TestDailySeries:
    """
    逐日赚钱效应：**昨天处于某状态的票，今天的平均涨幅**。

    这条曲线换掉了旧的四条（昨日涨停龙头/震荡/走弱/破位）——那四组按 Stock.phase
    分，而 phase 只是"收盘价在哪条均线下面"的单日快照。

    这里要钉住三件事：**归到收益发生的那天**（不是状态所在那天）、**用均值**
    （要跟「强势股均涨幅」可比）、**没有该状态的票时那天没有这个 key**（不是 0）。
    """

    def _setup(self, db):
        from datetime import date as d
        from app.services.trading_calendar import _write_cache
        days = [d(2026, 9, i) for i in (1, 2, 3, 4)]
        _write_cache(db, days)
        return days

    def _mk(self, db, code, days, closes, *, break_on=None):
        st = _stock(db, code)
        for i, day in enumerate(days):
            db.add(StockDailySnapshot(stock_id=st.id, date=day, close_price=closes[i],
                                      is_settled=True))
            db.add(LeaderCycleSnapshot(
                stock_id=st.id, stock_code=code, date=day, board_count_60d=5,
                cycle_start_date=days[0], cycle_peak_date=days[0], peak_board_count=5,
                latest_close=closes[i], data_fresh=True, bar_settled=True,
                break_date=break_on, days_since_break=(0 if break_on else None)))
        return st

    def test_归到收益发生的那天并用均值(self, db):
        from app.services.leader_cycle_effect_service import compute_effect
        days = self._setup(db)
        # 两只票同状态，涨幅不同 → 那天该状态取均值
        self._mk(db, "600001", days, [10.0, 11.0, 11.0, 11.0])   # 09-02 +10%
        self._mk(db, "600002", days, [10.0, 10.0, 10.0, 10.0])   # 09-02  0%
        db.commit()
        r = compute_effect(db, days[-1])
        by_date = {p["trade_date"]: p["values"] for p in r["series"]}
        assert "2026-09-02" in by_date, "收益发生在 09-02，就该归到 09-02"
        vals = list(by_date["2026-09-02"].values())
        assert len(vals) == 1 and vals[0]["n"] == 2
        assert vals[0]["avg"] == 5.0, "(+10% + 0%) / 2 —— 均值，不是中位数"

    def test_没有该状态的票时那天没有这个key(self, db):
        """**不是 0。** 0 会被读成"那天这组不赚不亏"。"""
        from app.services.leader_cycle_effect_service import compute_effect
        days = self._setup(db)
        self._mk(db, "600003", days, [10.0, 10.5, 10.5, 10.5])
        db.commit()
        r = compute_effect(db, days[-1])
        states = {s for p in r["series"] for s in p["values"]}
        assert "CROSS_FAILED" not in states, "没有的状态不能凭空出现一条 0 的线"

    def test_没有数据时给空列表不给假点(self, db):
        from app.services.leader_cycle_effect_service import compute_effect
        r = compute_effect(db, None)
        assert r["series"] == []


class TestEffectQueryScope:
    """
    **赚钱效应只查用得到的股票、日期、列。**

    2026-09-07 探针抓到：单次 `/leader-cycle/effect` 请求 +405MB / 11.95s，而全站
    其余接口都在 20~40MB。原因是它 `db.query(StockDailySnapshot).all()` —— 整张
    表，生产 21 万行、每行 40+ 列的映射对象。

    根因是同一天早些时候摘掉 `is_settled == True` 那个 filter（摘得对）时，没注意
    到它同时还兼着"把 21 万行削到 3.8 万行"的副作用——**一个 filter 同时承担两个
    职责，删掉它的时候只想着其中一个**。
    """

    def _src(self):
        import pathlib
        from app.services import leader_cycle_effect_service as m
        return pathlib.Path(m.__file__).read_text(encoding="utf-8")

    def test_不整表加载快照(self):
        src = self._src()
        assert "db.query(StockDailySnapshot)\n" not in src, \
            "整行 ORM 对象加载整张表，21 万行就是几百 MB"
        assert "StockDailySnapshot.close_price," in src, "只取用得到的列"

    def test_按日期和股票收窄(self):
        src = self._src()
        assert "StockDailySnapshot.date >= win_start" in src
        assert "StockDailySnapshot.stock_id.in_(pool_ids)" in src

    def test_不整表加载股票(self):
        src = self._src()
        assert "db.query(Stock).all()" not in src, \
            "只要 id→code 两列，且只要池子里那几十只"
        assert "db.query(Stock.id, Stock.code)" in src

    def test_窄查询之后结果不变(self, db):
        """收窄查询是性能改动，**数字一个都不能变**。"""
        from datetime import date as d
        from app.services.leader_cycle_effect_service import compute_effect
        from app.services.trading_calendar import _write_cache
        days = [d(2026, 9, i) for i in (1, 2, 3, 4)]
        _write_cache(db, days)
        st = _stock(db, "600099")
        for i, day in enumerate(days):
            db.add(StockDailySnapshot(stock_id=st.id, date=day,
                                      close_price=10.0 + i, is_settled=True))
            db.add(LeaderCycleSnapshot(
                stock_id=st.id, stock_code=st.code, date=day, board_count_60d=5,
                cycle_start_date=days[0], cycle_peak_date=days[0], peak_board_count=5,
                latest_close=10.0 + i, data_fresh=True, bar_settled=True))
        db.commit()
        r = compute_effect(db, days[-1])
        assert r["series"], "收窄之后还得算得出东西来"
        assert all(p["values"] for p in r["series"])


class TestSnapshotWindowScope:
    """
    生命周期快照也只加载**算得到窗口所需**的行。

    这张表每个交易日给池内每只票写一行（~60 行/天），而窗口固定 60 天——整表
    加载的成本随时间线性增长，窗口却不变。2026-09-07 实测 3725 行（06-11 起），
    刚好约等于窗口；半年后表就是窗口的三倍。**已知是问题就先解决。**

    截断的安全性论证（这条比省内存重要）：新周期时状态机做 `obs = [row]` +
    `_initial_state(row)`，并把 ever_success 归零——**某天的状态只取决于它所在
    那个周期的行**。所以下界取「窗口内各行的最早 cycle_start_date」，窗口里每
    一天的 state / last_valid_state 逐位不变。
    """

    def _src(self):
        import pathlib
        from app.services import leader_cycle_effect_service as m
        return pathlib.Path(m.__file__).read_text(encoding="utf-8")

    def test_不整表加载生命周期快照(self):
        src = self._src()
        assert "db.query(LeaderCycleSnapshot).all()" not in src
        assert "LeaderCycleSnapshot.date >= lower" in src

    def test_下界考虑周期起点而不是直接砍到窗口(self):
        """直接砍到 win_start 会让跨窗口边界的周期少掉前半段，状态就变了。"""
        src = self._src()
        assert "min(LeaderCycleSnapshot.cycle_start_date)" in src
        assert "min(_win_start, _min_cycle)" in src

    def test_跨窗口边界的周期状态不变(self, db):
        """
        造一段：周期从窗口**之前**开始，一直延续到窗口内。
        history_days=2 把窗口压到最后两天，下界必须回退到 cycle_start。
        """
        from datetime import date as d
        from app.services.leader_cycle_effect_service import compute_effect
        from app.services.trading_calendar import _write_cache
        days = [d(2026, 9, i) for i in (1, 2, 3, 4)]
        _write_cache(db, days)
        st = _stock(db, "600098")
        for i, day in enumerate(days):
            db.add(StockDailySnapshot(stock_id=st.id, date=day,
                                      close_price=10.0 + i, is_settled=True))
            db.add(LeaderCycleSnapshot(
                stock_id=st.id, stock_code=st.code, date=day, board_count_60d=5,
                cycle_start_date=days[0], cycle_peak_date=days[0], peak_board_count=5,
                latest_close=10.0 + i, ma5=9.0, ma10=8.0, ma20=7.0, ma30=6.0,
                data_fresh=True, bar_settled=True, days_since_break=i))
        db.commit()

        full = compute_effect(db, days[-1], history_days=60)
        clipped = compute_effect(db, days[-1], history_days=2)
        # 窗口重叠的那几天，状态必须一模一样
        f = {p["trade_date"]: p["values"] for p in full["series"]}
        c = {p["trade_date"]: p["values"] for p in clipped["series"]}
        for day in c:
            assert f.get(day) == c[day], f"{day} 截断之后状态变了：{f.get(day)} != {c[day]}"
        assert c, "窗口压到 2 天之后还得算得出东西"


class TestCohortTrimmedMean:
    """
    当日 cohort 用**截尾均值**：去掉一个最高、一个最低再平均。

    中位数只看排中间的那一两只，组里其余的涨跌完全不进结果；裸均值一只涨停就能
    把 5 只的组拽红。两头都挡一下。

    这里最容易出的错是**名字撒谎**——字段一度叫 median_pct_change 而里面装的是
    别的统计量。同一屏上方那张图是均值，标签只差一个字，读混的代价很实在。
    """

    def test_去掉一个最高一个最低(self):
        from app.services.leader_cycle_effect_service import _trimmed_mean
        # 去掉 -50 和 +50，剩 [1,2,3] → 2.0
        assert _trimmed_mean([3.0, -50.0, 1.0, 50.0, 2.0]) == 2.0

    def test_一只涨停拽不动整组(self):
        from app.services.leader_cycle_effect_service import _trimmed_mean
        vals = [10.0, 0.1, 0.2, 0.3, 0.4]
        assert _trimmed_mean(vals) == 0.3, "裸均值会是 2.2——被那一只拽红了"

    def test_不足3只算不出(self):
        """**不退回裸均值。** 同一列里混两种口径，看的人分不出哪个是哪个。"""
        from app.services.leader_cycle_effect_service import _trimmed_mean
        assert _trimmed_mean([5.0, -5.0]) is None
        assert _trimmed_mean([5.0]) is None
        assert _trimmed_mean([]) is None

    def test_三只时剩一只等于中位数(self):
        from app.services.leader_cycle_effect_service import _trimmed_mean
        assert _trimmed_mean([1.0, 7.0, 100.0]) == 7.0

    def test_字段名不能再叫median(self):
        import pathlib
        from app.services import leader_cycle_effect_service as m
        src = pathlib.Path(m.__file__).read_text(encoding="utf-8")
        assert '"median_pct_change"' not in src, \
            "字段里装的是截尾均值，名字必须说实话"
        assert '"trimmed_avg_pct_change"' in src


class TestCohortOrdering:
    """卡片按涨幅从高到低；同分看红盘率；**算不出的沉底**。"""

    def _sorted(self, rows):
        rows = list(rows)
        rows.sort(key=lambda c: (
            c["trimmed_avg_pct_change"] is None,
            -(c["trimmed_avg_pct_change"] or 0.0),
            -c["red_ratio"]))
        return [c["state"] for c in rows]

    def test_按涨幅降序同分看红盘率(self):
        rows = [
            {"state": "A", "trimmed_avg_pct_change": 1.0, "red_ratio": 0.5},
            {"state": "B", "trimmed_avg_pct_change": 3.0, "red_ratio": 0.6},
            {"state": "C", "trimmed_avg_pct_change": 1.0, "red_ratio": 0.9},
        ]
        assert self._sorted(rows) == ["B", "C", "A"]

    def test_算不出的沉底不跟真跌的抢倒数(self):
        """「不知道」不是「最低」。"""
        rows = [
            {"state": "N", "trimmed_avg_pct_change": None, "red_ratio": 1.0},
            {"state": "D", "trimmed_avg_pct_change": -8.0, "red_ratio": 0.1},
        ]
        assert self._sorted(rows) == ["D", "N"]

    def test_排序规则跟服务里的一致(self):
        import pathlib
        from app.services import leader_cycle_effect_service as m
        src = pathlib.Path(m.__file__).read_text(encoding="utf-8")
        assert 'c["trimmed_avg_pct_change"] is None,' in src
        assert '-(c["trimmed_avg_pct_change"] or 0.0),' in src
        assert '-c["red_ratio"]' in src


class TestTodayEstimate:
    """
    今日盘中估算点。

    **它最容易出的错是污染跨日统计**：盘中价一旦进了 px，上午的浮动就会冒充
    当日结果，而所有的 series / cohorts / history 都跟着错。所以这里测的是
    「它算得出来」和「它没有渗进别处」两件事。
    """

    def _setup(self, db, *, settle_today: bool):
        from datetime import date as d
        from app.services.trading_calendar import _write_cache
        days = [d(2026, 9, i) for i in (1, 2, 3)]
        _write_cache(db, days)
        st = _stock(db, "600097")
        closes = [10.0, 10.0, 12.0]          # 最后一天 +20%
        for i, day in enumerate(days):
            db.add(StockDailySnapshot(
                stock_id=st.id, date=day, close_price=closes[i],
                # 最后一天按参数决定结不结算
                is_settled=(settle_today or i < len(days) - 1)))
            db.add(LeaderCycleSnapshot(
                stock_id=st.id, stock_code=st.code, date=day, board_count_60d=5,
                cycle_start_date=days[0], cycle_peak_date=days[0], peak_board_count=5,
                latest_close=closes[i], ma5=9.0, ma10=8.0, ma20=7.0, ma30=6.0,
                data_fresh=True, bar_settled=True, days_since_break=i))
        db.commit()
        return days

    def test_未收盘时给出估算点(self, db):
        from app.services.leader_cycle_effect_service import compute_effect
        days = self._setup(db, settle_today=False)
        r = compute_effect(db, days[-1])
        te = r["today_estimate"]
        assert te and te["is_estimate"] is True
        assert te["trade_date"] == "2026-09-03" and te["based_on"] == "2026-09-02"
        one = next(iter(te["values"].values()))
        assert one["avg"] == 20.0 and one["n"] == 1

    def test_估算点不进series(self, db):
        """**盘中价不能进跨日统计。** 进去了上午的浮动就冒充了当日结果。"""
        from app.services.leader_cycle_effect_service import compute_effect
        days = self._setup(db, settle_today=False)
        r = compute_effect(db, days[-1])
        assert "2026-09-03" not in {p["trade_date"] for p in r["series"]}

    def test_收盘之后没有估算点(self, db):
        """真值已经有了，就不该再挂一个估算。"""
        from app.services.leader_cycle_effect_service import compute_effect
        days = self._setup(db, settle_today=True)
        r = compute_effect(db, days[-1])
        assert r["today_estimate"] is None
        assert "2026-09-03" in {p["trade_date"] for p in r["series"]}, \
            "收盘之后这一天该以真实值进 series"

    def test_估算这件事要写进notes(self, db):
        from app.services.leader_cycle_effect_service import compute_effect
        days = self._setup(db, settle_today=False)
        r = compute_effect(db, days[-1])
        assert any("盘中估算" in n for n in r["notes"]), \
            "图上多一个点而不说它是估算，就是拿盘中价冒充收盘结果"
