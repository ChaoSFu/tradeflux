"""成交额概览接口"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.turnover import TurnoverOverviewResponse
from ..services.turnover_service import get_turnover_overview

router = APIRouter(prefix="/turnover", tags=["turnover"])


@router.get("/overview", response_model=TurnoverOverviewResponse)
def turnover_overview(
    refresh: bool = Query(False, description="强制重新同步远端数据"),
    db: Session = Depends(get_db),
):
    """成交额概览：当日成交额前列股票 + 板块赚钱效应 + 新进标签（数据读库，daily_update 每日同步）。"""
    return get_turnover_overview(db, force_refresh=refresh)
