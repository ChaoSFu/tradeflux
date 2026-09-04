"""
板块指数日线同步 —— 相对强度 RS_sector 的基准。

## 两条路，刻意分开

    历史回填   push2his 逐板块拉 K 线   一次性、可断点续跑、低并发
    每日增量   复用 sync_boards 的 clist   零新增请求

分开是因为 **push2his 限流很凶**：2026-09-03 实测，约 15 次快速请求就把开发机 IP
打进限流，而且连既有代码依赖的上证指数（1.000001）也一起拉不到——不是板块特有的
问题，是整个域名。仓库文档里那句"push2/push2his 这一系域名被持续限流"是真的。

所以日常绝不能依赖它。它只做一件事：把历史补上，补完就不再需要。

## 回填必须可重入

限流会在任意一只板块中途掐断。所以每只板块独立提交、独立判断"够不够"，
重跑时已经补够的直接跳过——不是从头再来。这跟 daily_update 的幂等要求一致。

## 失败要诚实

某个板块拿不到指数历史 → 它下面股票的 RS_sector 就是 None（不知道），
如实标注，**不用 0 顶替、也不用别的板块顶替**。本仓库为"用0表达不知道"栽过太多次。
"""
from datetime import date
from typing import Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..models.market_index import SectorIndexDaily
from ..services.eastmoney_fetcher import fetch_sector_kline_detailed
from ..services.rate_limiter import (
    AdaptiveRateLimiter, Outcome, cooldown_remaining, set_cooldown,
)

# 认为"历史够用"的最少根数。RS 最长窗口 60 个交易日，+1 根锚点，再留些余量
MIN_BARS_FOR_RS = 70

# 限速的域名。冷却按域名记——板块回填把 push2his 打死，指数同步一样拉不到
THROTTLE_DOMAIN = "push2his.eastmoney.com"
# 判定被限流后的冷却时长。宁可长——重来一趟只是多等，打死 IP 是连累整条链路
BLOCKED_COOLDOWN_SECONDS = 30 * 60


def existing_bar_counts(db: Session, codes: Sequence[str]) -> Dict[str, int]:
    """各板块已入库的日线根数，用于跳过已补够的（可重入的关键）。"""
    from sqlalchemy import func as sqlfunc
    if not codes:
        return {}
    rows = (
        db.query(SectorIndexDaily.sector_code, sqlfunc.count(SectorIndexDaily.id))
        .filter(SectorIndexDaily.sector_code.in_(list(codes)))
        .group_by(SectorIndexDaily.sector_code).all()
    )
    return {c: n for c, n in rows}


