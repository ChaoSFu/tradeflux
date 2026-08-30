"""
交易日历（2026-08-28新增）。

在此之前，全仓库的"上一交易日/近N个交易日"都是**从已有数据反推**的：
  · limit_up_radar_service._trading_days —— 取 stock_daily_snapshots 里出现过的
    distinct 日期
  · daily_update.prev_trading_date      —— 取全体候选K线里 target_date 之前的最大日期
这两个推法在数据完整时是对的，但它们的前提是"我们那天跑过且写进去了"。漏跑一天、
或者某天全市场拉取失败，那一天就会从"交易日"里凭空消失，`近10个交易日`实际只覆盖
9 天——而且不会报错。这笔债在 docs/LIMIT_UP_SECTOR_RADAR.md 里记了很久。

fuyao 有现成接口：`GET /api/a-share/calendar/trading-days`，**无入参**，返回
近一年的固定窗口 `[今日-1年, 今日]`。

## 调用频率（用户 2026-08-28 明确要求"尽量低"）

缓存进 AppConfig，只在**问的日期超出缓存覆盖范围**时才去拉：
  · 问历史某天是不是交易日 → 永远命中缓存，0 请求
  · 盘中问"上一交易日"     → 命中缓存（昨天拉的那份就含昨天），0 请求
  · 跨到新的一天问"今天"   → 缓存不含今天，拉 1 次
所以稳态是**一天最多 1 次**，多数日子 0 次。

## 它不能成为新的单点

拉不到就用旧缓存；连缓存都没有就返回 None，让调用方退回原来从快照反推的老办法。
交易日历是"让判断更准"的增强，不是"没有它就跑不了"的依赖——这跟 dump 的定位一样。
"""
import json
from datetime import date, datetime, timedelta
from typing import List, Optional

import httpx
from sqlalchemy.orm import Session

from ..models.app_config import AppConfig
from .fuyao_dump import FUYAO_BASE, _timeouts, get_api_key
from .eastmoney_fetcher import json_or_explain

CACHE_KEY = "trading_calendar:days"


def fetch_trading_days(api_key: str, timeout: int = 20) -> List[date]:
    """近一年交易日序列。无入参，返回按日期升序。失败抛异常，由调用方兜底。"""
    with httpx.Client(timeout=_timeouts(timeout)) as c:
        resp = c.get(f"{FUYAO_BASE}/api/a-share/calendar/trading-days",
                     headers={"X-api-key": api_key})
    body = json_or_explain(resp, "fuyao 交易日历 ")
    if body.get("code") != 0:
        raise RuntimeError(f"交易日历 code={body.get('code')} {body.get('message')}")
    out: List[date] = []
    for it in ((body.get("data") or {}).get("item") or []):
        raw = str(it.get("date") or "")
        if len(raw) == 8 and raw.isdigit():
            out.append(date(int(raw[:4]), int(raw[4:6]), int(raw[6:8])))
    return sorted(set(out))


def _read_cache(db: Session) -> List[date]:
    row = db.query(AppConfig).filter(AppConfig.key == CACHE_KEY).first()
    if not row or not row.value:
        return []
    try:
        return sorted({date.fromisoformat(x) for x in (json.loads(row.value) or {}).get("days", [])})
    except Exception:  # noqa: BLE001
        return []


def _write_cache(db: Session, days: List[date]) -> None:
    val = json.dumps({"fetched_at": datetime.now().isoformat(timespec="seconds"),
                      "days": [d.isoformat() for d in days]}, ensure_ascii=False)
    row = db.query(AppConfig).filter(AppConfig.key == CACHE_KEY).first()
    if row:
        row.value = val
    else:
        db.add(AppConfig(key=CACHE_KEY, value=val))
    db.commit()


def get_trading_days(db: Session, need_through: Optional[date] = None,
                     log=None) -> Optional[List[date]]:
    """
    返回近一年交易日（升序）。**只在缓存覆盖不到 need_through 时才去拉**。
    拿不到且无缓存时返回 None —— 调用方据此退回原来从快照反推的老办法。
    """
    cached = _read_cache(db)
    if cached and (need_through is None or cached[-1] >= need_through):
        return cached                       # 零请求

    key = get_api_key()
    if not key:
        return cached or None
    try:
        days = fetch_trading_days(key)
        if days:
            _write_cache(db, days)
            if log:
                log.info(f"交易日历已更新：{len(days)} 个交易日（{days[0]} ~ {days[-1]}）")
            return days
    except Exception as e:  # noqa: BLE001
        if log:
            log.warning(f"交易日历拉取失败（{type(e).__name__}: {str(e)[:80]}），"
                        + ("沿用旧缓存" if cached else "退回从快照反推"))
    return cached or None


def prev_trading_day(days: List[date], d: date) -> Optional[date]:
    """d 之前最近的一个交易日。days 需升序。"""
    prev = [x for x in days if x < d]
    return prev[-1] if prev else None


def is_trading_day(days: List[date], d: date) -> bool:
    return d in set(days)


def last_n_trading_days(days: List[date], upto: date, n: int) -> List[date]:
    """截至 upto（含）的最近 n 个交易日，升序。不足 n 个就有多少给多少。"""
    sel = [x for x in days if x <= upto]
    return sel[-n:] if n > 0 else []
