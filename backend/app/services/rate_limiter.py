"""
自适应限速器 —— 给「限流很凶且看不到配额」的外部源用。

## 为什么固定 sleep 不够

板块指数回填第一版是 `time.sleep(1.5)` + 连续失败 5 次就停手。两个问题：

1. **节拍太规整本身就是指纹。** 每 1.5 秒整一次请求，没有任何真人会这样。
2. **停手之后只能靠人重跑**，而人往往立刻就重跑——那正是加深封锁的做法。
   push2his 实测约十几次快速请求就把 IP 打进限流，且会连累依赖同一域名的指数同步。

## 三件事分开

    分类   这次失败到底是"被限流"还是"这个板块本来就没数据"——见 Outcome
    退避   看出被限流之后怎么放慢：指数退避 + 抖动 + 缓慢恢复
    冷却   被限流到主动停手后，**跨进程**记住"在 X 点之前不许再打"

第三件是关键。前两件只约束单次运行，而真正打死 IP 的是"跑挂了→立刻重跑"的循环。
冷却写进 app_config，任何进程重跑都会看到。

**已知缺口**：目前只有板块回填会查冷却。daily_update 里 fetch_index_kline 的
push2his 兜底不查——那条路要拿 db session 才能查，会把连接层耦合进 fetcher。
影响有限（它是兜底、频次低），但要知道：冷却期内那条路仍会打 push2his。

## 恢复要慢

一次成功不代表限流解除，可能只是恰好放行了一个。所以成功时只把间隔乘 0.8 慢慢
往回收，不直接跳回基准值。宁可整批慢一点，也不要刚缓过来就再次打死。
"""
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models.app_config import AppConfig

# 冷却记录在 app_config 里的键前缀。按**域名**存，不按业务存——限流是域名级的，
# 板块回填把 push2his 打死，指数同步一样拉不到
COOLDOWN_KEY_PREFIX = "ratelimit_cooldown:"


@dataclass
class Outcome:
    """
    一次请求的结果分类。**"没拿到"必须能分成两类**，否则限速做不对：

      no_data  HTTP 200 + 合法 JSON + 确实没有这段序列 → 数据本身如此，不该退避
      blocked  被拦/空响应/连接被掐 → 要退避

    分不清这两者，只有两种结局：该退避时硬打（加深封锁），不该退避时空等。
    这是"用一个空值表达两件事"在限速场景下的具体代价。
    """
    kind: str                        # "ok" | "no_data" | "blocked" | "error"
    detail: str = ""
    retry_after: Optional[float] = None    # 服务端明确要求等多久（秒）

    @property
    def should_backoff(self) -> bool:
        return self.kind in ("blocked", "error")


class AdaptiveRateLimiter:
    """
    指数退避 + 抖动 + 缓慢恢复。**不是线程安全的**，按设计只在单线程回填里用
    ——被限流的源上并发本身就是错的。
    """

    def __init__(self, base_delay: float = 1.5, max_delay: float = 120.0,
                 jitter: float = 0.3, pause_every: int = 0, pause_seconds: float = 0.0):
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        self.pause_every = pause_every        # 每打 N 次长歇一次（应对按窗口计的配额）
        self.pause_seconds = pause_seconds
        self.delay = base_delay
        self.requests = 0
        self.blocked_count = 0
        self.slept = 0.0

    def _sleep(self, seconds: float):
        if seconds > 0:
            self.slept += seconds
            time.sleep(seconds)

    def before_request(self):
        """请求之前调用。第一次不等，之后按当前间隔（带抖动）等。"""
        if self.requests:
            j = 1.0 + random.uniform(-self.jitter, self.jitter)
            self._sleep(self.delay * j)
            if self.pause_every and self.requests % self.pause_every == 0:
                self._sleep(self.pause_seconds)
        self.requests += 1

    def on_outcome(self, outcome: Outcome):
        if outcome.should_backoff:
            self.blocked_count += 1
            self.delay = min(self.max_delay, self.delay * 2)
            # 服务端明说了等多久就听它的，别自作聪明用更短的
            if outcome.retry_after:
                self.delay = max(self.delay, float(outcome.retry_after))
        else:
            # 恢复要慢：一次成功不代表限流解除
            self.delay = max(self.base_delay, self.delay * 0.8)


# ── 跨进程冷却 ──────────────────────────────────────────────────────────────

def cooldown_remaining(db: Session, domain: str) -> Optional[timedelta]:
    """还要冷却多久；None = 可以打。"""
    row = (db.query(AppConfig)
           .filter(AppConfig.key == COOLDOWN_KEY_PREFIX + domain).first())
    if not row or not row.value:
        return None
    try:
        until = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    left = until - datetime.now(timezone.utc)
    return left if left.total_seconds() > 0 else None


def set_cooldown(db: Session, domain: str, seconds: float) -> datetime:
    """
    记下"在这个时刻之前不许再打这个域名"。**跨进程**——真正打死 IP 的不是单次
    运行打得太快，是"跑挂了→立刻重跑"的循环，而那个循环跨进程，只能记在库里。
    """
    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    key = COOLDOWN_KEY_PREFIX + domain
    row = db.query(AppConfig).filter(AppConfig.key == key).first()
    if row:
        row.value = until.isoformat()
    else:
        db.add(AppConfig(key=key, value=until.isoformat()))
    db.commit()
    return until


def clear_cooldown(db: Session, domain: str):
    row = (db.query(AppConfig)
           .filter(AppConfig.key == COOLDOWN_KEY_PREFIX + domain).first())
    if row:
        row.value = None
        db.commit()