def backfill_sector_index(
    db: Session, sector_codes: Sequence[str], days: int = 300,
    delay: float = 2.0, stop_after_failures: int = 4, max_requests: int = 80,
    log=None, ignore_cooldown: bool = False,
) -> dict:
    """
    逐板块回填指数日线。**可重入**：已补够 MIN_BARS_FOR_RS 根的直接跳过。

    ## 限速三件事

    1. **分类**：`fetch_sector_kline_detailed` 区分 blocked / no_data / error。
       "这个板块没有指数日线"不该触发退避——退避解决不了数据不存在。
    2. **退避**：`AdaptiveRateLimiter` 指数退避 + 抖动 + 缓慢恢复。抖动不是装饰，
       每 2.0 秒整打一次本身就是机器指纹。
    3. **冷却**：连续 blocked 到阈值 → 主动停手，并把"X 点前不许再打 push2his"
       写进库。**这一条最要紧**——真正打死 IP 的是"跑挂了→立刻重跑"的循环，
       那个循环跨进程，只靠单次运行内的退避拦不住。

    `max_requests` 是单次运行的请求硬上限，防止无人值守时打出几百次。

    返回 {'filled','skipped','failed','no_data','bars','aborted','requests',
          'slept','cooldown_until'}
    """
    def _say(msg):
        if log:
            log.info(msg)
        else:
            print(msg, flush=True)

    left = None if ignore_cooldown else cooldown_remaining(db, THROTTLE_DOMAIN)
    if left is not None:
        mins = left.total_seconds() / 60
        _say(f"  {THROTTLE_DOMAIN} 仍在冷却中，还需 {mins:.0f} 分钟——本次不发任何请求。"
             f"（上次被限流后设的；确认要打请用 ignore_cooldown=True）")
        return {"filled": 0, "skipped": 0, "failed": [], "no_data": [], "bars": 0,
                "aborted": True, "requests": 0, "slept": 0.0, "cooldown_until": None,
                "cooling_down": True}

    have = existing_bar_counts(db, sector_codes)
    todo = [c for c in sector_codes if have.get(c, 0) < MIN_BARS_FOR_RS]
    skipped = len(sector_codes) - len(todo)
    filled, bars_written, consecutive_block = 0, 0, 0
    failed: List[str] = []
    no_data: List[str] = []
    aborted = False
    cooldown_until = None
    limiter = AdaptiveRateLimiter(base_delay=delay, max_delay=90.0, jitter=0.35,
                                  pause_every=20, pause_seconds=30.0)

    for i, code in enumerate(todo):
        if limiter.requests >= max_requests:
            aborted = True
            _say(f"  达到单次运行请求上限 {max_requests}，主动停手"
                 f"（已补 {filled} 只，剩 {len(todo) - i} 只下次重跑）")
            break
        limiter.before_request()
        rows, kind, detail, retry_after = fetch_sector_kline_detailed(code, days=days)
        limiter.on_outcome(Outcome(kind=kind, detail=detail, retry_after=retry_after))

        if kind == "no_data":
            # 数据本身没有，不是我们被拦。记下来但**不退避、不计入连续失败**
            no_data.append(code)
            continue
        if kind != "ok":
            failed.append(code)
            consecutive_block += 1
            _say(f"  {code} {kind}：{detail}（间隔已退避到 {limiter.delay:.1f}s）")
            if consecutive_block >= stop_after_failures:
                aborted = True
                cooldown_until = set_cooldown(db, THROTTLE_DOMAIN,
                                              BLOCKED_COOLDOWN_SECONDS)
                _say(f"  连续 {consecutive_block} 只被拦，判定已被限流，主动停手。"
                     f"已补 {filled} 只，剩 {len(todo) - i - 1} 只。"
                     f"**{BLOCKED_COOLDOWN_SECONDS // 60} 分钟内不要再跑**"
                     f"（已写入冷却，重跑会被自动挡下）")
                break
            continue
        consecutive_block = 0

        exist_dates = {
            d for (d,) in db.query(SectorIndexDaily.date)
            .filter(SectorIndexDaily.sector_code == code).all()
        }
        n = 0
        for r in rows:
            try:
                d = date.fromisoformat(r["date"])
            except (KeyError, ValueError):
                continue
            if d in exist_dates:
                continue
            db.add(SectorIndexDaily(
                sector_code=code, date=d,
                close=r.get("close"), pct_change=r.get("pct_change"),
                open=r.get("open"), high=r.get("high"), low=r.get("low"),
                volume=r.get("volume"), amount=r.get("amount"),
            ))
            n += 1
        db.commit()          # 每只板块独立提交——限流中途掐断也不丢已补的
        filled += 1
        bars_written += n

    return {"filled": filled, "skipped": skipped, "failed": failed,
            "no_data": no_data, "bars": bars_written, "aborted": aborted,
            "requests": limiter.requests, "slept": round(limiter.slept, 1),
            "cooldown_until": cooldown_until, "cooling_down": False}


def load_sector_closes(db: Session, codes: Sequence[str],
                       as_of: Optional[date] = None) -> Dict[str, Dict[date, float]]:
    """{板块码: {日期: 收盘点位}}，供 RS_sector 计算。"""
    if not codes:
        return {}
    q = db.query(SectorIndexDaily.sector_code, SectorIndexDaily.date,
                 SectorIndexDaily.close).filter(
        SectorIndexDaily.sector_code.in_(list(codes)))
    if as_of:
        q = q.filter(SectorIndexDaily.date <= as_of)
    out: Dict[str, Dict[date, float]] = {}
    for code, d, close in q.all():
        if close and close > 0:
            out.setdefault(code, {})[d] = close
    return out
