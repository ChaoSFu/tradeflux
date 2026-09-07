from datetime import date as dt_date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional
from ..database import get_db
from ..models.stock import Stock, StockDailySnapshot
from ..services.strong_stock_service import (
    get_all_stocks, get_strong_pool, get_limit_moves_pool,
    get_limit_moves_trend, get_sector_limit_trend, get_sector_limit_trend_options,
    _enrich_stock_response,
)
from ..schemas.stock import (
    StockResponse, StockListResponse, StockDailySnapshotResponse,
    StockCreate, StockUpdate, LimitMoveTrendPoint,
    SectorLimitTrendPoint, SectorLimitTrendOption,
)

router = APIRouter(prefix="/stocks", tags=["stocks"])


def _parse_date(s: Optional[str]) -> Optional[dt_date]:
    """YYYY-MM-DD → date。**解析不出来就是 None（不传），不是报错也不是今天。**"""
    if not s:
        return None
    try:
        return dt_date.fromisoformat(s)
    except ValueError:
        return None


@router.get("", response_model=StockListResponse)
def list_stocks(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    in_strong_pool: Optional[bool] = None,
    sector_id: Optional[int] = None,
    search: Optional[str] = None,
    codes: Optional[str] = Query(None, description="逗号分隔的股票代码，按代码精确批量查询，忽略分页"),
    db: Session = Depends(get_db),
):
    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    return get_all_stocks(db, page, page_size, in_strong_pool, sector_id, search, code_list)


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


@router.get("/limit-moves/trend/sector-options", response_model=list[SectorLimitTrendOption])
def list_sector_limit_trend_options(
    days: int = Query(30, ge=5, le=60),
    db: Session = Depends(get_db),
):
    """近 N 个交易日出现过涨停/跌停的关注板块（按总次数降序），供走势图叠加选择。"""
    return get_sector_limit_trend_options(db, days)


@router.get("/limit-moves/trend/sector", response_model=list[SectorLimitTrendPoint])
def get_sector_trend(
    sector: str = Query(..., min_length=1, description="板块名称"),
    days: int = Query(30, ge=5, le=60),
    db: Session = Depends(get_db),
):
    """指定板块近 N 个交易日每日涨停/跌停数量（非ST，日期与全市场 trend 对齐、缺日补零）。"""
    return get_sector_limit_trend(db, sector, days)


class AdvanceLadderRow(BaseModel):
    from_board: int
    to_board: int
    previous_count: int          # 昨天该板位的涨停股总数
    observed_count: int          # 其中今天有快照的（= advanced + broken）
    advanced_count: int
    broken_count: int
    unknown_count: int           # 今天没有这只票的行。**既不是晋级也不是断板**
    advance_ratio: Optional[float] = None   # 分母是 observed；没观测到就是 None


class AdvanceLadderResponse(BaseModel):
    trade_date: Optional[dt_date] = None
    prev_date: Optional[dt_date] = None     # 交易日历上的前一个交易日
    rows: List[AdvanceLadderRow] = []
    notes: List[str] = []


class SectorContinuationRow(BaseModel):
    sector_id: int
    sector_name: str
    yesterday_limit_up_count: int
    today_continued_limit_up_count: int
    today_new_limit_up_count: int
    today_broken_count: int
    today_unknown_count: int
    today_limit_down_count: int
    continuation_ratio: Optional[float] = None


class SectorContinuationResponse(BaseModel):
    trade_date: Optional[dt_date] = None
    prev_date: Optional[dt_date] = None
    rows: List[SectorContinuationRow] = []
    notes: List[str] = []


@router.get("/limit-moves/advance-ladder", response_model=AdvanceLadderResponse)
def get_advance_ladder(
    date: Optional[str] = Query(None, description="交易日 YYYY-MM-DD，不传=最新"),
    db: Session = Depends(get_db),
):
    """
    分板位晋级：昨日 N 板的票今天有多少继续涨停。**只出计数和比率，不出接力分。**

    T-1 取交易日历上的前一个交易日，不是"库里上一条记录"。
    今天没有快照的票单独计入 unknown，不并进 broken——停牌和退市不是断板。
    """
    from ..services.limit_moves_analysis_service import compute_advance_ladder
    return compute_advance_ladder(db, _parse_date(date))


@router.get("/limit-moves/sector-continuation", response_model=SectorContinuationResponse)
def get_sector_continuation(
    date: Optional[str] = Query(None, description="交易日 YYYY-MM-DD，不传=最新"),
    db: Session = Depends(get_db),
):
    """
    板块跨日延续：昨天强的板块今天还强不强。**只出计数，不出延续分。**

    归组走 StockSectorRelation（关注板块），一只股票可同时属于多个板块，
    所以各行相加会大于全市场涨停数。
    """
    from ..services.limit_moves_analysis_service import compute_sector_continuation
    return compute_sector_continuation(db, _parse_date(date))


@router.get("/limit-moves", response_model=StockListResponse)
def list_limit_moves(
    page: int = Query(1, ge=1),
    page_size: int = Query(500, ge=1, le=1000),
    search: Optional[str] = None,
    move_type: Optional[str] = Query(None, pattern="^(limit_up|limit_down)$"),
    date: Optional[str] = Query(None, description="历史交易日 YYYY-MM-DD，不传=最新"),
    db: Session = Depends(get_db),
):
    """非ST股中指定交易日涨停/跌停的股票列表。move_type=limit_up|limit_down|不传(两者)；date 指定历史日。"""
    return get_limit_moves_pool(db, page, page_size, search, move_type,
                                date=_parse_date(date))


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
