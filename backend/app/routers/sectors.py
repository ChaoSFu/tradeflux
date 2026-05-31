from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session
from ..database import get_db
from ..models.sector import Sector, SectorDailySnapshot
from ..schemas.sector import SectorListResponse, SectorResponse, SectorDailySnapshotResponse
from ..services.sector_phase_service import get_all_sectors, get_sector_by_code
from ..services.sector_top_stocks_service import get_top_stocks


class TopStockItem(BaseModel):
    code: str
    name: str
    pct_today: Optional[float] = None
    pct_5d: Optional[float] = None
    pct_10d: Optional[float] = None
    pct_20d: Optional[float] = None
    pct_60d: Optional[float] = None


class TopStocksResponse(BaseModel):
    bk_code: str
    period: str
    stocks: List[TopStockItem]

router = APIRouter(prefix="/sectors", tags=["sectors"])


@router.get("", response_model=SectorListResponse)
def list_sectors(db: Session = Depends(get_db)):
    return get_all_sectors(db)


@router.get("/{code}", response_model=SectorResponse)
def get_sector(code: str, db: Session = Depends(get_db)):
    sector = get_sector_by_code(db, code)
    if not sector:
        raise HTTPException(status_code=404, detail=f"Sector {code} not found")
    return sector


@router.get("/{code}/top-stocks", response_model=TopStocksResponse)
def get_sector_top_stocks(
    code: str,
    period: str = Query("20d", regex="^(5d|10d|20d|60d)$"),
    limit: int = Query(10, ge=1, le=50),
):
    """
    返回板块内主板非ST个股，按指定周期涨幅降序排列。
    数据来源：东方财富 clist API（沪市主板 m:1+t:2 + 深市主板 m:0+t:6）。
    """
    stocks = get_top_stocks(bk_code=code, period=period, limit=limit)
    return TopStocksResponse(
        bk_code=code,
        period=period,
        stocks=[TopStockItem(**s.__dict__) for s in stocks],
    )


@router.get("/{code}/snapshots", response_model=list[SectorDailySnapshotResponse])
def get_sector_snapshots(
    code: str,
    days: int = 30,
    db: Session = Depends(get_db),
):
    sector = db.query(Sector).filter(Sector.code == code).first()
    if not sector:
        raise HTTPException(status_code=404, detail=f"Sector {code} not found")

    snaps = (
        db.query(SectorDailySnapshot)
        .filter(SectorDailySnapshot.sector_id == sector.id)
        .order_by(SectorDailySnapshot.date.desc())
        .limit(days)
        .all()
    )
    return list(reversed(snaps))
