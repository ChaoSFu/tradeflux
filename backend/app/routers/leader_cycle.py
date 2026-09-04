"""高标龙头生命周期 —— 事实层接口。"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models.leader_cycle import LeaderCycleSnapshot
from ..models.sector import Sector
from ..models.stock import Stock

router = APIRouter(prefix="/leader-cycle", tags=["leader-cycle"])


class LeaderCycleItem(BaseModel):
    code: str
    name: Optional[str] = None
    sector_name: Optional[str] = None

    peak_board_count: Optional[int] = None      # 本轮周期最高连板
    board_count_60d: Optional[int] = None       # 60日最高（历史辨识度，另存不混用）
    cycle_start_date: Optional[date] = None
    cycle_peak_date: Optional[date] = None
    break_date: Optional[date] = None           # None = 仍在连板中
    days_since_break: Optional[int] = None

    peak_price: Optional[float] = None
    post_break_high: Optional[float] = None
    post_break_low: Optional[float] = None
    latest_close: Optional[float] = None
    peak_drawdown: Optional[float] = None

    ma5: Optional[float] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma30: Optional[float] = None
    ma_window_complete: Optional[bool] = None

    rs_market_10: Optional[float] = None
    rs_market_20: Optional[float] = None
    rs_market_60: Optional[float] = None
    rs_sector_10: Optional[float] = None
    rs_sector_20: Optional[float] = None
    rs_sector_60: Optional[float] = None

    volume: Optional[float] = None
    amount: Optional[float] = None
    turnover_rate: Optional[float] = None

    missing_days: Optional[int] = None
    peak_board_confident: Optional[bool] = None


class LeaderCycleResponse(BaseModel):
    trade_date: Optional[date] = None
    running: List[LeaderCycleItem]      # 仍在连板中（break_date 为空）
    broken: List[LeaderCycleItem]       # 已断板，按距断板交易日数升序
    # 覆盖率：每一项都是"这个事实我们掌握了多少"。**必须暴露给前端**——
    # 一个 63% 覆盖率的字段和一个 100% 的字段，读图的人有权知道区别
    coverage: dict
    scope_note: str


@router.get("", response_model=LeaderCycleResponse)
def get_leader_cycle(
    trade_date: Optional[date] = Query(None, description="不传=最新有数据的交易日"),
    db: Session = Depends(get_db),
):
    """
    高标龙头生命周期的**事实层**。这里没有任何状态判定（RUNNING/RECLAIMING 之类）
    ——那是状态机的事，等事实层积累出足够历史、能验证阈值之后再做。

    分组只按一个纯事实切：`break_date` 有没有。不做 D+1~3 / D+4~10 这类分桶，
    因为桶边界本身就是拍出来的阈值，而这一层刻意不引入任何阈值。
    """
    if trade_date is None:
        trade_date = db.query(LeaderCycleSnapshot.date).order_by(
            LeaderCycleSnapshot.date.desc()).limit(1).scalar()
    if trade_date is None:
        return LeaderCycleResponse(trade_date=None, running=[], broken=[],
                                   coverage={}, scope_note="暂无数据")

    rows = db.query(LeaderCycleSnapshot).filter(
        LeaderCycleSnapshot.date == trade_date).all()
    if not rows:
        return LeaderCycleResponse(trade_date=trade_date, running=[], broken=[],
                                   coverage={}, scope_note="该交易日暂无数据")

    stocks = {s.code: s for s in db.query(Stock).filter(
        Stock.code.in_([r.stock_code for r in rows])).all()}
    sec_names = {sid: name for sid, name in db.query(Sector.id, Sector.name).all()}

    items: List[LeaderCycleItem] = []
    for r in rows:
        st = stocks.get(r.stock_code)
        items.append(LeaderCycleItem(
            code=r.stock_code,
            name=st.name if st else None,
            sector_name=(sec_names.get(st.primary_sector_id)
                         if st and st.primary_sector_id else None),
            **{k: getattr(r, k) for k in (
                "peak_board_count", "board_count_60d", "cycle_start_date",
                "cycle_peak_date", "break_date", "days_since_break",
                "peak_price", "post_break_high", "post_break_low", "latest_close",
                "peak_drawdown", "ma5", "ma10", "ma20", "ma30", "ma_window_complete",
                "rs_market_10", "rs_market_20", "rs_market_60",
                "rs_sector_10", "rs_sector_20", "rs_sector_60",
                "volume", "amount", "turnover_rate",
                "missing_days", "peak_board_confident")},
        ))

    running = [i for i in items if i.break_date is None]
    broken = [i for i in items if i.break_date is not None]
    # 连板中：板数高者在前。已断板：离断板越近越靠前——那是结构还没走完的时候
    running.sort(key=lambda i: -(i.peak_board_count or 0))
    broken.sort(key=lambda i: (i.days_since_break if i.days_since_break is not None
                               else 10 ** 6, -(i.peak_board_count or 0)))

    n = len(items)
    cov = {
        "total": n,
        "peak_board_confident": sum(1 for i in items if i.peak_board_confident),
        "ma_window_complete": sum(1 for i in items if i.ma_window_complete),
        "rs_market": sum(1 for i in items if i.rs_market_20 is not None),
        "rs_sector": sum(1 for i in items if i.rs_sector_20 is not None),
        "turnover_rate": sum(1 for i in items if i.turnover_rate is not None),
        "volume": sum(1 for i in items if i.volume is not None),
    }
    return LeaderCycleResponse(
        trade_date=trade_date, running=running, broken=broken, coverage=cov,
        scope_note="高标池 = 近60个交易日最高连板 ≥ 4；不含 ST、退市整理期、北交所",
    )
