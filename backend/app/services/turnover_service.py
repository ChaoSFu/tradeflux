"""
成交额概览引擎。

每日拉取「成交额排序前N」选股结果（prompt 可在 PoolConfig 页配置），落库
turnover_pool_daily；读取时按板块聚合赚钱效应、跟前一交易日名单对比标出
「新进」股票。板块归属沿用 daily_update._update_primary_sectors 的既有
约定：只认 is_watched=True 的板块，命中多个时取 F10 返回顺序里第一个。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date, datetime
from typing import List, Optional

from sqlalchemy.orm import Session

from ..models.turnover_pool import TurnoverPoolDaily
from ..models.sector import Sector
from .eastmoney_fetcher import fetch_turnover_top_stocks, fetch_stock_bk_codes
from .pool_config_service import get_pool_keywords

_SYNC_DEBOUNCE = 600  # 库空自愈同步防抖（秒）
_last_sync_attempt: dict = {"ts": None}

# 赚钱效应方向判定阈值
_BIAS_PCT_THRESHOLD = 1.5
_BIAS_RATIO_THRESHOLD = 0.6


def _resolve_sector_names(db: Session, codes: List[str]) -> dict[str, Optional[str]]:
    """并发查每只股票所属的 is_watched 板块名，命中多个取第一个。"""
    watched = {s.code: s.name for s in db.query(Sector).filter(Sector.is_watched == True).all()}  # noqa: E712

    def fetch_one(code: str):
        return code, fetch_stock_bk_codes(code)

    result: dict[str, Optional[str]] = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_one, c): c for c in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                _, bk_codes = future.result()
            except Exception:  # noqa: BLE001
                result[code] = None
                continue
            result[code] = next((watched[bk] for bk in bk_codes if bk in watched), None)
    return result


def sync_turnover_pool(db: Session) -> dict:
    """
    拉取当日成交额前列股票并 upsert 入库（同一天重跑会先清空当天旧行，幂等）。
    返回 {'ok': bool, 'count': n, 'errors': [...]}
    """
    _last_sync_attempt["ts"] = datetime.now()
    keyword = get_pool_keywords(db)["turnover_pool_keyword"]
    stocks = fetch_turnover_top_stocks(keyword)
    if not stocks:
        return {"ok": False, "count": 0, "errors": ["成交额选股 API 不可用（本次未拿到完整分页）"]}

    codes = [s["code"] for s in stocks]
    # 板块归属短期内基本不变：复用之前任意一天已解析过的结果，只对真正没
    # 见过的新代码走 F10（这是本函数最慢的一步，60只里通常大半是老面孔）
    prior_rows = (
        db.query(TurnoverPoolDaily.stock_code, TurnoverPoolDaily.sector_name)
        .filter(TurnoverPoolDaily.stock_code.in_(codes))
        .order_by(TurnoverPoolDaily.date.asc())
        .all()
    )
    known_sectors: dict[str, Optional[str]] = {code: sector for code, sector in prior_rows}
    codes_to_resolve = [c for c in codes if c not in known_sectors]
    newly_resolved = _resolve_sector_names(db, codes_to_resolve) if codes_to_resolve else {}
    sector_by_code = {**known_sectors, **newly_resolved}

    today = _date.today()
    db.query(TurnoverPoolDaily).filter(TurnoverPoolDaily.date == today).delete()
    for i, s in enumerate(stocks, start=1):
        db.add(TurnoverPoolDaily(
            date=today,
            stock_code=s["code"],
            stock_name=s["name"],
            rank=i,
            amount=s["amount"],
            pct_change=s["pct_change"],
            turnover_rate=s["turnover_rate"],
            sector_name=sector_by_code.get(s["code"]),
            market=s["market"],
        ))
    db.commit()
    return {"ok": True, "count": len(stocks), "errors": []}


def _classify_bias(avg_pct: float, up_ratio: float, down_ratio: float) -> str:
    if avg_pct > _BIAS_PCT_THRESHOLD and up_ratio > _BIAS_RATIO_THRESHOLD:
        return "bullish"
    if avg_pct < -_BIAS_PCT_THRESHOLD and down_ratio > _BIAS_RATIO_THRESHOLD:
        return "bearish"
    return "mixed"


def get_turnover_overview(db: Session, force_refresh: bool = False) -> dict:
    """
    成交额概览。数据读 DB（daily_update 每日同步写入）；
    refresh=true 强制重新同步；库内当天无数据时自愈同步一次（10 分钟防抖）。
    """
    errors: List[str] = []
    if force_refresh:
        r = sync_turnover_pool(db)
        errors.extend(r.get("errors") or [])
    else:
        has_today = (
            db.query(TurnoverPoolDaily.id)
            .filter(TurnoverPoolDaily.date == _date.today())
            .first() is not None
        )
        if not has_today:
            last = _last_sync_attempt["ts"]
            if last is None or (datetime.now() - last).total_seconds() > _SYNC_DEBOUNCE:
                r = sync_turnover_pool(db)
                errors.extend(r.get("errors") or [])

    rows = (
        db.query(TurnoverPoolDaily)
        .filter(TurnoverPoolDaily.date == _date.today())
        .order_by(TurnoverPoolDaily.rank)
        .all()
    )
    if not rows:
        return {
            "date": None, "stocks": [], "sector_groups": [],
            "total_amount": 0.0, "new_count": 0, "overall_avg_pct": 0.0,
            "errors": errors or ["暂无成交额数据"],
        }

    today_date = rows[0].date
    prev_date_row = (
        db.query(TurnoverPoolDaily.date)
        .filter(TurnoverPoolDaily.date < today_date)
        .order_by(TurnoverPoolDaily.date.desc())
        .first()
    )
    prev_codes: set[str] = set()
    if prev_date_row:
        prev_codes = {
            r[0] for r in db.query(TurnoverPoolDaily.stock_code)
            .filter(TurnoverPoolDaily.date == prev_date_row[0]).all()
        }

    stocks: list[dict] = []
    groups: dict[str, dict] = {}
    total_amount = 0.0
    new_count = 0
    total_pct = 0.0

    for r in rows:
        is_new = r.stock_code not in prev_codes
        if is_new:
            new_count += 1
        total_amount += r.amount
        total_pct += r.pct_change
        stocks.append({
            "code": r.stock_code, "name": r.stock_name, "rank": r.rank,
            "amount": r.amount, "pct_change": r.pct_change, "turnover_rate": r.turnover_rate,
            "sector_name": r.sector_name, "market": r.market, "is_new": is_new,
        })
        key = r.sector_name or "未分类"
        g = groups.setdefault(key, {
            "name": key, "count": 0, "total_amount": 0.0, "total_pct": 0.0,
            "up": 0, "down": 0, "flat": 0, "new_count": 0,
        })
        g["count"] += 1
        g["total_amount"] += r.amount
        g["total_pct"] += r.pct_change
        if r.pct_change > 0:
            g["up"] += 1
        elif r.pct_change < 0:
            g["down"] += 1
        else:
            g["flat"] += 1
        if is_new:
            g["new_count"] += 1

    sector_groups = []
    for g in groups.values():
        avg_pct = g["total_pct"] / g["count"]
        up_ratio = g["up"] / g["count"]
        down_ratio = g["down"] / g["count"]
        sector_groups.append({
            "name": g["name"], "count": g["count"],
            "avg_pct_change": round(avg_pct, 2),
            "up_count": g["up"], "down_count": g["down"], "flat_count": g["flat"],
            "total_amount": g["total_amount"], "new_count": g["new_count"],
            "bias": _classify_bias(avg_pct, up_ratio, down_ratio),
        })
    sector_groups.sort(key=lambda g: g["total_amount"], reverse=True)

    return {
        "date": str(today_date),
        "stocks": stocks,
        "sector_groups": sector_groups,
        "total_amount": total_amount,
        "new_count": new_count,
        "overall_avg_pct": round(total_pct / len(rows), 2),
        "errors": errors,
    }
