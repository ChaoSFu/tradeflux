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
