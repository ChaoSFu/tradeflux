"""大盘趋势分析接口"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.market_index import MarketTrendResponse
from ..services.index_trend_service import get_market_trend
from ..services.windvane_service import WindvaneResponse, get_windvane

router = APIRouter(prefix="/market-trend", tags=["market-trend"])


@router.get("/indices", response_model=MarketTrendResponse)
def list_index_trends(
    refresh: bool = Query(False, description="强制重新同步远端数据"),
    db: Session = Depends(get_db),
):
    """核心指数趋势分析（数据读库，daily_update 每日同步；refresh=true 强制重新同步）。"""
    return get_market_trend(db, force_refresh=refresh)


@router.get("/windvane", response_model=WindvaneResponse)
def get_market_windvane(
    refresh: bool = Query(False, description="强制重新同步远端数据"),
    db: Session = Depends(get_db),
):
    """市场风向标：融资融券/涨跌统计/成交分析（数据读库，daily_update 每日同步）。"""
    return get_windvane(db, force_refresh=refresh)
