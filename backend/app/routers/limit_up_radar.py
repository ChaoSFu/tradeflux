"""
涨停板块雷达接口（2026-08-25新增）。

刷新接口刻意做得非常窄：只同步涨停/炸板明细这一件事。它**不会**触发 daily_update、
不会拉65日K线、不会重算 Market State、不碰弱转强雷达、不做板块全量同步、不调AI。
目的是让用户在盘中能以极低代价把最新封板状态刷到页面上。

也不做自动轮询——TradeFlux 当前是 manual-first，只在用户主动点击时打外部接口，
避免不必要的限流/封禁风险。
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..schemas.limit_up_radar import (
    GroupMode, LimitUpRadarRefreshResponse, LimitUpRadarResponse,
)
from ..services import limit_up_radar_service as radar
from ..services.limit_up_detail_fetcher import SOURCE_NAME
from ..services.limit_up_detail_service import (
    get_last_refreshed, get_latest_detail_date, sync_limit_up_details,
    sync_core_recall, refresh_radar_scores,
)

router = APIRouter(prefix="/limit-up-radar", tags=["limit-up-radar"])


def _resolve_date(db: Session, trade_date: Optional[date]) -> date:
    """不传日期时用库里最新有明细的交易日；一条都没有时退回今天（页面会显示空态）。"""
    if trade_date:
        return trade_date
    return get_latest_detail_date(db) or date.today()


@router.get("", response_model=LimitUpRadarResponse)
def get_limit_up_radar(
    trade_date: Optional[date] = Query(None, alias="date", description="交易日，默认取库里最新一天"),
    include_core: bool = Query(True, description="是否补全板块核心（今日未涨停但历史强势）"),
    group_mode: GroupMode = Query("all_watched_sectors", description="板块归组方式"),
    core_10d_min: int = Query(radar.DEFAULT_CORE_10D_MIN, ge=1, le=10),
    core_20d_min: int = Query(radar.DEFAULT_CORE_20D_MIN, ge=1, le=20),
    core_60d_min: int = Query(radar.DEFAULT_CORE_60D_MIN, ge=1, le=60),
    core_max_board_min: int = Query(radar.DEFAULT_CORE_MAX_BOARD_MIN, ge=1, le=20),
    max_core_per_sector: int = Query(radar.DEFAULT_MAX_CORE_PER_SECTOR, ge=1, le=50,
                                     description='每板块核心锚展示上限（不影响召回，core_count 仍是真实总数）'),
    min_limit_up: int = Query(radar.DEFAULT_MIN_LIMIT_UP, ge=0, le=50,
                             description='板块最少涨停只数'),
    min_board_height: int = Query(radar.DEFAULT_MIN_BOARD_HEIGHT, ge=0, le=20,
                                  description='板块最低连板高度（与上面是AND关系）'),
    db: Session = Depends(get_db),
):
    """涨停板块雷达：按板块聚合当日涨停结构 + 板块核心锚。纯读库，不打外部接口。"""
    target = _resolve_date(db, trade_date)
    result = radar.build_radar(
        db, target,
        include_core=include_core, group_mode=group_mode,
        core_10d_min=core_10d_min, core_20d_min=core_20d_min,
        core_60d_min=core_60d_min, core_max_board_min=core_max_board_min,
        max_core_per_sector=max_core_per_sector,
        min_limit_up=min_limit_up, min_board_height=min_board_height,
    )
    refreshed = get_last_refreshed(db, target)
    result["refreshed_at"] = refreshed.isoformat() if refreshed else None
    result["source"] = SOURCE_NAME if refreshed else None
    return result


@router.post("/refresh", response_model=LimitUpRadarRefreshResponse)
def refresh_limit_up_details(
    trade_date: Optional[date] = Query(None, alias="date", description="默认今天"),
    db: Session = Depends(get_db),
):
    """
    只做一件事：拉当日涨停/炸板明细并 upsert。同步执行（3个轻量请求，通常1-2秒），
    不起后台线程——用户点了刷新就是要等这个结果，异步反而要再轮询一次状态。

    失败时不删除已有数据，返回 ok=False + 上次成功时间，页面继续显示上一份并明确
    标注刷新失败。stale 数据加上诚实的时间戳，好过一个空页面或一份伪装成最新的数据。
    """
    target = trade_date or date.today()
    try:
        lu, bb, _warnings = sync_limit_up_details(db, target)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        last = get_last_refreshed(db, target) or get_last_refreshed(db, _resolve_date(db, None))
        return LimitUpRadarRefreshResponse(
            ok=False, trade_date=target.isoformat(),
            error=f"{type(e).__name__}: {e}",
            last_success_at=last.isoformat() if last else None,
        )

    # 顺带刷新东财核心召回名单（第4个轻量请求）。它决定"哪些历史强势股不该从核心区
    # 消失"，失败只是退回上一份名单，不影响涨停明细这个主要目的，所以单独 try。
    scores_n = 0
    try:
        sync_core_recall(db, target)
        # 只给这个页面上的股票重算龙头分/风险分（东财给不了这两个）。范围严格限定，
        # 3/3门槛下实测41只，几秒钟。结果存页面作用域，不写 Stock——见
        # refresh_radar_scores 的注释。
        scores_n = refresh_radar_scores(db, target)
    except Exception:  # noqa: BLE001
        db.rollback()

    refreshed = get_last_refreshed(db, target)
    return LimitUpRadarRefreshResponse(
        ok=True, trade_date=target.isoformat(),
        limit_up_written=lu, broken_written=bb, scores_recomputed=scores_n,
        refreshed_at=(refreshed or datetime.now()).isoformat(),
    )
