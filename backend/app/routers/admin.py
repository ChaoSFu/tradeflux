"""
Admin endpoints — manual triggers for background data jobs + sector visibility management.
"""
import sys
import os
import json
import fcntl
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

# ── 进程级互斥锁文件（与 cron 任务共享，防止并发执行）─────────────────────────
DAILY_UPDATE_LOCK_FILE = "/tmp/tradeflux_daily_update.lock"

# ── 最后一次更新结果持久化文件（服务重启后仍可读）──────────────────────────────
def _get_last_update_path() -> str:
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(backend_dir, "logs", "last_update_status.json")

def _save_last_update(
    source: str, status: str, started_at: str | None, finished_at: str | None, message: str,
    degraded: bool = False, warnings: list[str] | None = None,
) -> None:
    try:
        path = _get_last_update_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({
                "source": source,          # "manual" | "scheduled"
                "status": status,          # "done" | "error"
                "started_at": started_at,
                "finished_at": finished_at,
                "message": message,
                "degraded": degraded,      # True=有数据源API降级，数据可能不完整/过时
                "warnings": warnings or [],
            }, f, ensure_ascii=False)
    except Exception:
        pass

def _load_last_update() -> dict | None:
    try:
        path = _get_last_update_path()
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

# ── In-memory job state ────────────────────────────────────────────────────────
_lock = threading.Lock()
_job: dict = {
    "status": "idle",       # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "message": "",
    "log_lines": [],        # last N lines of stdout captured
    "degraded": False,      # True=有数据源API降级，数据可能不完整/过时
    "warnings": [],
}

_boards_lock = threading.Lock()
_boards_job: dict = {
    "status": "idle",
    "started_at": None,
    "finished_at": None,
    "message": "",
    "log_lines": [],
    "mode": None,   # "meta" | "full"，区分板块行情同步和板块全量同步
}

_MAX_LOG = 200  # Boards sync is long; keep more lines


def _write_log_file(target_date: date, message: str) -> None:
    """向当日 daily_update 日志文件追加一条记录。"""
    try:
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        log_dir = os.path.join(backend_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, f"daily_update_{target_date.isoformat()}.log")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a") as f:
            f.write(f"\n[{ts}] [UI] {message}\n")
    except Exception:
        pass


def _capture_update(target_date: date, skip_boards: bool, source: str = "manual") -> None:
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
    lock_fd = None
    try:
        # ── 文件锁：与 cron 任务互斥，防止并发执行 ──────────────────────────
        lock_fd = open(DAILY_UPDATE_LOCK_FILE, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            msg = "另一个更新任务正在运行（定时任务或手动触发），请稍后再试"
            _write_log_file(target_date, f"❌ 获取锁失败：{msg}")
            with _lock:
                _job["status"] = "error"
                _job["finished_at"] = datetime.now().isoformat(timespec="seconds")
                _job["message"] = msg
            return

        _write_log_file(target_date, "✅ UI 手动触发，获取锁成功，开始执行")

        # Ensure the backend package root is importable
        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)

        with contextlib.redirect_stdout(buf):
            from scripts.daily_update import run_daily_update  # type: ignore
            result = run_daily_update(target_date, skip_boards=skip_boards)

        degraded = bool(result and result.get("degraded"))
        warnings = list((result or {}).get("warnings") or [])

        _flush(buf.getvalue())
        finished = datetime.now().isoformat(timespec="seconds")
        _flush(buf.getvalue())
        _write_log_file(target_date, f"✅ {source} 触发完成"
                        + (f"（数据降级：{'；'.join(warnings)}）" if degraded else ""))
        message = f"更新完成 {target_date}" + ("（部分数据源降级，详见告警）" if degraded else "")
        _save_last_update(source, "done", _job.get("started_at"), finished, message,
                          degraded=degraded, warnings=warnings)
        with _lock:
            _job["status"] = "done"
            _job["finished_at"] = finished
            _job["message"] = message
            _job["degraded"] = degraded
            _job["warnings"] = warnings

    except Exception as exc:  # noqa: BLE001
        finished = datetime.now().isoformat(timespec="seconds")
        _flush(buf.getvalue())
        _write_log_file(target_date, f"❌ {source} 触发失败: {exc}")
        _save_last_update(source, "error", _job.get("started_at"), finished, str(exc))
        with _lock:
            _job["status"] = "error"
            _job["finished_at"] = finished
            _job["message"] = str(exc)
            _job["degraded"] = False
            _job["warnings"] = []
    finally:
        if lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()


def _capture_sync_boards(meta_only: bool = False) -> None:
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
            run_sync_boards(meta_only=meta_only)

        output = buf.getvalue()
        _flush(output)
        _write_log_file(output)
        with _boards_lock:
            _boards_job["status"] = "done"
            _boards_job["finished_at"] = datetime.now().isoformat(timespec="seconds")
            _boards_job["message"] = "板块元数据更新完成" if meta_only else "板块全量同步完成"

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
        _job["degraded"] = False
        _job["warnings"] = []

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
def trigger_sync_boards(meta_only: bool = False, _: str = Depends(require_auth)):
    """
    启动板块同步。
    meta_only=true：仅更新涨跌幅/换手/市值等频繁变化的元数据（约30s），供每日调用。
    meta_only=false（默认）：全量同步，含成份股数量 + 个股板块关联（约5-8分钟），建议每周一次。
    """
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
        _boards_job["mode"] = "meta" if meta_only else "full"
        _boards_job["message"] = (
            "正在更新板块元数据（涨跌幅/换手/市值），约30秒..." if meta_only
            else "正在全量同步板块：元数据 + 成份股数量 + 个股关联，预计 5-8 分钟..."
        )

    t = threading.Thread(target=_capture_sync_boards, args=(meta_only,), daemon=True)
    t.start()
    return {"ok": True, "message": "板块元数据更新已启动" if meta_only else "板块全量同步已启动"}


@router.get("/sync-boards/status")
def get_sync_boards_status():
    """返回板块同步任务当前状态。"""
    with _boards_lock:
        return dict(_boards_job)


@router.get("/update/last")
def get_last_update():
    """返回最后一次更新结果（持久化，服务重启后仍可读）。"""
    data = _load_last_update()
    if not data:
        return {"source": None, "status": None, "started_at": None, "finished_at": None, "message": None}
    return data


@router.get("/scheduler/status")
def get_scheduler_status():
    """返回内置调度器状态及下次执行时间。"""
    try:
        from apscheduler.schedulers.base import STATE_RUNNING
        # 通过 app state 获取 scheduler（lifespan 中绑定）
        from app.main import _scheduler  # type: ignore
        if _scheduler is None:
            return {"running": False, "next_run": None, "message": "调度器未启动"}
        # 盘后 + 盘前两个定时任务，取最早的下次执行时间
        jobs = [j for j in (_scheduler.get_job("daily_update"),
                            _scheduler.get_job("daily_update_preopen")) if j]
        runs = [(j.id, j.next_run_time) for j in jobs if j.next_run_time]
        runs.sort(key=lambda x: x[1])
        soonest = runs[0] if runs else (None, None)
        return {
            "running": _scheduler.state == STATE_RUNNING,
            "next_run": soonest[1].isoformat() if soonest[1] else None,
            "job_id": soonest[0],
            "jobs": [{"id": jid, "next_run": t.isoformat()} for jid, t in runs],
        }
    except Exception as exc:
        return {"running": False, "next_run": None, "message": str(exc)}


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
