"""
每个请求前后量一次 RSS，涨得多的把路径打进日志。

## 为什么是这个

2026-09-07 这台 1.87G 的服务器整站超时，uvicorn 常驻 900MB+。从外面采样猜了
四次，四次都错：

    ① 15:30 定时任务在跑        —— 日志显示那次因锁被占用跳过了
    ② jitter 让重启重新触发日更  —— next_run 指向明天，[SCHED] 全天只有两条
    ③ 三个 page_size=500 撞一起  —— 并发三轮只涨 14MB
    ④ npm run build 抢内存       —— 抓现场时根本没有构建在跑

每一次都是**对着代码推断生产行为**。外部采样只能看到"涨了"，看不到"谁涨的"，
而 10 秒一次的采样窗口里有几十个请求，怎么归因都是猜。

所以改成让进程自己说：请求处理前后各读一次 `/proc/self/statm`，增量超过阈值
就把路径、增量、当前 RSS 打进 journal。**跑一天就知道是谁。**

## 代价

一次 statm 读取是几微秒的事（内核直接给数，不遍历页表），比任何一次 DB 查询
都便宜几个数量级。所以常开，不做开关——需要开关才敢用的观测，出事时永远是关着的。

## 读不出来时

非 Linux、或 /proc 不可读 → `_rss_mb()` 返回 None，中间件直接放行不记录。
**不猜一个数**：一个假的 RSS 比没有 RSS 更糟。
"""
import logging
import os
import time
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("tradeflux.rss")

#: 单个请求涨这么多 MB 才记。默认 20MB——日常浏览整页才 +70MB，
#: 单个请求涨 20MB 已经值得看一眼了
GROWTH_LOG_MB = float(os.environ.get("RSS_PROBE_MB", "20"))
_PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _rss_mb() -> Optional[float]:
    """当前进程 RSS（MB）。读不出来返回 None，**不返回 0**。"""
    try:
        with open("/proc/self/statm", "rb") as f:
            return int(f.read().split()[1]) * _PAGE / 1048576
    except (OSError, IndexError, ValueError):
        return None


class RssProbeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        before = _rss_mb()
        t0 = time.monotonic()
        response = await call_next(request)
        after = _rss_mb()
        if before is not None and after is not None:
            grew = after - before
            if grew >= GROWTH_LOG_MB:
                # 带上 query string——page_size=500 和 page_size=1 是两件事
                q = request.url.query
                logger.warning(
                    "[RSS] +%.0fMB → %.0fMB  %.2fs  %s%s",
                    grew, after, time.monotonic() - t0, request.url.path,
                    f"?{q}" if q else "")
        return response


#: 心跳采样间隔（秒）和记录阈值（MB）
HEARTBEAT_SEC = float(os.environ.get("RSS_PROBE_HEARTBEAT_SEC", "60"))
HEARTBEAT_MB = float(os.environ.get("RSS_PROBE_HEARTBEAT_MB", "30"))


async def rss_heartbeat() -> None:
    """
    定期记 RSS，**只在跳变时记**。

    中间件只看得到请求。而这个进程里还有几条不走请求的重活：
    后台刷新线程（涨停雷达 POST /refresh 立即返回、活干在线程里，实测约 40 秒；
    弱转强候选刷新实测 249 秒）、调度器的板块同步。这些涨起来的内存，中间件
    一行都不会记。

    所以再加一条心跳：涨/落超过阈值就打一行。跟中间件的记录对照，就能分出
    「某个请求干的」和「没有请求也在涨」。
    """
    import asyncio
    last = _rss_mb()
    if last is None:
        logger.info("[RSS] 拿不到 /proc/self/statm，心跳不启动（非 Linux？）")
        return
    logger.warning("[RSS] 心跳启动，当前 %.0fMB", last)
    while True:
        try:
            await asyncio.sleep(HEARTBEAT_SEC)
            cur = _rss_mb()
            if cur is None:
                continue
            if abs(cur - last) >= HEARTBEAT_MB:
                logger.warning("[RSS] 心跳 %+.0fMB → %.0fMB", cur - last, cur)
                last = cur
        except asyncio.CancelledError:
            raise
        except Exception:               # noqa: BLE001
            # 观测挂了不能带垮服务
            logger.exception("[RSS] 心跳异常")
