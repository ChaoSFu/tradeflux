"""破局雷达 / Speculation Regime Radar 接口。"""
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..services.speculation_radar_service import (
    compute_height_series, get_ladder_members, FRONTIER_WINDOW, LADDER_MAX,
)

router = APIRouter(prefix="/speculation-radar", tags=["speculation-radar"])


class HeightPointOut(BaseModel):
    date: str
    height: int
    frontier: Optional[int] = None
    # None = 不知道（窗口里有交易日缺连板数据，上沿可能被低估）。
    # 没证明突破 ≠ 已证明没突破
    is_breakout: Optional[bool] = None
    has_data: bool = True
    frontier_covered: int = 0
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


class LadderMember(BaseModel):
    """
    **除主板块外，每一项都取那一天那行快照的值**，不是 Stock 表的当前值。
    点开 7 月某天的格子，看到的必须是它当时的「近60日涨停 5 次」。
    """
    code: str
    name: Optional[str] = None
    sector_name: Optional[str] = None      # 当前归属（板块关系没有逐日落库）
    board_count: int
    pct_change: Optional[float] = None
    is_one_word: bool = False
    turnover_rate: Optional[float] = None  # None = 那天没拿到，不是 0
    amount: Optional[float] = None         # 成交额（元）
    board_count_60d: Optional[int] = None
    limit_up_days_10d: Optional[int] = None
    limit_up_days_20d: Optional[int] = None
    limit_up_days_60d: Optional[int] = None
    pct_change_10d: Optional[float] = None
    pct_change_20d: Optional[float] = None
    pct_change_60d: Optional[float] = None


class LadderMembersResponse(BaseModel):
    date: str
    bucket: str
    members: List[LadderMember]
    warnings: List[str] = []


@router.get("/ladder-members", response_model=LadderMembersResponse)
def get_ladder_members_api(
    date: str = Query(..., description="交易日 YYYY-MM-DD"),
    bucket: str = Query(..., description='梯队档位，如 "5" 或 "8+"（封顶档）'),
    days: int = Query(66, ge=5, le=250),
    db: Session = Depends(get_db),
):
    """
    某一天某个梯队档位里到底是哪几只票。热力图点格子用。

    跟热力图的格子数**同源**（都走 `_build_by_date`）——列表长度必须等于格子里
    的数字。两边各写一套查询，迟早出现"格子写 3 只、点开列出 4 只"。
    """
    from datetime import date as _date
    try:
        target = _date.fromisoformat(date)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"日期格式不对：{date}")
    members, warns = get_ladder_members(db, target, bucket, days=days)
    return LadderMembersResponse(date=date, bucket=bucket,
                                 members=[LadderMember(**m) for m in members],
                                 warnings=warns)
