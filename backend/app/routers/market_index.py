"""大盘趋势分析接口"""
from fastapi import APIRouter, Query

from ..schemas.market_index import MarketTrendResponse
from ..services.index_trend_service import get_market_trend

router = APIRouter(prefix="/market-trend", tags=["market-trend"])


@router.get("/indices", response_model=MarketTrendResponse)
def list_index_trends(refresh: bool = Query(False, description="跳过缓存强制刷新")):
    """核心指数（上证/深成/创业板/科创50/北证50）趋势分析：均线体系 + 趋势强度分 + 近期信号。"""
    return get_market_trend(force_refresh=refresh)
