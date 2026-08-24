"""
TradeFlux 内置调度器
- 与 FastAPI 应用同生同死（lifespan 管理）
- 服务重启自动 kill 旧调度器、启动新调度器
- 与 UI 手动触发共享文件锁，互斥执行
- 失败后 10 分钟内重试，最多 3 次
"""
import fcntl
import logging
import os
import sys
import time
from datetime import date, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger(__name__)

LOCK_FILE = "/tmp/tradeflux_daily_update.lock"
MAX_RETRIES = 3
RETRY_INTERVAL = 600  # 10 分钟


def _log(log_path: str, tag: str, msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{tag}] {msg}"
    with open(log_path, "a") as f:
        f.write(line + "\n")
    logger.info(line)


def _do_update(log_path: str, today: date) -> dict:
    """执行一次完整的更新流程（数据更新 + 板块行情同步）。返回 daily_update 的汇总 dict。"""
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from scripts.daily_update import run_daily_update  # type: ignore
    from scripts.sync_boards import run_sync_boards    # type: ignore
    from app.routers.admin import record_job_duration  # type: ignore

    t0 = datetime.now().isoformat(timespec="seconds")
    result = run_daily_update(today) or {}
    t1 = datetime.now().isoformat(timespec="seconds")
    record_job_duration("daily_update", t0, t1)
    _log(log_path, "SCHED", "✅ 每日数据更新完成，开始板块行情同步...")

    run_sync_boards(meta_only=True)
    t2 = datetime.now().isoformat(timespec="seconds")
    record_job_duration("board_meta", t1, t2)
    _log(log_path, "SCHED", "✅ 板块行情同步完成")
    return result


