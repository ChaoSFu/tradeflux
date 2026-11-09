"""
顶栏「今日涨停最多」的板块计数：成员、板块口径必须跟 /stocks/limit-moves 名单完全一样
（以前就是拿那份名单在前端数的），只是不用再把整份名单拉回来。
"""
from collections import Counter
from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.sector import Sector, StockSectorRelation
from app.models.stock import Stock, StockDailySnapshot
from app.services.strong_stock_service import get_limit_moves_pool, limit_moves_sector_counts

D0, D1 = date(2026, 11, 6), date(2026, 11, 9)


def _sector(db, name, watched=True):
    s = Sector(code=f"BK{abs(hash(name)) % 10000:04d}", name=name, sector_type="concept", is_watched=watched)
    db.add(s)
    db.flush()
    return s


def _stock(db, code, sectors, d=D1, lu=True, ld=False, st=False):
    s = Stock(code=code, name=f"股{code}", market="SZ", is_st=st)
    db.add(s)
    db.flush()
    db.add(StockDailySnapshot(stock_id=s.id, date=d, close_price=10.0, pct_change=10.0 if lu else -10.0,
                              is_limit_up=lu, is_limit_down=ld))
    for sec in sectors:
        db.add(StockSectorRelation(stock_id=s.id, sector_id=sec.id))
    return s


def _seed(db):
    robot, space, chem = _sector(db, "人形机器人"), _sector(db, "商业航天"), _sector(db, "基础化工")
    hidden = _sector(db, "融资融券", watched=False)
    _stock(db, "000001", [robot, space, hidden])
    _stock(db, "000002", [robot])
    _stock(db, "000003", [robot, chem])
    _stock(db, "000004", [space])
    _stock(db, "000005", [robot], st=True)                 # ST 不在名单里
    _stock(db, "000006", [chem], lu=False, ld=True)        # 跌停
    _stock(db, "000007", [robot], d=D0)                    # 前一天涨停，今天没快照
    db.commit()


def test_跟名单里的板块逐个数出来的一样(db):
    _seed(db)
    items = get_limit_moves_pool(db, 1, 500, None, "limit_up").items
    expect = Counter(n for s in items for n in set(s.sectors))
    r = limit_moves_sector_counts(db, "limit_up")
    assert {x["name"]: x["count"] for x in r["sectors"]} == dict(expect)
    assert r["members"] == len(items) == 4
    assert r["trade_date"] == D1


def test_口径(db):
    _seed(db)
    r = limit_moves_sector_counts(db, "limit_up")
    assert [(x["name"], x["count"]) for x in r["sectors"]] == [("人形机器人", 3), ("商业航天", 2), ("基础化工", 1)], \
        "按只数降序；ST、没关注的板块、别的日子都不算"
    assert limit_moves_sector_counts(db, "limit_down")["sectors"] == [{"name": "基础化工", "count": 1}]
    assert [x["name"] for x in limit_moves_sector_counts(db, "limit_up", date=D0)["sectors"]] == ["人形机器人"]


def test_只要前几名时并列的全带上(db):
    _seed(db)
    r = limit_moves_sector_counts(db, "limit_up", top=1)
    assert [x["name"] for x in r["sectors"]] == ["人形机器人"] and r["sectors_total"] == 3
    # 第 2 名「商业航天」2 只；砍到 top=2 不能把同只数的砍掉一半
    db.add(Sector(code="BK9999", name="低空经济", sector_type="concept", is_watched=True))
    db.flush()
    sec = db.query(Sector).filter(Sector.name == "低空经济").one()
    for code in ("000001", "000002"):
        st = db.query(Stock).filter(Stock.code == code).one()
        db.add(StockSectorRelation(stock_id=st.id, sector_id=sec.id))
    db.commit()
    assert {x["name"] for x in limit_moves_sector_counts(db, "limit_up", top=2)["sectors"]} == \
        {"人形机器人", "商业航天", "低空经济"}


def test_没有快照(db):
    r = limit_moves_sector_counts(db, "limit_up")
    assert r["members"] == 0 and r["sectors"] == [] and r["trade_date"] is None


def test_接口路径不被个股详情吃掉(db):
    from app.database import get_db
    from app.routers import stocks
    _seed(db)
    app = FastAPI()
    app.include_router(stocks.router)
    app.dependency_overrides[get_db] = lambda: db
    res = TestClient(app).get("/stocks/limit-moves/sector-counts?move_type=limit_up&top=5")
    assert res.status_code == 200
    assert res.json()["sectors"][0] == {"name": "人形机器人", "count": 3}
