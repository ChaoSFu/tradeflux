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
from app.models.stock import Stock
from app.routers import leader_cycle

TODAY = date(2026, 9, 4)


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
    row = LeaderCycleSnapshot(stock_id=st.id, stock_code=st.code,
                              date=TODAY, peak_board_count=5,
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
        assert a["lifecycle_formula_version"] == "price_v1"

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
