"""
涨停板块雷达接口测试（2026-08-25新增）。

其中 test_refresh_only_syncs_limit_up_details 是需求里明确要求的护栏：手动刷新
必须只做涨停明细同步，绝不能顺带触发 daily_update / 全量K线 / Market State /
弱转强雷达 / 板块全量同步 / AI点评。
"""
from datetime import date, time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.models.limit_up_detail import LimitUpDailyDetail
from app.models.sector import Sector, StockSectorRelation
from app.models.stock import Stock
from app.routers import limit_up_radar
from app.services.limit_up_detail_fetcher import LimitUpDetail

TODAY = date(2026, 8, 25)


@pytest.fixture
def client(db):
    app = FastAPI()
    app.include_router(limit_up_radar.router)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _seed_sector_with_limit_up(db):
    sec = Sector(code="中药", name="中药", is_watched=True, phase=3)
    db.add(sec); db.flush()
    anchor = Stock(code="600664", name="哈药股份", market="SH", limit_up_days_60d=6)
    attacker = Stock(code="002412", name="汉森制药", market="SZ", limit_up_days_10d=2)
    db.add_all([anchor, attacker]); db.flush()
    db.add_all([
        StockSectorRelation(stock_id=anchor.id, sector_id=sec.id, is_leader=True),
        StockSectorRelation(stock_id=attacker.id, sector_id=sec.id),
    ])
    db.add(LimitUpDailyDetail(
        stock_id=attacker.id, stock_code="002412", stock_name="汉森制药",
        trade_date=TODAY, board_count=2, first_limit_time=time(9, 45),
        last_limit_time=time(9, 45), seal_amount=1.17e8, pct_change=10.0,
        limit_reason="创新药+业绩增长", source="eastmoney", source_trade_date=TODAY,
        refreshed_at=__import__("datetime").datetime(2026, 8, 25, 14, 32, 18),
    ))
    db.commit()
    return sec


def test_get_radar_returns_sectors_with_core_and_freshness(db, client):
    _seed_sector_with_limit_up(db)
    r = client.get("/limit-up-radar", params={"date": "2026-08-25"})
    assert r.status_code == 200
    body = r.json()

    assert body["trade_date"] == "2026-08-25"
    assert body["refreshed_at"] == "2026-08-25T14:32:18"   # 页面必须能显示抓取时刻
    assert body["source"] == "eastmoney"
    assert body["summary"]["limit_up_count"] == 1

    card = body["sectors"][0]
    assert card["sector_name"] == "中药"
    assert card["today_limit_up_stocks"][0]["code"] == "002412"
    assert card["today_limit_up_stocks"][0]["limit_reason"] == "创新药+业绩增长"
    # 今天没涨停的板块龙头必须还在
    assert [c["code"] for c in card["core_stocks"]] == ["600664"]


def test_include_core_false_drops_core_section(db, client):
    _seed_sector_with_limit_up(db)
    body = client.get("/limit-up-radar", params={"date": "2026-08-25", "include_core": "false"}).json()
    assert body["sectors"][0]["core_stocks"] == []


def test_core_thresholds_are_overridable_via_query(db, client):
    _seed_sector_with_limit_up(db)
    # 把60日门槛提到7，哈药(6次)就只剩"板块龙头"这一条召回理由
    body = client.get("/limit-up-radar", params={"date": "2026-08-25", "core_60d_min": 7}).json()
    core = body["sectors"][0]["core_stocks"][0]
    assert core["core_roles"] == ["SECTOR_LEADER"]


def test_date_defaults_to_latest_day_with_data(db, client):
    _seed_sector_with_limit_up(db)
    body = client.get("/limit-up-radar").json()
    assert body["trade_date"] == "2026-08-25"


def test_empty_day_returns_empty_not_500(db, client):
    body = client.get("/limit-up-radar", params={"date": "2026-01-01"}).json()
    assert body["sectors"] == []
    assert body["summary"]["limit_up_count"] == 0
    assert body["refreshed_at"] is None


def test_refresh_only_syncs_limit_up_details(db, client):
    """
    需求明确要求的护栏：手动刷新只能同步涨停明细。
    这里给所有"绝不能被触发"的重活打桩，任一被调用即测试失败。
    """
    st = Stock(code="002412", name="汉森制药", market="SZ")
    db.add(st); db.commit()

    def _boom(name):
        def _f(*a, **kw):
            raise AssertionError(f"手动刷新绝不能触发 {name}")
        return _f

    with patch("app.services.limit_up_detail_service.fetch_limit_up_details",
               return_value=([LimitUpDetail(code="002412", name="汉森制药", board_count=1)], [], [])), \
         patch("app.services.limit_up_detail_service.fetch_core_recall_details",
               return_value={}), \
         patch("app.services.eastmoney_fetcher.fetch_klines_batch", _boom("全量K线")), \
         patch("app.services.eastmoney_fetcher.fetch_strong_pool_codes", _boom("强势股选股API")), \
         patch("app.services.eastmoney_fetcher.fetch_stock_quotes_batch", _boom("实时行情批量拉取")), \
         patch("app.services.market_state_service.get_current_market_state", _boom("Market State重算")), \
         patch("app.services.w2s_refresh_service.run_refresh", _boom("弱转强雷达刷新")):
        r = client.post("/limit-up-radar/refresh", params={"date": "2026-08-25"})

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["limit_up_written"] == 1
    assert body["refreshed_at"] is not None


def test_refresh_failure_keeps_existing_data_and_reports_last_success(db, client):
    """
    外部接口失败时：不删已有数据、不返回500、明确告诉页面上次成功是什么时候。
    stale 数据加上诚实的时间戳，好过空页面或一份伪装成最新的数据。
    """
    _seed_sector_with_limit_up(db)
    with patch("app.services.limit_up_detail_service.fetch_limit_up_details",
               side_effect=TimeoutError("东财超时")), \
         patch("app.services.limit_up_detail_service.fetch_core_recall_details",
               return_value={}):
        r = client.post("/limit-up-radar/refresh", params={"date": "2026-08-25"})

    body = r.json()
    assert r.status_code == 200
    assert body["ok"] is False
    assert "TimeoutError" in body["error"]
    assert body["last_success_at"] == "2026-08-25T14:32:18"
    # 数据还在
    assert db.query(LimitUpDailyDetail).count() == 1
    assert client.get("/limit-up-radar", params={"date": "2026-08-25"}).json()["summary"]["limit_up_count"] == 1


def test_group_mode_primary_is_accepted(db, client):
    sec = _seed_sector_with_limit_up(db)
    st = db.query(Stock).filter(Stock.code == "002412").one()
    st.primary_sector_id = sec.id
    db.commit()
    body = client.get("/limit-up-radar", params={"date": "2026-08-25", "group_mode": "primary"}).json()
    assert [s["sector_name"] for s in body["sectors"]] == ["中药"]


def test_invalid_group_mode_is_rejected(db, client):
    assert client.get("/limit-up-radar", params={"group_mode": "随便写"}).status_code == 422
