"""大盘趋势分析接口"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.market_index import MarketTrendResponse
from ..services.index_trend_service import get_market_trend
from ..services.windvane_service import (
    WindvaneResponse, get_windvane, get_updown_dates, get_updown_series, UpDownSeriesPoint,
)

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
    margin_range: str = Query("6m", description="融资融券图表时间周期：6m/1y/3y/5y/all"),
    updown_date: str | None = Query(None, description="涨跌统计指定日期 YYYY-MM-DD，不传=最新"),
    db: Session = Depends(get_db),
):
    """市场风向标：融资融券/涨跌统计/成交分析（数据读库，daily_update 每日同步）。"""
    return get_windvane(db, force_refresh=refresh, margin_range=margin_range, updown_date=updown_date)


@router.get("/updown-dates", response_model=list[str])
def list_updown_dates(db: Session = Depends(get_db)):
    """涨跌统计可选历史日期列表（升序），供前端下拉选择使用。"""
    return get_updown_dates(db)


@router.get("/updown-series", response_model=list[UpDownSeriesPoint])
def list_updown_series(
    days: int = Query(120, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """涨跌家数时间序列（升序），供主图叠加涨跌统计使用。"""
    return get_updown_series(db, days=days)
