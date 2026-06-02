"""
TradeFlux 内置调度器
- 与 FastAPI 应用同生同死（lifespan 管理）
- 服务重启自动 kill 旧调度器、启动新调度器
- 与 UI 手动触发共享文件锁，互斥执行
"""
import fcntl
import logging
import os
import sys
from datetime import date, datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

LOCK_FILE = "/tmp/tradeflux_daily_update.lock"


def _log(log_path: str, tag: str, msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{tag}] {msg}"
    with open(log_path, "a") as f:
        f.write(line + "\n")
    logger.info(line)


def _run_daily_update() -> None:
    """定时任务执行体：获取文件锁后运行每日数据更新。"""
    today = date.today()
    backend_dir = os.path.dirname(os.path.dirname(__file__))
    log_dir = os.path.join(backend_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"daily_update_{today.isoformat()}.log")

    _log(log_path, "SCHED", "=" * 40)
    _log(log_path, "SCHED", "定时任务触发，尝试获取锁...")

    lock_fd = None
    try:
        lock_fd = open(LOCK_FILE, "w")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _log(log_path, "SCHED", "❌ 锁已被占用（UI 手动触发正在运行），本次跳过")
            return

        _log(log_path, "SCHED", "✅ 获取锁成功，开始执行每日数据更新")

        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)

        from scripts.daily_update import run_daily_update  # type: ignore
        run_daily_update(today)

        _log(log_path, "SCHED", "✅ 每日数据更新完成")

    except Exception as exc:
        _log(log_path, "SCHED", f"❌ 每日数据更新失败: {exc}")
        logger.exception("定时更新异常")
    finally:
        if lock_fd:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass
        _log(log_path, "SCHED", "=" * 40)


def create_scheduler() -> BackgroundScheduler:
    """
    创建并配置后台调度器。
    - 周一至周五 15:30 触发，jitter=3600 在 1 小时内随机延迟执行
    - max_instances=1 确保同一时刻只有一个实例
    """
    scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(
        _run_daily_update,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=15,
            minute=30,
            timezone="Asia/Shanghai",
        ),
        jitter=3600,        # 随机延迟 0~3600 秒，落在 15:30~16:30 之间
        max_instances=1,    # 同一任务只允许一个实例运行
        id="daily_update",
        name="每日数据更新",
        replace_existing=True,
        misfire_grace_time=7200,  # 错过触发时间 2 小时内仍可补跑
    )
    return scheduler
