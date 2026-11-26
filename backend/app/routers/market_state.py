from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from ..database import get_db
from ..schemas.market_state import MarketStateResponse, MarketHistoryPoint, ProfitEffectResponse, StrongPoolLimitPoint
from ..services.market_state_service import (
    get_current_market_state, get_market_history, get_profit_effect, get_strong_pool_limit_history,
)

router = APIRouter(prefix="/market-state", tags=["market-state"])


@router.get("", response_model=MarketStateResponse)
def current_market_state(db: Session = Depends(get_db)):
    return get_current_market_state(db)


@router.get("/history", response_model=list[MarketHistoryPoint])
def market_state_history(
    days: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    return get_market_history(db, days)


@router.get("/strong-pool-limits", response_model=list[StrongPoolLimitPoint])
def strong_pool_limits(
    days: int = Query(30, ge=2, le=90),
    db: Session = Depends(get_db),
):
    """强势池每天的涨停 / 跌停只数（顶栏那一格的历史序列；今天的数另取 profit-effect）"""
    return get_strong_pool_limit_history(db, days)


@router.get("/profit-effect", response_model=ProfitEffectResponse)
def profit_effect(
    min_stocks: int = Query(3, ge=1, description="最少强势股数量（与板块强势分布过滤一致）"),
    db: Session = Depends(get_db),
):
    return get_profit_effect(db, min_stocks=min_stocks)
