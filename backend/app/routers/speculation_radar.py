"""破局雷达 / Speculation Regime Radar 接口。"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.speculation_radar_service import (
    compute_height_series, FRONTIER_WINDOW, LADDER_MAX,
)

router = APIRouter(prefix="/speculation-radar", tags=["speculation-radar"])


class HeightPointOut(BaseModel):
    date: str
    height: int
    frontier: Optional[int] = None
    is_breakout: bool
    near_top_count: int
    multi_board_count: int
    limit_up_count: int
    ladder_count: int
    ladder: Dict[str, int]


class HeightSeriesResponse(BaseModel):
    frontier_window: int
    ladder_max: int
    points: List[HeightPointOut]
    # 剔除了哪些记录必须让调用方看得见——分不清"这天真没有高板"和"这天的高板
    # 被我们剔掉了"，等于没有监控
    warnings: List[str]
    # 口径声明：两个选股 prompt 都写了「非ST」，这里的市场高度不含 ST 股
    scope_note: str


@router.get("/height", response_model=HeightSeriesResponse)
def get_height_series(
    days: int = Query(60, ge=5, le=250, description="展示多少个交易日"),
    db: Session = Depends(get_db),
):
    """市场高度前沿曲线 + 连板梯队。只读快照，零外部请求。"""
    points, warnings = compute_height_series(db, days=days)
    return HeightSeriesResponse(
        frontier_window=FRONTIER_WINDOW,
        ladder_max=LADDER_MAX,
        points=[HeightPointOut(**p.__dict__) for p in points],
        warnings=warnings,
        scope_note="不含 ST 股（选股口径为「非ST」；ST 是 5% 板，与主板不可比）",
    )