def _run_with_retry(attempt: int = 1) -> None:
    """
    定时任务执行体，支持失败重试。
    attempt：当前是第几次尝试（1=首次，2/3=重试）
    """
    today = date.today()
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    log_dir = os.path.join(backend_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"daily_update_{today.isoformat()}.log")

    tag = "SCHED" if attempt == 1 else f"RETRY{attempt-1}"
    _log(log_path, tag, "=" * 40)
    if attempt == 1:
        _log(log_path, tag, "定时任务触发，尝试获取锁...")
    else:
        _log(log_path, tag, f"第 {attempt-1} 次重试，尝试获取锁...")

    lock_fd = None
    try:
        lock_fd = open(LOCK_FILE, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _log(log_path, tag, "❌ 锁已被占用（UI 手动触发正在运行），本次跳过")
            return

        _log(log_path, tag, f"✅ 获取锁成功，开始执行（第 {attempt}/{MAX_RETRIES} 次）")
        started = datetime.now().isoformat(timespec="seconds")

        result = _do_update(log_path, today)
        degraded = bool(result.get("degraded"))
        warnings = list(result.get("warnings") or [])

        finished = datetime.now().isoformat(timespec="seconds")
        _log(log_path, tag, f"✅ 全部完成（共尝试 {attempt} 次）"
             + (f"，数据降级：{'；'.join(warnings)}" if degraded else ""))

        try:
            from app.routers.admin import _save_last_update  # type: ignore
            _save_last_update("scheduled", "done", started, finished,
                              f"定时更新完成 {today}（第{attempt}次）"
                              + ("（部分数据源降级）" if degraded else ""),
                              degraded=degraded, warnings=warnings)
        except Exception:
            pass

    except Exception as exc:
        finished = datetime.now().isoformat(timespec="seconds")
        _log(log_path, tag, f"❌ 失败: {exc}")
        logger.exception(f"定时更新异常（第{attempt}次）")

        if attempt < MAX_RETRIES:
            _log(log_path, tag,
                 f"⏳ {RETRY_INTERVAL // 60} 分钟后进行第 {attempt} 次重试（最多 {MAX_RETRIES} 次）...")
            # 调度一次性重试任务
            from datetime import timedelta
            retry_time = datetime.now() + timedelta(seconds=RETRY_INTERVAL)
            try:
                # 通过全局 scheduler 注册单次重试任务
                from app.main import _scheduler  # type: ignore
                if _scheduler and _scheduler.running:
                    _scheduler.add_job(
                        _run_with_retry,
                        trigger=DateTrigger(run_date=retry_time),
                        args=[attempt + 1],
                        id=f"daily_update_retry_{attempt}",
                        name=f"每日更新重试#{attempt}",
                        replace_existing=True,
                    )
                    _log(log_path, tag,
                         f"  重试任务已注册，将于 {retry_time.strftime('%H:%M:%S')} 执行")
                else:
                    _log(log_path, tag, "  ⚠️ 调度器不可用，无法注册重试任务")
            except Exception as e:
                _log(log_path, tag, f"  ⚠️ 注册重试任务失败: {e}")
        else:
            _log(log_path, tag, f"❌ 已达最大重试次数（{MAX_RETRIES}），今日更新放弃")
            try:
                from app.routers.admin import _save_last_update  # type: ignore
                _save_last_update("scheduled", "error", None, finished,
                                  f"定时更新失败（已重试{MAX_RETRIES}次）: {exc}")
            except Exception:
                pass
    finally:
        if lock_fd:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass
        _log(log_path, tag, "=" * 40)


def _run_daily_update() -> None:
    """调度器入口：首次触发（attempt=1）。"""
    _run_with_retry(attempt=1)


def _run_weekly_full_board_sync() -> None:
    """
    板块全量同步（周度兜底任务）：核对全部跟踪股票的板块归属（个股→板块
    F10），捕获"每日数据更新/板块行情同步"这两个快路径没覆盖到的变化——
    daily_update 里的板块关联同步只处理当日涨跌停股票，board 元数据同步
    （meta_only=True）跳过个股关联，都不会发现"某只股票板块归属变了"这种
    情况。全量同步成本较高（要过一遍全部跟踪股票），不适合每天跑，放在
    周六（休市、不抢每日更新的锁）跑一次，专门保证数据完整性。
    跟每日更新共享同一把锁，避免并发写库冲突；失败不做多次重试——反正
    下周还会再跑，是兜底任务不是关键路径，没必要像 daily_update 那样重试。
    """
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    log_dir = os.path.join(backend_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"sync_boards_full_{date.today().isoformat()}.log")

    lock_fd = None
    try:
        lock_fd = open(LOCK_FILE, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _log(log_path, "SCHED", "❌ 锁已被占用（其他更新任务正在运行），本次跳过，下周再试")
            return

        _log(log_path, "SCHED", "板块全量同步（周度兜底）开始...")
        from scripts.sync_boards import run_sync_boards  # type: ignore
        from app.routers.admin import record_job_duration  # type: ignore
        t0 = datetime.now().isoformat(timespec="seconds")
        run_sync_boards(meta_only=False)
        t1 = datetime.now().isoformat(timespec="seconds")
        record_job_duration("board_full", t0, t1)
        _log(log_path, "SCHED", "✅ 板块全量同步（周度兜底）完成")
    except Exception as exc:
        _log(log_path, "SCHED", f"❌ 板块全量同步失败: {exc}")
        logger.exception("板块全量同步（周度兜底）异常")
    finally:
        if lock_fd:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass


def create_scheduler() -> BackgroundScheduler:
    """
    创建并配置后台调度器。三个定时任务：
    - 盘后 15:30 触发每日数据更新，jitter=3600（±1h 内随机）
    - 盘前 09:27 触发每日数据更新，jitter=60（9:26:00~9:28:00，集合竞价后、开盘前随机）
    - 周六 10:00 触发板块全量同步（周度兜底，见 BACKEND.md §0.3）
    三者共享同一把文件锁互斥；max_instances=1；每日更新失败后 10 分钟自动重试
    最多 3 次，周度兜底失败不重试（下周还会再跑，非关键路径）。
    """
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    # 盘后更新（收盘后最终数据）
    scheduler.add_job(
        _run_daily_update,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=15,
            minute=30,
            timezone="Asia/Shanghai",
        ),
        jitter=3600,        # ±3600 秒随机
        max_instances=1,    # 同一任务只允许一个实例运行
        id="daily_update",
        name="每日数据更新",
        replace_existing=True,
        misfire_grace_time=7200,  # 错过触发时间 2 小时内仍可补跑
    )
    # 盘前更新（集合竞价 9:25 之后、开盘 9:30 之前）
    scheduler.add_job(
        _run_daily_update,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=9,
            minute=27,
            timezone="Asia/Shanghai",
        ),
        jitter=60,          # ±60 秒 → 9:26:00~9:28:00
        max_instances=1,
        id="daily_update_preopen",
        name="盘前数据更新",
        replace_existing=True,
        misfire_grace_time=1800,  # 错过 30 分钟内补跑（避免太晚跑进盘中）
    )
    # 板块全量同步（周度兜底）：休市日跑，不抢工作日每日更新的锁
    scheduler.add_job(
        _run_weekly_full_board_sync,
        trigger=CronTrigger(
            day_of_week="sat",
            hour=10,
            minute=0,
            timezone="Asia/Shanghai",
        ),
        max_instances=1,
        id="weekly_full_board_sync",
        name="板块全量同步（周度兜底）",
        replace_existing=True,
        misfire_grace_time=3600 * 6,  # 错过 6 小时内仍可补跑（非关键路径，宽松些）
    )
    return scheduler
