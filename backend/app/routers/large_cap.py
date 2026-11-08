"""大市值概览接口。读接口只读库；手动同步要登录，跑在子进程里，跟定时任务共用一把锁。"""
import threading
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..database import get_db
from ..models.large_cap import LargeCapSyncBatch
from ..services.large_cap_service import (
    NoSuchDate, _batch_dict, build_overview, get_config, set_config,
)

router = APIRouter(prefix="/large-cap", tags=["large-cap"])


@router.get("/overview")
def large_cap_overview(
    date_: Optional[date] = Query(None, alias="date", description="交易日，默认最新一份完整收盘快照"),
    threshold: Optional[float] = Query(None, gt=0, description="市值门槛（亿元），不传用配置值"),
    db: Session = Depends(get_db),
):
    """
    status=no_data：一次完整快照都还没有（不是「今天没有大市值公司」）。
    请求的日期没有完整快照 → 404，不拿别的日期顶替。
    """
    try:
        return build_overview(db, trade_date=date_, threshold=threshold)
    except NoSuchDate as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


_SORTABLE = {"total_cap", "pct", "pct_5d", "pct_10d", "pct_20d", "pct_60d", "amount",
             "turnover_rate", "volume_ratio", "amount_multiple", "float_cap"}


@router.get("/stocks")
def large_cap_stocks(
    date_: Optional[date] = Query(None, alias="date"),
    min_market_cap: Optional[float] = Query(None, gt=0, description="市值门槛（亿元）"),
    group_type: Optional[str] = Query(None, pattern="^(industry_l1|industry_l2|concept)$"),
    group: Optional[str] = Query(None, description="行业名或概念 BK 码"),
    sort_by: str = Query("total_cap"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    if sort_by not in _SORTABLE:
        raise HTTPException(status_code=400, detail=f"sort_by 只能是 {sorted(_SORTABLE)}")
    try:
        ov = build_overview(db, trade_date=date_, threshold=min_market_cap)
    except NoSuchDate as e:
        raise HTTPException(status_code=404, detail=str(e))
    if ov["status"] != "ok":
        return {"status": ov["status"], "date": None, "total": 0, "items": []}
    items = ov["stocks"]
    if group_type and group:
        if group_type == "concept":
            items = [s for s in items if group in s["concepts"]]
        else:
            items = [s for s in items if s[group_type] == group]
    # 缺值的排在最后，不当 0 参与排序
    present = [s for s in items if s.get(sort_by) is not None]
    absent = [s for s in items if s.get(sort_by) is None]
    present.sort(key=lambda s: s[sort_by], reverse=sort_order == "desc")
    items = present + absent
    start = (page - 1) * page_size
    return {"status": "ok", "date": ov["date"], "threshold_yi": ov["threshold_yi"],
            "total": len(items), "page": page, "page_size": page_size,
            "items": items[start:start + page_size]}


class LargeCapConfigUpdate(BaseModel):
    threshold_yi: Optional[float] = None
    surge_amount_multiple: Optional[float] = None
    surge_volume_ratio: Optional[float] = None
    history_days: Optional[int] = None
    include_st: Optional[bool] = None
    reset: bool = False


@router.get("/config")
def large_cap_config(db: Session = Depends(get_db)):
    return get_config(db)


@router.put("/config")
def update_large_cap_config(payload: LargeCapConfigUpdate, db: Session = Depends(get_db),
                            _: str = Depends(require_auth)):
    """只改展示口径，不会重新采集。历史快照存的是全市场，换门槛直接按新门槛重算。"""
    patch = payload.model_dump(exclude_unset=True, exclude={"reset"})
    try:
        return set_config(db, patch, reset=payload.reset)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── 手动同步 ────────────────────────────────────────────────────────────────

_job: dict = {"status": "idle", "started_at": None, "finished_at": None,
              "exit_code": None, "log_lines": []}
_job_lock = threading.Lock()
_MAX_LOG = 200


def _run_job() -> None:
    from ..services.daily_update_runner import run_script_subprocess
    lines: list = []

    def on_line(ln: str) -> None:
        lines.append(ln)
        _job["log_lines"] = lines[-_MAX_LOG:]

    try:
        code = run_script_subprocess(["-m", "scripts.large_cap_sync", "--apply"], on_line=on_line)
    except Exception as e:  # noqa: BLE001
        code = -1
        on_line(f"启动失败：{e}")
    _job.update(status="done" if code == 0 else "error", exit_code=code,
                finished_at=datetime.now().isoformat(timespec="seconds"))


@router.post("/sync")
def trigger_large_cap_sync(_: str = Depends(require_auth)):
    """抓一次全市场收盘快照（约 2 分钟）。盘中点了也只记批次、不写快照。"""
    with _job_lock:
        if _job["status"] == "running":
            raise HTTPException(status_code=409, detail="大市值同步已经在跑")
        _job.update(status="running", started_at=datetime.now().isoformat(timespec="seconds"),
                    finished_at=None, exit_code=None, log_lines=[])
    threading.Thread(target=_run_job, daemon=True, name="large-cap-sync").start()
    return {"ok": True}


@router.get("/sync/status")
def large_cap_sync_status(db: Session = Depends(get_db)):
    recent = db.query(LargeCapSyncBatch).order_by(LargeCapSyncBatch.id.desc()).limit(8).all()
    return {**_job, "recent_batches": [_batch_dict(b) for b in recent]}
