from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional
from ..database import get_db
from ..models.stock import Stock, StockDailySnapshot
from ..services.strong_stock_service import (
    get_all_stocks, get_strong_pool, get_limit_moves_pool,
    get_limit_moves_trend, _enrich_stock_response,
)
from ..schemas.stock import (
    StockResponse, StockListResponse, StockDailySnapshotResponse,
    StockCreate, StockUpdate, LimitMoveTrendPoint,
)

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("", response_model=StockListResponse)
def list_stocks(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    in_strong_pool: Optional[bool] = None,
    sector_id: Optional[int] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    return get_all_stocks(db, page, page_size, in_strong_pool, sector_id, search)


@router.get("/strong-pool", response_model=StockListResponse)
def list_strong_pool(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    sector_id: Optional[int] = None,
    phase: Optional[str] = None,
    search: Optional[str] = None,
    sort_by: str = Query("leader_score", regex="^(leader_score|risk_score|emotion_score|board_count_60d|board_down_count_60d|limit_up_days_60d|limit_up_days_20d|limit_up_days_10d|pct_change_60d|pct_change_20d|pct_change_10d)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    db: Session = Depends(get_db),
):
    return get_strong_pool(db, page, page_size, sector_id, phase, search, sort_by, sort_order)


@router.get("/limit-moves/trend", response_model=list[LimitMoveTrendPoint])
def list_limit_moves_trend(
    days: int = Query(20, ge=5, le=60),
    db: Session = Depends(get_db),
):
    """近 N 个交易日每日涨停/跌停数量趋势（非ST）。"""
    return get_limit_moves_trend(db, days)


@router.get("/limit-moves", response_model=StockListResponse)
def list_limit_moves(
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=1000),
    search: Optional[str] = None,
    move_type: Optional[str] = Query(None, pattern="^(limit_up|limit_down)$"),
    db: Session = Depends(get_db),
):
    """非ST股中今日涨停/跌停的股票列表。move_type=limit_up|limit_down|不传(两者)。"""
    return get_limit_moves_pool(db, page, page_size, search, move_type)


@router.get("/{code}", response_model=StockResponse)
def get_stock(code: str, db: Session = Depends(get_db)):
    stock = db.query(Stock).filter(Stock.code == code).first()
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock {code} not found")
    return _enrich_stock_response(stock, db)


@router.get("/{code}/snapshots", response_model=list[StockDailySnapshotResponse])
def get_stock_snapshots(
    code: str,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
):
    stock = db.query(Stock).filter(Stock.code == code).first()
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock {code} not found")

    snaps = (
        db.query(StockDailySnapshot)
        .filter(StockDailySnapshot.stock_id == stock.id)
        .order_by(StockDailySnapshot.date.desc())
        .limit(days)
        .all()
    )
    return list(reversed(snaps))


@router.post("", response_model=StockResponse, status_code=201)
def create_stock(payload: StockCreate, db: Session = Depends(get_db)):
    existing = db.query(Stock).filter(Stock.code == payload.code).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Stock {payload.code} already exists")
    stock = Stock(**payload.model_dump())
    db.add(stock)
    db.commit()
    db.refresh(stock)
    return _enrich_stock_response(stock, db)


@router.patch("/{code}", response_model=StockResponse)
def update_stock(code: str, payload: StockUpdate, db: Session = Depends(get_db)):
    stock = db.query(Stock).filter(Stock.code == code).first()
    if not stock:
        raise HTTPException(status_code=404, detail=f"Stock {code} not found")
    for field, val in payload.model_dump(exclude_none=True).items():
        setattr(stock, field, val)
    db.commit()
    db.refresh(stock)
    return _enrich_stock_response(stock, db)
