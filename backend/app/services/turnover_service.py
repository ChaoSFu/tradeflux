"""
成交额概览引擎。

每日拉取「成交额排序前N」选股结果（prompt 可在 PoolConfig 页配置），落库
turnover_pool_daily；读取时按板块聚合赚钱效应、跟前一交易日名单对比标出
「新进」股票。板块归属沿用 daily_update._update_primary_sectors 的既有
约定：只认 is_watched=True 的板块，命中多个时取 F10 返回顺序里第一个。

## 落库日期 = 这批数据属于的交易日，不是跑的那天（2026-11-08 修）

选股 API 不带数据日期。原来一律写 date.today()：周末、节假日、开盘前打开页面触发
自愈同步，拿到的是上一交易日收盘的名单，却记在周日/节假日名下（本地库 60 行记在
2026-11-08 周日）。下游按交易日取数的地方（复盘核心池、数据体检的当天快照项）都对
不上这一天。现在：
  · daily_update 传它自己定好的 target_date（K线最大日期修正过的），不另算
  · 页面自愈/手动刷新不传，按市场时间 + 交易日历现算（resolve_trade_date）
  · 两条路都过同一道闸：日历明确说不是交易日、或者晚于市场时钟的日期，一律不写
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date as _date, datetime
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models.turnover_pool import TurnoverPoolDaily
from ..models.sector import Sector
from .eastmoney_fetcher import (
    SH_TZ, bar_is_settled, fetch_stock_bk_codes, fetch_turnover_top_stocks, probe_market_now,
)
from .pool_config_service import get_pool_keywords
from .trading_calendar import calendar_status, get_trading_days, last_weekday_before, market_data_day

_SYNC_DEBOUNCE = 600  # 库空自愈同步防抖（秒）
_last_sync_attempt: dict = {"ts": None}

# 赚钱效应方向判定阈值
_BIAS_PCT_THRESHOLD = 1.5
_BIAS_RATIO_THRESHOLD = 0.6


def _resolve_sector_names(db: Session, codes: List[str]) -> dict[str, Optional[str]]:
    """并发查每只股票所属的 is_watched 板块名，命中多个取第一个。"""
    import httpx

    watched = {s.code: s.name for s in db.query(Sector).filter(Sector.is_watched == True).all()}  # noqa: E712

    max_workers = 10

    def fetch_one(code: str, client: httpx.Client):
        return code, fetch_stock_bk_codes(code, client)

    result: dict[str, Optional[str]] = {}
    with httpx.Client(
        limits=httpx.Limits(max_connections=max_workers, max_keepalive_connections=max_workers),
    ) as shared_client:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(fetch_one, c, shared_client): c for c in codes}
            for future in as_completed(futures):
                code = futures[future]
                try:
                    _, bk_codes = future.result()
                except Exception:  # noqa: BLE001
                    result[code] = None
                    continue
                result[code] = next((watched[bk] for bk in bk_codes if bk in watched), None)
    return result


def _naive_sh(dt: datetime) -> datetime:
    return dt.astimezone(SH_TZ).replace(tzinfo=None) if dt.tzinfo else dt


def _calendar(db: Session, now: datetime):
    try:
        return get_trading_days(db, need_through=last_weekday_before(now.date()))
    except Exception:  # noqa: BLE001  日历是增强，不是单点
        return None


def resolve_trade_date(db: Session, trade_date: Optional[_date] = None,
                       market_now: Optional[datetime] = None
                       ) -> Tuple[Optional[_date], Optional[bool], Optional[str]]:
    """
    这批成交额前列数据该记在哪个交易日。返回 (日期, 是否已收盘, 不写的原因)。

    trade_date 由调用方定好的（daily_update 的 target_date）直接用；没给就按市场时间 +
    交易日历现算（trading_calendar.market_data_day）。两种都要过同一道闸，过不了返回
    (None, None, 原因)，调用方不写库：
      · 交易日历明确说这天不是交易日——行情停在上一交易日收盘，数据不属于这天
      · 晚于市场时钟的日期——行情还没走到那天，数据不可能属于它
      · 市场时间探测失败、日历又没覆盖到算出来的那天——说不准，宁可这次不写
    已收盘与否用 bar_is_settled，跟 daily_update 同一套判定。
    """
    now = market_now if market_now is not None else probe_market_now()
    from_market = now is not None
    now = _naive_sh(now if from_market else datetime.now(SH_TZ))
    cal = _calendar(db, now)

    day = trade_date
    if day is None:
        day = market_data_day(cal, now)
        if day is None:
            return None, None, "交易日历里找不到这批数据该属于的交易日，本次不写"
        if not from_market and not (cal and day <= cal[-1]):
            return None, None, (f"市场时间探测失败、交易日历也没覆盖到 {day}：说不准这批数据属于哪天，"
                                f"本次不写")
    if day > now.date():
        return None, None, f"{day} 晚于市场时间 {now:%Y-%m-%d %H:%M}，行情还没走到那天，本次不写"
    if cal and calendar_status(cal, day)["is_trading_day"] is False:
        return None, None, f"{day} 不是交易日（交易日历），本次不写——行情停在上一交易日收盘，数据不属于这天"
    return day, bar_is_settled(day, now), None


def sync_turnover_pool(db: Session, trade_date: Optional[_date] = None,
                       market_now: Optional[datetime] = None) -> dict:
    """
    拉取成交额前列股票，记在这批数据所属的交易日名下（同一天重跑会先清空当天旧行，幂等）。

    trade_date：daily_update 传它的 target_date；页面自愈/手动刷新不传，现算。见 resolve_trade_date。
    返回 {'ok': bool, 'count': n, 'date': 'YYYY-MM-DD'|None, 'settled': bool|None, 'errors': [...]}
    """
    _last_sync_attempt["ts"] = datetime.now()
    day, settled, why = resolve_trade_date(db, trade_date, market_now)
    if day is None:
        return {"ok": False, "count": 0, "date": None, "settled": None, "errors": [why]}
    keyword = get_pool_keywords(db)["turnover_pool_keyword"]
    stocks = fetch_turnover_top_stocks(keyword)
    if not stocks:
        return {"ok": False, "count": 0, "date": None, "settled": None,
                "errors": ["成交额选股 API 不可用（本次未拿到完整分页）"]}

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

    db.query(TurnoverPoolDaily).filter(TurnoverPoolDaily.date == day).delete()
    for i, s in enumerate(stocks, start=1):
        db.add(TurnoverPoolDaily(
            date=day,
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
    return {"ok": True, "count": len(stocks), "date": day.isoformat(), "settled": settled, "errors": []}


def _classify_bias(avg_pct: float, up_ratio: float, down_ratio: float) -> str:
    if avg_pct > _BIAS_PCT_THRESHOLD and up_ratio > _BIAS_RATIO_THRESHOLD:
        return "bullish"
    if avg_pct < -_BIAS_PCT_THRESHOLD and down_ratio > _BIAS_RATIO_THRESHOLD:
        return "bearish"
    return "mixed"


def _latest_archived_trading_date(db: Session, cal, before: Optional[_date] = None) -> Optional[_date]:
    """
    库里最近一个有存档的交易日（早于 before）。跳过交易日历明确判为非交易日的日期：
    2026-11-08 之前的版本会把上一交易日的名单记在周末/节假日名下，那些行清理之前
    不能被当成「最新一天」展示，也不能被当成「前一天」拿来标新进。
    """
    q = db.query(TurnoverPoolDaily.date).distinct()
    if before is not None:
        q = q.filter(TurnoverPoolDaily.date < before)
    for (d,) in q.order_by(TurnoverPoolDaily.date.desc()).limit(30):
        if not (cal and calendar_status(cal, d)["is_trading_day"] is False):
            return d
    return None


def get_turnover_overview(db: Session, force_refresh: bool = False) -> dict:
    """
    成交额概览。数据读 DB（daily_update 每日同步写入）；
    refresh=true 强制重新同步；库内「行情当前所在交易日」无数据时自愈同步一次（10 分钟防抖）。

    「行情当前所在交易日」按本机时钟 + 交易日历判（market_data_day）：读页面不为它去探测
    市场时间。周末/盘前就是上一交易日——那天有存档就直接展示，不再每个周末都触发一次
    同步、把上周五的名单再记一份到周日名下。真要同步时，落库日期由 sync_turnover_pool
    按市场时间定。

    要展示的那天没有数据时不展示空白页，退回展示最近一个有数据的交易日——跟大盘趋势
    等页面的自愈读取约定一致，避免用户误以为数据没更新/功能坏了。
    """
    errors: List[str] = []
    now = datetime.now(SH_TZ).replace(tzinfo=None)
    try:
        cal = get_trading_days(db)      # 只读缓存：读页面不为日历发请求，缓存落后也照样能判
    except Exception:  # noqa: BLE001
        cal = None
    want = market_data_day(cal, now)
    synced: Optional[str] = None
    if force_refresh:
        r = sync_turnover_pool(db)
        errors.extend(r.get("errors") or [])
        synced = r.get("date")
    else:
        has_want = want is not None and (
            db.query(TurnoverPoolDaily.id)
            .filter(TurnoverPoolDaily.date == want)
            .first() is not None
        )
        if not has_want:
            last = _last_sync_attempt["ts"]
            if last is None or (datetime.now() - last).total_seconds() > _SYNC_DEBOUNCE:
                r = sync_turnover_pool(db)
                errors.extend(r.get("errors") or [])
                synced = r.get("date")

    show = _date.fromisoformat(synced) if synced else want
    rows = (
        db.query(TurnoverPoolDaily)
        .filter(TurnoverPoolDaily.date == show)
        .order_by(TurnoverPoolDaily.rank)
        .all()
    ) if show else []
    if not rows:
        # 那天确实还没数据——退回最近一个有数据的交易日，而非空白页
        latest = _latest_archived_trading_date(db, cal)
        if latest:
            rows = (
                db.query(TurnoverPoolDaily)
                .filter(TurnoverPoolDaily.date == latest)
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
    prev_date = _latest_archived_trading_date(db, cal, before=today_date)
    prev_codes: set[str] = set()
    if prev_date:
        prev_codes = {
            r[0] for r in db.query(TurnoverPoolDaily.stock_code)
            .filter(TurnoverPoolDaily.date == prev_date).all()
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
