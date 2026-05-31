from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import Optional
from ..database import get_db
from ..schemas.signal import SignalListResponse
from ..services.weak_to_strong_service import get_signals

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("", response_model=SignalListResponse)
def list_signals(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    signal_type: Optional[str] = None,
    risk_level: Optional[str] = None,
    stock_id: Optional[int] = None,
    sector_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    return get_signals(db, page, page_size, signal_type, risk_level, stock_id, sector_id)
