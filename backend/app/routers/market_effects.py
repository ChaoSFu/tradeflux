from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import date as date_cls

from ..database import get_db
from ..schemas.market_effect import MarketEffectDailyResponse, MarketEffectHistoryPoint, CohortMember
from ..models.market_effect import MarketEffectDaily
from ..services.market_effect_service import (
    get_or_compute, get_latest_trade_date, get_history,
    list_cohort_members, _prev_trading_date, COHORT_LABELS,
)

router = APIRouter(prefix="/market-effects", tags=["market-effects"])


def _to_response(row: MarketEffectDaily) -> MarketEffectDailyResponse:
    return MarketEffectDailyResponse(
        trade_date=row.trade_date,
        profit_strength=row.profit_strength,
        loss_strength=row.loss_strength,
        quadrant=row.quadrant,
        lifecycle_state=row.lifecycle_state,
        breadth_source=row.breadth_source,
        coverage_ratio=row.coverage_ratio,
        cohorts=row.cohorts_json or {},
        evidence=row.evidence_json or [],
        summary=row.summary or "",
        formula_version=row.formula_version,
    )


@router.get("/latest", response_model=MarketEffectDailyResponse)
def latest_market_effect(db: Session = Depends(get_db)):
    trade_date = get_latest_trade_date(db)
    if trade_date is None:
        raise HTTPException(status_code=404, detail="暂无可用交易日数据")
    row = get_or_compute(db, trade_date)
    return _to_response(row)


@router.get("/history", response_model=list[MarketEffectHistoryPoint])
def market_effect_history(
    days: int = Query(60, ge=1, le=250),
    db: Session = Depends(get_db),
):
    rows = get_history(db, days)
    return [
        MarketEffectHistoryPoint(
            trade_date=r.trade_date,
            profit_strength=r.profit_strength,
            loss_strength=r.loss_strength,
            quadrant=r.quadrant,
            lifecycle_state=r.lifecycle_state,
            breadth_source=r.breadth_source,
        )
        for r in rows
    ]


@router.get("/{trade_date}/cohorts/{cohort_type}/members", response_model=list[CohortMember])
def cohort_members(trade_date: date_cls, cohort_type: str, db: Session = Depends(get_db)):
    if cohort_type not in COHORT_LABELS:
        raise HTTPException(status_code=404, detail=f"未知冻结群体类型: {cohort_type}")
    cohort_date = _prev_trading_date(db, trade_date)
    if cohort_date is None:
        return []
    return list_cohort_members(db, cohort_date, trade_date, cohort_type)


@router.get("/{trade_date}", response_model=MarketEffectDailyResponse)
def market_effect_by_date(trade_date: date_cls, db: Session = Depends(get_db)):
    row = get_or_compute(db, trade_date)
    return _to_response(row)
