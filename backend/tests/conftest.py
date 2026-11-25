"""
共享测试基建（2026-08-25新增）。

用 SQLite 内存库真的建表、真的跑 upsert/聚合——本仓库前几轮的教训是：只测纯函数
会漏掉落库这一层的语义错误（凯莱英那次事故就发生在"哪些字段在什么条件下允许写"
这个判断上，纯函数测试全绿也照样出事）。

只建用到的表：market_breadth_daily 有 JSONB 列，SQLite 建不了；其余表都是
Integer/Float/Boolean/Date/Time/String/Text，可以直接建。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.stock import Stock, StockDailySnapshot, StockSuspensionDay
from app.models.sector import Sector, StockSectorRelation, SectorDailySnapshot
from app.models.limit_up_detail import LimitUpDailyDetail, BrokenBoardDailyDetail
from app.models.app_config import AppConfig
from app.models.regulatory import RegulatoryStatusDaily, RegulatoryUnusual
from app.models.market_index import IndexDailySnapshot, SectorIndexDaily
from app.models.leader_cycle import LeaderCycleSnapshot
from app.models.turnover_pool import TurnoverPoolDaily
from app.models.market_effect import CohortOutcomeQuote
from app.models.trade_journal import TradeJournal
from app.models.pre_trade_check import PreTradeCheck
from app.models.weak_to_strong_radar import (
    WeakToStrongCandidate, WeakToStrongEvent, WeakToStrongDiscoveryRun,
)
from app.models.large_cap import LargeCapMarketDaily, LargeCapStockBoards, LargeCapSyncBatch
from app.models.stock_day_fact import StockDayFact
from app.models.market_recap import (
    LeaderActivationEvent, LeaderRelation, LeaderResponseDaily, MarketPopularCoreDaily, MarketPopularCoreRun,
    MarketRecap, NextDayPlan, RecapLockedVersion, RecapWatchStock,
)

_TABLES = [
    Stock.__table__, StockDailySnapshot.__table__, StockSuspensionDay.__table__,
    Sector.__table__, StockSectorRelation.__table__, SectorDailySnapshot.__table__,
    LimitUpDailyDetail.__table__, BrokenBoardDailyDetail.__table__,
    AppConfig.__table__,
    RegulatoryStatusDaily.__table__,
    RegulatoryUnusual.__table__,
    IndexDailySnapshot.__table__,
    SectorIndexDaily.__table__,
    LeaderCycleSnapshot.__table__,
    TurnoverPoolDaily.__table__,
    WeakToStrongCandidate.__table__, WeakToStrongEvent.__table__,
    WeakToStrongDiscoveryRun.__table__,
    CohortOutcomeQuote.__table__,
    TradeJournal.__table__, PreTradeCheck.__table__,
    LargeCapMarketDaily.__table__, LargeCapSyncBatch.__table__, LargeCapStockBoards.__table__,
    StockDayFact.__table__,
    MarketRecap.__table__, RecapWatchStock.__table__, LeaderRelation.__table__,
    LeaderResponseDaily.__table__, NextDayPlan.__table__, RecapLockedVersion.__table__,
    MarketPopularCoreRun.__table__, MarketPopularCoreDaily.__table__, LeaderActivationEvent.__table__,
]


@pytest.fixture
def db():
    # StaticPool + check_same_thread=False：FastAPI 的 TestClient 在另一个线程里跑
    # 请求，默认的 SQLite 连接不允许跨线程，而且每开一个新连接就是一个新的空内存库。
    # 用同一个连接才能让"测试里建的数据"和"接口读到的数据"是同一个库。
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine, tables=_TABLES)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _no_on_demand_prompt(monkeypatch):
    """
    2026-11-25 起每日复盘 / 人气核心池会按需问选股 prompt（当天的日期才问）。测试默认一律不问、不探市场时间，
    要测按需取数的用例自己把 might_be_current 打开、并把 prompt_api 换成假的。
    选股请求的跨进程节流在测试里关掉（不然每次请求都要等 1 秒）。
    """
    monkeypatch.setattr("app.services.stock_day_facts.might_be_current", lambda d: False)
    monkeypatch.setattr("app.services.prompt_api.MIN_INTERVAL_S", 0)
