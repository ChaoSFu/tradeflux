"""大盘趋势分析接口"""
from fastapi import APIRouter, Query

from ..schemas.market_index import MarketTrendResponse
from ..services.index_trend_service import get_market_trend
from ..services.windvane_service import WindvaneResponse, get_windvane

router = APIRouter(prefix="/market-trend", tags=["market-trend"])


@router.get("/indices", response_model=MarketTrendResponse)
def list_index_trends(refresh: bool = Query(False, description="跳过缓存强制刷新")):
    """核心指数（上证/深成/创业板/科创50/北证50）趋势分析：均线体系 + 趋势强度分 + 近期信号。"""
    return get_market_trend(force_refresh=refresh)


@router.get("/windvane", response_model=WindvaneResponse)
def get_market_windvane(refresh: bool = Query(False, description="跳过缓存强制刷新")):
    """市场风向标：融资融券（两融余额/净买入 vs 上证）、涨跌统计（三市分布）、成交分析（近60日）。"""
    return get_windvane(force_refresh=refresh)
