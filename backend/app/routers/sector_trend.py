"""
板块趋势 · 主升板块雷达（V1 /sector-trend 页面）。只读库，不向外部发请求。

规则见 services/sector_mainline_service.py 和 docs/SECTOR_MAINLINE.md。
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.sector_mainline_service import (
    get_sector_mainline_detail, get_sector_mainline_state,
)

router = APIRouter(prefix="/sector-trend", tags=["sector-trend"])


@router.get("")
def list_sector_trend(
    as_of: Optional[date] = Query(None, alias="date",
                                  description="状态基准日上限（复盘用），默认最近一个已收盘的交易日"),
    db: Session = Depends(get_db),
):
    """全体关注板块的当前状态。每个板块只带当天的事实和最近 5 天的状态轨迹，不带历史序列。"""
    return get_sector_mainline_state(db, as_of=as_of)


@router.get("/{code}")
def get_sector_trend_detail(
    code: str,
    as_of: Optional[date] = Query(None, alias="date"),
    db: Session = Depends(get_db),
):
    """单个板块：60 根指数日线 + 均线、30 天成分股生态、近 10 天每天的状态与四道闸。"""
    r = get_sector_mainline_detail(db, code, as_of=as_of)
    if r is None:
        raise HTTPException(status_code=404, detail=f"{code} 不是关注板块，或者还没有可用的状态基准日")
    return r
