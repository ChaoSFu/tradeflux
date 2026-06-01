"""
Admin endpoints — manual triggers for background data jobs + sector visibility management.
"""
import sys
import os
import threading
from datetime import date, datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from app.database import get_db
from app.models.sector import Sector
from app.auth import require_auth

router = APIRouter(prefix="/admin", tags=["admin"])

# ── In-memory job state ────────────────────────────────────────────────────────
_lock = threading.Lock()
_job: dict = {
    "status": "idle",       # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "message": "",
    "log_lines": [],        # last N lines of stdout captured
}

_boards_lock = threading.Lock()
_boards_job: dict = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "",
    "log_lines": [],
}

_MAX_LOG = 200  # Boards sync is long; keep more lines


def _capture_update(target_date: date, skip_boards: bool) -> None:
    """Run daily_update in a thread; capture print output into _job['log_lines']."""
    import io
    import contextlib

    log: list[str] = []

    def _flush(text: str) -> None:
        for line in text.splitlines():
            if line.strip():
                log.append(line)
        with _lock:
            _job["log_lines"] = log[-60:]

    buf = io.StringIO()
    try:
        # Ensure the backend package root is importable
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)

        with contextlib.redirect_stdout(buf):
            from scripts.daily_update import run_daily_update  # type: ignore
            run_daily_update(target_date, skip_boards=skip_boards)

        _flush(buf.getvalue())
        with _lock:
            _job["status"] = "done"
            _job["finished_at"] = datetime.now().isoformat(timespec="seconds")
            _job["message"] = f"更新完成 {target_date}"

    except Exception as exc:  # noqa: BLE001
        _flush(buf.getvalue())
        with _lock:
            _job["status"] = "error"
            _job["finished_at"] = datetime.now().isoformat(timespec="seconds")
            _job["message"] = str(exc)


def _capture_sync_boards() -> None:
    """Run sync_boards in a thread; capture print output into _boards_job."""
    import io
    import contextlib

    log: list[str] = []

    def _flush(text: str) -> None:
        for line in text.splitlines():
            if line.strip():
                log.append(line)
        with _boards_lock:
            _boards_job["log_lines"] = log[-_MAX_LOG:]

    def _write_log_file(text: str) -> None:
        """把捕获到的输出同步写入日志文件。"""
        import os
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        log_dir = os.path.join(backend_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"sync_boards_{date.today().isoformat()}.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(text)

    buf = io.StringIO()
    try:
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)

        with contextlib.redirect_stdout(buf):
            from scripts.sync_boards import run_sync_boards  # type: ignore
            run_sync_boards()

        output = buf.getvalue()
        _flush(output)
        _write_log_file(output)
        with _boards_lock:
            _boards_job["status"] = "done"
            _boards_job["finished_at"] = datetime.now().isoformat(timespec="seconds")
            _boards_job["message"] = "板块全量同步完成"

    except Exception as exc:  # noqa: BLE001
        output = buf.getvalue()
        _flush(output)
        _write_log_file(output)
        with _boards_lock:
            _boards_job["status"] = "error"
            _boards_job["finished_at"] = datetime.now().isoformat(timespec="seconds")
            _boards_job["message"] = str(exc)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/update")
def trigger_update(skip_boards: bool = True, _: str = Depends(require_auth)):
    """Start a daily data update in the background."""
    with _lock:
        if _job["status"] == "running":
            return {"ok": False, "message": "已有更新任务在运行中，请稍后"}

        # 互斥检查：板块同步正在运行时不允许启动日更
        # 两者都写 stock_sector_relations，并发会导致 DeadlockDetected
        with _boards_lock:
            if _boards_job["status"] == "running":
                return {
                    "ok": False,
                    "message": "板块同步正在运行中，请等待同步完成后再执行数据更新（避免数据库死锁）",
                }

        _job["status"] = "running"
        _job["started_at"] = datetime.now().isoformat(timespec="seconds")
        _job["finished_at"] = None
        _job["log_lines"] = []

        now = datetime.now()
        h, m = now.hour, now.minute
        in_trading = (9, 25) <= (h, m) <= (15, 0)
        _job["message"] = (
            "⚠️ 当前为交易时段，K 线数据为实时盘中价，收盘后需再次更新以获取最终价格"
            if in_trading else "启动中…"
        )

    t = threading.Thread(
        target=_capture_update,
        args=(date.today(), skip_boards),
        daemon=True,
    )
    t.start()
    return {"ok": True, "message": "数据更新已启动"}


