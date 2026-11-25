"""
个股当日事实 + 区间涨幅前列（2026-11-25）。替代「每天翻一遍全市场」：用到哪几只就问哪几只。

## facts(db, d, codes)

某个交易日一批股票的事实（名称、价、涨跌幅、5/10/20/60 日涨幅、成交额、量比、市值）。来源按顺序：
  1. 大市值快照（large_cap_market_daily，≥300 亿那批，收盘后同步）
  2. 已存的 stock_day_facts
  3. d 就是选股接口此刻给的那个交易日时，按代码问 prompt（一次 100 只），存进 stock_day_facts
历史日期没存过的问不回来（接口只给此刻），就是缺——调用方照实显示，不拿别的日子顶。
盘中问到的标 settled=False，收盘后再用到会重问一次。

## gain_tops(db, d)

人气核心池「区间涨幅前 20」（5 / 10 / 20 / 60 日，全市场）：每个窗口问一次「近 N 个交易日涨幅排名前 40」，
去掉北交所留前 30（竞争排名要看见并列第 20 名），存 AppConfig `prompt_gain_tops:YYYY-MM-DD`。
只在收盘后、而且接口给的就是 d 这天时才问——盘中的「前 20」会变，不拿来当当天结论。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from types import SimpleNamespace
from typing import Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models.app_config import AppConfig
from ..models.large_cap import LargeCapMarketDaily
from ..models.stock_day_fact import StockDayFact
from . import prompt_api

logger = logging.getLogger(__name__)

GAIN_WINDOWS = (5, 10, 20, 60)
GAIN_ASK = 40          # 问前 40（含北交所），去掉北交所后留前 30
GAIN_KEEP = 30
GAIN_KEY = "prompt_gain_tops:{}"
SOURCE_LARGE_CAP = "大市值快照"
SOURCE_PROMPT = "选股 prompt"

_FIELDS = ("stock_name", "is_st", "price", "pct_change", "pct_change_5d", "pct_change_10d", "pct_change_20d",
           "pct_change_60d", "amount", "turnover_rate", "volume_ratio", "total_market_cap", "industry")


_probe_cache: dict = {"at": 0.0, "now": None}
PROBE_TTL_S = 30


def _market_now() -> Optional[datetime]:
    """腾讯字段 30 的市场时间，进程内缓存 30 秒（按需取数时每次都探一下太多了）。"""
    import time as _t
    from .eastmoney_fetcher import probe_market_now
    if _probe_cache["now"] is not None and _t.monotonic() - _probe_cache["at"] < PROBE_TTL_S:
        return _probe_cache["now"]
    now = probe_market_now()
    if now is not None:
        _probe_cache.update(at=_t.monotonic(), now=now)
    return now


def might_be_current(d: date) -> bool:
    """d 离今天超过 5 天就不可能是接口此刻给的那天——不用去探市场时间。"""
    from datetime import timedelta
    from .eastmoney_fetcher import SH_TZ
    return d >= datetime.now(SH_TZ).date() - timedelta(days=5)


def current_day(db: Session, market_now: Optional[datetime] = None) -> Tuple[Optional[date], Optional[bool], datetime]:
    """选股接口此刻给的是哪个交易日的数据、收盘了没有。市场时间取腾讯字段 30，探测不到退回本机时钟。"""
    from .eastmoney_fetcher import SH_TZ, bar_is_settled
    from .trading_calendar import get_trading_days, last_weekday_before, market_data_day
    now = market_now if market_now is not None else _market_now()
    if now is None:
        now = datetime.now(SH_TZ)
    now = now.astimezone(SH_TZ).replace(tzinfo=None) if now.tzinfo else now
    try:
        cal = get_trading_days(db, need_through=last_weekday_before(now.date())) or []
    except Exception:  # noqa: BLE001
        db.rollback()
        cal = []
    d = market_data_day(cal, now)
    return d, (bar_is_settled(d, now) if d else None), now


def _ns(source: str, **kw) -> SimpleNamespace:
    base = {k: None for k in _FIELDS}
    base.update(kw)
    return SimpleNamespace(source=source, **base)


def _from_prompt_row(r: dict) -> dict:
    yi = lambda v: round(v / 1e8, 4) if v is not None else None  # noqa: E731
    iv = r.get("intervals") or {}
    name = r.get("name")
    return {"stock_name": name, "market": r.get("market"), "is_st": bool(name) and "ST" in name.upper(),
            "price": r.get("price"), "pct_change": r.get("pct_change"),
            "pct_change_5d": iv.get(5), "pct_change_10d": iv.get(10), "pct_change_20d": iv.get(20),
            "pct_change_60d": iv.get(60), "amount": yi(r.get("amount")), "turnover_rate": r.get("turnover_rate"),
            "volume_ratio": r.get("volume_ratio"), "total_market_cap": yi(r.get("total_market_cap"))}


def facts(db: Session, d: date, codes: Iterable[str], *, fetch: bool = True,
          market_now: Optional[datetime] = None) -> Dict[str, SimpleNamespace]:
    """d 这天这些票的事实。见模块说明。拿不到的不在返回里。"""
    codes = list(dict.fromkeys(c for c in codes if c))
    out: Dict[str, SimpleNamespace] = {}
    if not codes:
        return out
    for r in db.query(LargeCapMarketDaily).filter(LargeCapMarketDaily.trade_date == d,
                                                  LargeCapMarketDaily.stock_code.in_(codes)):
        out[r.stock_code] = _ns(SOURCE_LARGE_CAP, **{k: getattr(r, k) for k in _FIELDS})
    rest = [c for c in codes if c not in out]
    stored = {r.stock_code: r for r in db.query(StockDayFact).filter(StockDayFact.trade_date == d,
                                                                     StockDayFact.stock_code.in_(rest))} if rest else {}
    need = [c for c in rest if c not in stored]
    unsettled = [c for c, r in stored.items() if not r.settled]
    if fetch and (need or unsettled) and (market_now is not None or might_be_current(d)):
        cur, settled, _now = current_day(db, market_now)
        ask = need + (unsettled if settled else [])
        if cur == d and ask:
            got = _fetch_and_store(db, d, ask, bool(settled))
            stored.update(got)
    for c, r in stored.items():
        out[c] = _ns(SOURCE_PROMPT, **{k: getattr(r, k, None) for k in _FIELDS})
    return out


def _fetch_and_store(db: Session, d: date, codes: List[str], settled: bool) -> Dict[str, StockDayFact]:
    try:
        rows = prompt_api.fetch_codes(codes)
    except prompt_api.PromptError as e:
        logger.warning("按代码问选股 prompt 失败（%d 只）: %s", len(codes), e)
        return {}
    got: Dict[str, StockDayFact] = {}
    now = datetime.now()
    existing = {r.stock_code: r for r in db.query(StockDayFact).filter(StockDayFact.trade_date == d,
                                                                       StockDayFact.stock_code.in_(list(rows)))}
    for code, r in rows.items():
        if r.get("data_date") != d:          # 接口给的不是 d 这天：不存
            continue
        vals = _from_prompt_row(r)
        row = existing.get(code) or StockDayFact(trade_date=d, stock_code=code)
        for k, v in vals.items():
            setattr(row, k, v)
        row.settled, row.fetched_at = settled, now
        if code not in existing:
            db.add(row)
        got[code] = row
    try:
        db.commit()
    except Exception as e:  # noqa: BLE001 —— 并发写同一行：这次不存，下次再问
        db.rollback()
        logger.warning("stock_day_facts 写入失败: %r", e)
    return got


def industry_of(db: Session, codes: Iterable[str], d: date) -> Dict[str, str]:
    """二级行业：d 当天或之前最近一份大市值快照 → 涨停明细里的东财行业。都没有就不在返回里。"""
    from sqlalchemy import func
    from ..models.limit_up_detail import LimitUpDailyDetail
    codes = list(dict.fromkeys(c for c in codes if c))
    out: Dict[str, str] = {}
    if not codes:
        return out
    sub = (db.query(LargeCapMarketDaily.stock_code, func.max(LargeCapMarketDaily.trade_date).label("d"))
           .filter(LargeCapMarketDaily.stock_code.in_(codes), LargeCapMarketDaily.trade_date <= d,
                   LargeCapMarketDaily.industry.isnot(None))
           .group_by(LargeCapMarketDaily.stock_code).subquery())
    for c, ind in (db.query(LargeCapMarketDaily.stock_code, LargeCapMarketDaily.industry)
                   .join(sub, (sub.c.stock_code == LargeCapMarketDaily.stock_code)
                         & (sub.c.d == LargeCapMarketDaily.trade_date))):
        out[c] = ind
    rest = [c for c in codes if c not in out]
    if rest:
        for c, ind, _d in (db.query(LimitUpDailyDetail.stock_code, LimitUpDailyDetail.em_industry,
                                    LimitUpDailyDetail.trade_date)
                           .filter(LimitUpDailyDetail.stock_code.in_(rest), LimitUpDailyDetail.trade_date <= d,
                                   LimitUpDailyDetail.em_industry.isnot(None))
                           .order_by(LimitUpDailyDetail.trade_date.desc())):
            out.setdefault(c, ind)
    return out


# ── 区间涨幅前列 ─────────────────────────────────────────────────────────────

def gain_keyword(w: int) -> str:
    return f"近{w}个交易日涨幅排名前{GAIN_ASK}"


def sync_gain_tops(db: Session, d: date, market_now: Optional[datetime] = None) -> dict:
    """收盘后问 4 个窗口的前列，存 AppConfig。返回 {ok, reason, counts}。已有就不再问。"""
    from .eastmoney_fetcher import _is_bj_code
    key = GAIN_KEY.format(d.isoformat())
    if db.query(AppConfig.id).filter(AppConfig.key == key).first():
        return {"ok": True, "reason": "已有", "counts": None}
    if market_now is None and not might_be_current(d):
        return {"ok": False, "reason": f"{d} 是历史日期，选股接口只给此刻的数据", "counts": None}
    cur, settled, now = current_day(db, market_now)
    if cur != d or not settled:
        return {"ok": False, "reason": f"选股接口此刻给的是 {cur}{'（已收盘）' if settled else '（未收盘）'}，不是 {d} 的收盘数据",
                "counts": None}
    windows: Dict[str, list] = {}
    for w in GAIN_WINDOWS:
        try:
            items, _total = prompt_api.search_all(gain_keyword(w), page_size=GAIN_ASK, max_pages=2)
        except prompt_api.PromptError as e:
            return {"ok": False, "reason": f"{w} 日前列没拿到（{e}）", "counts": None}
        rows = [prompt_api.parse_row(it) for it in items]
        rows = [r for r in rows if r and not _is_bj_code(r["code"])]
        dates = {r["data_date"] for r in rows if r.get("data_date")}
        if dates != {d}:
            return {"ok": False, "reason": f"{w} 日前列的数据日期是 {sorted(map(str, dates))}，不是 {d}", "counts": None}
        keep = sorted((r for r in rows if (r["intervals"] or {}).get(w) is not None),
                      key=lambda r: -r["intervals"][w])[:GAIN_KEEP]
        windows[str(w)] = [[r["code"], r["name"], r["intervals"][w], bool(r["name"]) and "ST" in r["name"].upper()]
                           for r in keep]
    val = json.dumps({"windows": windows, "fetched_at": datetime.now().isoformat(timespec="seconds"),
                      "asked": GAIN_ASK}, ensure_ascii=False)
    db.add(AppConfig(key=key, value=val))
    db.commit()
    return {"ok": True, "reason": None, "counts": {w: len(v) for w, v in windows.items()}}


def gain_tops(db: Session, d: date) -> Optional[Dict[int, Dict[str, float]]]:
    """{窗口: {代码: 区间涨幅}}，没存过 = None。"""
    row = db.query(AppConfig).filter(AppConfig.key == GAIN_KEY.format(d.isoformat())).first()
    if row is None or not row.value:
        return None
    try:
        data = json.loads(row.value)["windows"]
        return {int(w): {c: pct for c, _n, pct, _st in rows} for w, rows in data.items()}
    except (ValueError, KeyError, TypeError):
        return None


def gain_top_names(db: Session, d: date) -> Dict[str, Tuple[str, bool]]:
    """前列里每只的 (名称, 是否 ST)——它们不一定在别的表里。"""
    row = db.query(AppConfig).filter(AppConfig.key == GAIN_KEY.format(d.isoformat())).first()
    if row is None or not row.value:
        return {}
    try:
        data = json.loads(row.value)["windows"]
    except (ValueError, KeyError, TypeError):
        return {}
    return {c: (n, bool(st)) for rows in data.values() for c, n, _p, st in rows}
