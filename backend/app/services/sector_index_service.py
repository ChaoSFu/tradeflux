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
import time
from datetime import date
from typing import Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from ..models.market_index import SectorIndexDaily
from ..services.eastmoney_fetcher import fetch_sector_kline

# 认为"历史够用"的最少根数。RS 最长窗口 60 个交易日，+1 根锚点，再留些余量
MIN_BARS_FOR_RS = 70


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
    delay: float = 1.5, stop_after_failures: int = 5, log=None,
) -> dict:
    """
    逐板块回填指数日线。**可重入**：已补够 MIN_BARS_FOR_RS 根的直接跳过。

    `delay` 是每次请求之间的间隔——push2his 限流很凶，宁可慢也不要把整批打死。
    连续失败 `stop_after_failures` 次就停手：那多半已经被限流，继续打只是加深封锁，
    而且会连累依赖同一域名的指数同步。**主动停下比硬撑更负责**。

    返回 {'filled': 补了几只, 'skipped': 跳过几只, 'failed': [...], 'bars': 写入行数,
          'aborted': 是否因连续失败提前退出}
    """
    def _say(msg):
        if log:
            log.info(msg)
        else:
            print(msg, flush=True)

    have = existing_bar_counts(db, sector_codes)
    todo = [c for c in sector_codes if have.get(c, 0) < MIN_BARS_FOR_RS]
    skipped = len(sector_codes) - len(todo)
    filled, bars_written, consecutive_fail = 0, 0, 0
    failed: List[str] = []
    aborted = False

    for i, code in enumerate(todo):
        if i:
            time.sleep(delay)
        rows = fetch_sector_kline(code, days=days)
        if not rows:
            failed.append(code)
            consecutive_fail += 1
            if consecutive_fail >= stop_after_failures:
                aborted = True
                _say(f"  连续 {consecutive_fail} 只失败，判定已被限流，主动停手"
                     f"（已补 {filled} 只，剩 {len(todo) - i - 1} 只下次重跑）")
                break
            continue
        consecutive_fail = 0

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
            "bars": bars_written, "aborted": aborted}


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