@router.get("/update/status")
def get_update_status():
    """Return current job state."""
    with _lock:
        return dict(_job)


@router.post("/sync-boards")
def trigger_sync_boards(_: str = Depends(require_auth)):
    """启动东财板块全量同步（概念 + 行业全量 + 地区，约 5-8 分钟）。"""
    with _boards_lock:
        if _boards_job["status"] == "running":
            return {"ok": False, "message": "已有板块同步任务在运行中，请稍后"}

        # 互斥检查：日更正在运行时不允许启动板块同步
        with _lock:
            if _job["status"] == "running":
                return {
                    "ok": False,
                    "message": "数据更新正在运行中，请等待更新完成后再执行板块同步（避免数据库死锁）",
                }

        _boards_job["status"] = "running"
        _boards_job["started_at"] = datetime.now().isoformat(timespec="seconds")
        _boards_job["finished_at"] = None
        _boards_job["log_lines"] = []
        _boards_job["message"] = "正在同步东财板块：概念(e:3) + 行业全量(MK0881) + 地区(e:1)，预计 5-8 分钟..."

    t = threading.Thread(target=_capture_sync_boards, daemon=True)
    t.start()
    return {"ok": True, "message": "板块全量同步已启动，可通过 /admin/sync-boards/status 查看进度"}


@router.get("/sync-boards/status")
def get_sync_boards_status():
    """返回板块同步任务当前状态。"""
    with _boards_lock:
        return dict(_boards_job)


# ── Sector visibility management ──────────────────────────────────────────────

class SectorVisibilityItem(BaseModel):
    id: int
    code: str
    name: str
    sector_type: Optional[str]
    is_watched: bool
    stock_count: Optional[int]
    total_market_cap: Optional[float]  # 亿
    amount: Optional[float]            # 亿
    turnover_rate: Optional[float]     # %
    pct_change_30d: Optional[float]    # 今日涨幅 %
    pct_change_5d: Optional[float]     # 近5日涨幅 %
    pct_change_10d: Optional[float]    # 近10日涨幅 %

    model_config = {"from_attributes": True}


@router.get("/sectors", response_model=List[SectorVisibilityItem])
def list_sectors_for_config(db: Session = Depends(get_db)):
    """返回所有板块及其 is_watched 状态，供管理页面使用。"""
    sectors = (
        db.query(Sector)
        .filter(Sector.sector_type.in_(["concept", "industry", "region"]))
        .order_by(Sector.sector_type, Sector.stock_count.desc().nullslast())
        .all()
    )
    return sectors


@router.patch("/sectors/{sector_id}/watch")
def toggle_sector_watch(sector_id: int, db: Session = Depends(get_db)):
    """切换指定板块的 is_watched 状态。"""
    sector = db.query(Sector).filter(Sector.id == sector_id).first()
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")
    sector.is_watched = not sector.is_watched
    db.commit()
    return {"id": sector.id, "name": sector.name, "is_watched": sector.is_watched}


@router.post("/sectors/batch-watch")
def batch_set_watch(
    sector_ids: List[int],
    watched: bool,
    db: Session = Depends(get_db),
):
    """批量设置一批板块的 is_watched。"""
    updated = (
        db.query(Sector)
        .filter(Sector.id.in_(sector_ids))
        .all()
    )
    for s in updated:
        s.is_watched = watched
    db.commit()
    return {"updated": len(updated), "is_watched": watched}
