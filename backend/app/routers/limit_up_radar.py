"""
涨停板块雷达接口（2026-08-25新增）。

刷新接口刻意做得非常窄：只同步涨停/炸板明细这一件事。它**不会**触发 daily_update、
不会拉65日K线、不会重算 Market State、不碰弱转强雷达、不做板块全量同步、不调AI。
目的是让用户在盘中能以极低代价把最新封板状态刷到页面上。

也不做自动轮询——TradeFlux 当前是 manual-first，只在用户主动点击时打外部接口，
避免不必要的限流/封禁风险。
"""
import threading
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..database import SessionLocal, get_db
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


# ── 手动刷新：后台线程 + 状态轮询 ────────────────────────────────────────────
# 2026-08-26 从同步执行改成异步。原因：加上"为本页股票重算龙头分/风险分"之后，整个
# 刷新实测 37.8 秒（东财3个接口 + 100 只K线），而前端 axios 全局超时是 15 秒——请求
# 被前端掐断，onSuccess 不触发、页面不刷新，但**后台其实已经把活干完了**，用户看到
# 的就是"点了没反应，过一会儿手动切换才出来"。
# 单纯调大超时不解决问题：让用户对着转圈等 40 秒本身就不对。改成跟弱转强雷达同一套
# 模式（后台线程 + /refresh/status 轮询），点完立即返回，页面轮询到完成再刷新数据。
# 这不违反"不做自动轮询"——那条约束针对的是"页面自己定时打外部接口"，这里是用户
# 主动点击后为了拿到这一次的结果而轮询本地状态，点完就停。
_lock = threading.Lock()
_job: dict = {
    "running": False, "ok": None, "error": None, "trade_date": None,
    "limit_up_written": 0, "broken_written": 0, "scores_recomputed": 0,
    "finished_at": None, "step": None,
}


def _set(**kw) -> None:
    with _lock:
        _job.update(kw)


def _run_refresh_job(target: date) -> None:
    db = SessionLocal()
    try:
        _set(step="拉取涨停/炸板明细")
        lu, bb, _w = sync_limit_up_details(db, target)
        _set(limit_up_written=lu, broken_written=bb)

        # 下面两步失败不影响涨停明细这个主要目的，各自 try
        scores_n = 0
        try:
            _set(step="东财条件选股（板块核心召回）")
            sync_core_recall(db, target)
            _set(step="重算本页股票龙头分/风险分")
            scores_n = refresh_radar_scores(db, target)
        except Exception:  # noqa: BLE001
            db.rollback()

        refreshed = get_last_refreshed(db, target)
        _set(running=False, ok=True, error=None, trade_date=target.isoformat(),
             scores_recomputed=scores_n, step=None,
             finished_at=(refreshed or datetime.now()).isoformat())
    except Exception as e:  # noqa: BLE001
        db.rollback()
        last = get_last_refreshed(db, target)
        _set(running=False, ok=False, step=None,
             error=f"{type(e).__name__}: {e}",
             trade_date=target.isoformat(),
             finished_at=last.isoformat() if last else None)
    finally:
        db.close()


@router.post("/refresh", response_model=LimitUpRadarRefreshResponse)
def refresh_limit_up_details(
    trade_date: Optional[date] = Query(None, alias="date", description="默认今天"),
):
    """
    启动刷新（后台执行，立即返回）。进度与结果查 GET /refresh/status。

    只做四件事：涨停池 + 炸板池 + 涨停原因 + 东财核心召回名单，外加为**本页面上的
    股票**重算龙头分/风险分。绝不触发 daily_update / 全市场选股 / Market State /
    弱转强雷达 / 板块全量同步 / AI点评。

    失败时不删除已有数据，状态里返回 ok=false + 上次成功时间，页面继续显示上一份
    并明确标注刷新失败。stale 数据加上诚实的时间戳，好过空页面或伪装成最新的数据。
    """
    target = trade_date or date.today()
    with _lock:
        if _job["running"]:
            return LimitUpRadarRefreshResponse(
                ok=False, trade_date=target.isoformat(),
                error="已有刷新任务在运行中，请稍候",
                last_success_at=_job.get("finished_at"),
            )
        _job.update({"running": True, "ok": None, "error": None, "step": "启动中",
                     "trade_date": target.isoformat()})
    threading.Thread(target=_run_refresh_job, args=(target,), daemon=True).start()
    return LimitUpRadarRefreshResponse(ok=True, running=True, trade_date=target.isoformat())


@router.get("/refresh/status", response_model=LimitUpRadarRefreshResponse)
def get_refresh_status():
    with _lock:
        j = dict(_job)
    return LimitUpRadarRefreshResponse(
        ok=bool(j["ok"]) if j["ok"] is not None else True,
        running=j["running"], step=j["step"],
        trade_date=j["trade_date"],
        limit_up_written=j["limit_up_written"], broken_written=j["broken_written"],
        scores_recomputed=j["scores_recomputed"],
        refreshed_at=j["finished_at"] if j["ok"] else None,
        error=j["error"], last_success_at=j["finished_at"],
    )
