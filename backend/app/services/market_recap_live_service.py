"""
每日复盘 · 盘中跟踪（2026-11-09）：盘中把「上一交易日锁定的预案、观察池、关联股、人气核心池」
和实时行情放在一起看，盘中操作时有最新数据可参考。接口 `GET /market-recap/live`。

## 跟盘后复盘严格分开

- **只读，一行不写**：不落库、不冻结核心池、不碰锁定版本、不产生正式活跃事件。盘后复盘照旧只用
  收盘后同步的数据（PRD：没有已结算数据时不得生成正式收盘事件；盘中事件要单独标记为临时事件）。
- **盘中事件一律是临时的**（provisional）：此刻封住的可能尾盘炸，此刻炸开的可能回封。收盘同步后
  以盘后复盘为准。
- **拿不到 = 未知**：哪只没行情就标「没行情」，统计只用拿到的、并报样本数，不当 0。
- **时间以行情自带的市场时间为准**（腾讯字段 30），收没收盘走 bar_is_settled；本机时钟只用来
  区分盘中的几个时段（竞价 / 连续竞价 / 午休 / 尾盘集合竞价）。
- 涨停 / 炸板 / 跌停走 kline_bar_from_quote（跟日更补 K 线同一套判定），不另写一份。

## 执行的是哪一天的预案

行情是今天的（盘中 / 午休 / 收盘后到盘后同步之前）：执行的是**上一交易日 R** 锁定的预案。
行情还停在 R（开盘前、周末、节假日）：执行的是 R 锁定的、给下一交易日的预案，价格是 R 的收盘。
"""
import copy
import logging
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from datetime import date, datetime, time as dtime
from time import monotonic
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import httpx
from sqlalchemy.orm import Session

from ..models.market_index import MarketBreadthDaily
from ..models.stock import Stock, StockDailySnapshot
from . import market_recap_store as store
from . import popular_core_service as pc
from .eastmoney_fetcher import (
    HEADERS, SH_TZ, StockQuote, _fetch_quotes_tencent, bar_is_settled, fetch_stock_quotes_batch,
    kline_bar_from_quote, market_int,
)
from .limit_up_detail_fetcher import fetch_broken_board_pool, fetch_limit_up_pool
from .market_effect_service import _cohort_snapshots
from .market_recap_service import seal_rate_of
from .trading_calendar import calendar_status, market_data_day, prev_trading_day
from .windvane_service import _fetch_trends_projection, _fetch_updown

logger = logging.getLogger(__name__)

INDEXES = [("000001", 1, "上证指数"), ("399001", 0, "深证成指"), ("399006", 0, "创业板指"),
           ("000688", 1, "科创50")]
PROVISIONAL_NOTE = "盘中临时：此刻的状态，收盘前随时会变；正式结论以盘后同步后的复盘为准"

PHASE_ZH = {
    "pre_open": "未开盘", "auction": "集合竞价", "auction_done": "竞价结束、等待开盘",
    "trading": "盘中", "lunch": "午间休市", "closing_auction": "尾盘集合竞价",
    "closed": "已收盘（盘后同步前）", "non_trading": "休市", "stale": "行情没有更新", "unknown": "行情时间未知",
}
LIVE_PHASES = {"auction", "auction_done", "trading", "lunch", "closing_auction", "closed"}
POLL_LIVE_S, POLL_LUNCH_S, POLL_RETRY_S, POLL_MAX_WAIT_S = 30, 300, 60, 600

QUOTE_TTL_S = 15            # 同一只票 15 秒内不重复取（多开几个标签页也不会多打行情源）
MARKET_TTL_S = 30           # 全市场几路（涨跌分布、涨停池、成交额）30 秒
CORES_TTL_S = 600           # 上一交易日冻结的核心池不会变；现算的也不必每 30 秒重算
SOURCE_BUDGET_S = 8.0       # 外部数据最多等这么久，没回来的这次标未知（线程跑完照样进缓存）

_T_AUCTION, _T_AUCTION_END, _T_OPEN = dtime(9, 15), dtime(9, 25), dtime(9, 30)
_T_AM_CLOSE, _T_PM_OPEN, _T_CLOSE_AUCTION = dtime(11, 30), dtime(13, 0), dtime(14, 57)

_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="recap-live")
_cache: Dict[tuple, tuple] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cached(key: tuple, ttl: float, fn: Callable[[], Any]) -> Any:
    """取到的才缓存（None / 异常不缓存，下次再试）；给副本，调用方改了不串到下一次。"""
    now = monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return copy.deepcopy(hit[1])
    val = fn()
    if val is not None:
        with _cache_lock:
            _cache[key] = (monotonic(), copy.deepcopy(val))
    return val


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone(SH_TZ).replace(tzinfo=None) if dt.tzinfo else dt


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


# ── 时段：今天是盘中、午休、收盘，还是根本没开 ─────────────────────────────────

def session_of(market_dt: Optional[datetime], local_now: datetime, cal: Sequence[date]) -> dict:
    """
    market_dt = 行情自带的最新时间（市场时间）；local_now = 本机上海时间。
    返回 phase、行情属于哪天（data_date）、执行哪天锁定的预案（ref_date）、预案的目标日（target_date）。
    """
    today = local_now.date()
    md = market_dt.date() if market_dt else None
    st = calendar_status(list(cal), today)
    if md == today:
        if bar_is_settled(today, market_dt):
            phase = "closed"
        else:
            t = local_now.time()
            phase = ("pre_open" if t < _T_AUCTION else "auction" if t < _T_AUCTION_END else
                     "auction_done" if t < _T_OPEN else "trading" if t < _T_AM_CLOSE else
                     "lunch" if t < _T_PM_OPEN else "trading" if t < _T_CLOSE_AUCTION else "closing_auction")
        ref = prev_trading_day(sorted(cal), today)
        return {"phase": phase, "data_date": today, "ref_date": ref, "target_date": today,
                "target_is_provisional": False, "calendar": st}
    # 行情不是今天的：开盘前、休市，或者行情源没更新
    if md is None:
        phase = "unknown"
    elif st["is_trading_day"] is False:
        phase = "non_trading"
    elif local_now.time() < _T_AUCTION:
        phase = "pre_open"
    else:
        phase = "stale"
    if md is not None:
        ref = md
    else:
        # 拿不到行情时间：按本机时钟推「行情此刻属于哪天」，是今天就执行前一交易日的预案
        dd = market_data_day(cal, local_now)
        ref = prev_trading_day(sorted(cal), today) if dd == today else dd
    target, provisional = store.next_trading_day(cal, ref) if ref else (None, True)
    return {"phase": phase, "data_date": md, "ref_date": ref, "target_date": target,
            "target_is_provisional": provisional, "calendar": st}


def poll_seconds(phase: str, local_now: datetime) -> Optional[int]:
    """建议的刷新间隔。None = 不用自动刷新（已收盘、休市）。"""
    if phase in ("auction", "auction_done", "trading", "closing_auction"):
        return POLL_LIVE_S
    if phase == "lunch":
        until = (datetime.combine(local_now.date(), _T_PM_OPEN) - local_now).total_seconds()
        return int(max(POLL_LIVE_S, min(POLL_LUNCH_S, until)))
    if phase == "pre_open":
        if local_now.weekday() >= 5:
            return None
        until = (datetime.combine(local_now.date(), _T_AUCTION) - local_now).total_seconds()
        return int(max(POLL_LIVE_S, min(POLL_MAX_WAIT_S, until)))
    if phase in ("stale", "unknown"):
        return POLL_RETRY_S
    return None


# ── 个股实时事实 ────────────────────────────────────────────────────────────

def _quotes(codes_markets: List[Tuple[str, int]]) -> Dict[str, StockQuote]:
    """逐只按 QUOTE_TTL_S 缓存，只取过期的那些。"""
    now = monotonic()
    out: Dict[str, StockQuote] = {}
    need = []
    with _cache_lock:
        for c, m in codes_markets:
            hit = _cache.get(("quote", c))
            if hit and now - hit[0] < QUOTE_TTL_S:
                out[c] = hit[1]
            else:
                need.append((c, m))
    if need:
        got = fetch_stock_quotes_batch(need)
        ts = monotonic()
        with _cache_lock:
            for c, q in got.items():
                _cache[("quote", c)] = (ts, q)
        out.update(got)
    return out


def live_fact(code: str, q: Optional[StockQuote], d: Optional[date], is_st: bool, name: Optional[str]) -> dict:
    """
    一只票此刻的事实。键名跟 stock_facts 对齐（pct / is_limit_up / status），classify_response 直接能用。
    limit：sealed 此刻封涨停 / broken 摸过涨停没封住 / limit_down 此刻跌停 / None。
    """
    base = {"code": code, "name": name, "status": "missing", "price": None, "pct": None, "open": None,
            "high": None, "low": None, "prev_close": None, "amount_yi": None, "is_limit_up": None,
            "limit": None, "observed_at": None, "note": None}
    if q is None or not q.price:
        base["note"] = "没拿到行情"
        return base
    base["observed_at"] = _iso(_naive(q.trade_dt))
    if d is None or q.trade_date != d:
        base.update(status="stale", note=f"行情日期 {q.trade_date}，不是 {d}")
        return base
    bar = kline_bar_from_quote(q, code, is_st, d)
    if bar is None:
        # 日期对、但没有可信价格或今天没成交（停牌、竞价没撮合上）
        base.update(status="no_trade", prev_close=q.prev_close, note="今天还没有成交（停牌或竞价未撮合）")
        return base
    base.update(status="ok", price=q.price, pct=round(bar.pct_change, 2), open=q.open or None,
                high=q.high or None, low=q.low or None, prev_close=q.prev_close,
                amount_yi=round(q.amount / 1e8, 4) if q.amount else None,
                is_limit_up=bool(bar.is_limit_up),
                limit=("sealed" if bar.is_limit_up else "broken" if bar.is_broken_board else
                       "limit_down" if bar.is_limit_down else None))
    return base


def _names(db: Session, codes: Iterable[str]) -> Dict[str, Tuple[Optional[str], bool, int]]:
    """代码 → (名称, 是否 ST, 行情 market)。库里没有的按代码前缀猜交易所。"""
    codes = list(dict.fromkeys(codes))
    rows = {s.code: s for s in db.query(Stock.code, Stock.name, Stock.market, Stock.is_st)
            .filter(Stock.code.in_(codes))} if codes else {}
    out = {}
    for c in codes:
        s = rows.get(c)
        if s is not None:
            out[c] = (s.name, bool(s.is_st) or "ST" in (s.name or ""), market_int(s.market, c))
        else:
            out[c] = (None, False, market_int("SH" if c.startswith(("6", "5", "9")) else "SZ", c))
    return out


# ── 全市场几路（网络，放线程里） ──────────────────────────────────────────────

def _index_quotes() -> Optional[Dict[str, StockQuote]]:
    # 指数单独一次请求：上证指数 000001 跟平安银行 000001 同代码，混在个股里会串号
    got = _fetch_quotes_tencent([(c, m) for c, m, _ in INDEXES], timeout=8)
    return got or None


def _breadth() -> Optional[dict]:
    with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=8) as c:
        ud = _fetch_updown(c)
    return {"up": ud.up, "down": ud.down, "flat": ud.flat, "limit_up": ud.limit_up,
            "limit_down": ud.limit_down, "observed_at": _iso(datetime.now(SH_TZ).replace(tzinfo=None))}


def _limit_pools(d: date) -> Optional[dict]:
    zt = fetch_limit_up_pool(d, timeout=8)
    zb = fetch_broken_board_pool(d, timeout=8)
    t = lambda x: x.strftime("%H:%M:%S") if x else None  # noqa: E731
    return {"sealed": [{"code": x.code, "name": x.name, "board": x.board_count, "first_time": t(x.first_limit_time),
                        "last_time": t(x.last_limit_time), "broken_times": x.broken_times,
                        "industry": x.em_industry, "pct": x.pct_change}
                       for x in zt],
            "broken": [{"code": x.code, "name": x.name, "first_time": t(x.first_limit_time),
                        "broken_times": x.broken_times, "industry": x.em_industry, "pct": x.pct_change}
                       for x in zb],
            "observed_at": _iso(datetime.now(SH_TZ).replace(tzinfo=None))}


def _amount() -> Optional[dict]:
    proj, err = _fetch_trends_projection()      # 60 秒缓存，不落库（落库的是风向标那条路）
    if err:
        raise RuntimeError(err)
    return proj


def _wait(f, t0: float, label: str) -> Tuple[Any, Optional[str]]:
    try:
        return f.result(timeout=max(0.05, SOURCE_BUDGET_S - (monotonic() - t0))), None
    except FutTimeout:
        return None, f"{SOURCE_BUDGET_S:g} 秒没回，这次先不等（晚到的会进缓存，下次刷新就有）"
    except Exception as e:  # noqa: BLE001
        logger.warning("盘中跟踪 %s 取数失败: %r", label, e)
        return None, f"取数失败（{type(e).__name__}）"


def _meta(status: str, source: str, observed_at=None, note: Optional[str] = None) -> dict:
    return {"status": status, "source": source, "observed_at": observed_at, "note": note,
            "provisional": True}


# ── 组装 ──────────────────────────────────────────────────────────────────

def _cores(db: Session, ref: date, cal: Sequence[date]) -> dict:
    def build():
        from sqlalchemy import func
        latest = db.query(func.max(StockDailySnapshot.date)).scalar()
        res = pc.load(db, ref, cal, latest)
        return {"frozen": res["frozen"], "built_at": _iso(res.get("built_at")), "sources": res["sources"],
                "items": res["items"], "events": res["events"]}
    return _cached(("cores", ref, pc.RULE_VERSION), CORES_TTL_S, build)


def _prev_snaps(db: Session, codes: Iterable[str], ref: Optional[date]) -> Dict[str, StockDailySnapshot]:
    codes = list(codes)
    if not ref or not codes:
        return {}
    return {s.code: x for s, x in db.query(Stock, StockDailySnapshot)
            .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
            .filter(StockDailySnapshot.date == ref, Stock.code.in_(codes))}


def live_events(code: str, f: dict, prev: Optional[StockDailySnapshot], first_time: Optional[str]) -> List[dict]:
    """
    盘中临时事件，规则跟 popular_core_service.compute_events 对齐（今日涨停 / 再次涨停 / 连板延续 / 断板），
    多一个盘中才有意义的「炸板」。全部 provisional：收盘前随时会变，不进活跃事件表。
    昨天没有快照就不判再次涨停 / 连板延续 / 断板（跟收盘口径一样，缺了不出事件）。
    """
    if f.get("status") != "ok":
        return []
    out = []

    def ev(t, evidence):
        out.append({"event_type": t, "evidence": evidence, "time": first_time, "provisional": True})
    lim = f.get("limit")
    if lim == "sealed":
        ev("limit_up", "此刻封涨停" + (f"，首封 {first_time}" if first_time else ""))
        if prev is not None and not prev.is_limit_up and (prev.board_count_60d or 0) >= 2:
            ev("relimit", f"昨天没涨停，之前 60 日最高 {prev.board_count_60d} 板，此刻重新封板")
        if prev is not None and prev.is_limit_up and (prev.board_count or 0) >= 1:
            ev("streak_continue", f"{prev.board_count} → {prev.board_count + 1} 板（此刻封住）")
    elif lim == "broken":
        ev("broken", "摸过涨停、此刻没封住")
    elif lim == "limit_down":
        ev("limit_down", "此刻跌停")
    if lim != "sealed" and prev is not None and prev.is_limit_up and (prev.board_count or 0) >= 2:
        ev("streak_break", f"昨天 {prev.board_count} 板，此刻没封涨停（{f.get('pct')}%）")
    return out


# 叫法跟收盘事件分开：盘中说「今日涨停」「断板」都太早，尾盘还会变
LIVE_EVENT_LABEL = {"limit_up": "封板中", "relimit": "再次封板", "streak_continue": "连板中",
                    "streak_break": "暂未连板", "broken": "炸板", "limit_down": "跌停"}


def _cohort(members: List[str], facts: Dict[str, dict]) -> dict:
    got = [facts[c] for c in members if facts.get(c, {}).get("status") == "ok"]
    pcts = [f["pct"] for f in got if f.get("pct") is not None]
    return {"members": len(members), "quoted": len(got),
            "median_pct": round(statistics.median(pcts), 2) if pcts else None,
            "red_ratio": round(sum(1 for p in pcts if p > 0) / len(pcts), 3) if pcts else None,
            "sealed": sum(1 for f in got if f.get("limit") == "sealed"),
            "broken": sum(1 for f in got if f.get("limit") == "broken"),
            "limit_down": sum(1 for f in got if f.get("limit") == "limit_down")}


def build_live(db: Session, owner: str, local_now: Optional[datetime] = None) -> dict:
    local_now = local_now or datetime.now(SH_TZ).replace(tzinfo=None)
    from .trading_calendar import get_trading_days, last_weekday_before
    try:
        cal = get_trading_days(db, need_through=last_weekday_before(local_now.date())) or []
    except Exception:  # noqa: BLE001
        cal = []

    t0 = monotonic()
    f_idx = _pool.submit(_cached, ("index",), QUOTE_TTL_S, _index_quotes)
    idx_quotes, idx_err = _wait(f_idx, t0, "指数")
    market_dt = max((_naive(q.trade_dt) for q in (idx_quotes or {}).values() if q.trade_dt), default=None)

    # 先用指数行情定时段；指数没拿到再用个股行情的时间补（见下）
    sess = session_of(market_dt, local_now, cal)
    ref, data_d = sess["ref_date"], sess["data_date"]
    live = sess["phase"] in LIVE_PHASES

    # ── 我的数据（主线程查库） ──
    pool_day = sess["target_date"] or local_now.date()
    watch = store.watch_pool(db, owner, max(pool_day, local_now.date()))
    deep_codes = [w.stock_code for w in watch if w.deep]
    rels = store.relations_for(db, owner, deep_codes) if deep_codes else []
    chosen = store.chosen_version(db, owner, ref, cal) if ref else None
    plan_source, plans, plan_meta = None, [], {}
    if chosen is not None:
        plan_source, plans = "locked", chosen["payload"].get("plans") or []
        rec = chosen["payload"].get("recap") or {}
        plan_meta = {"version": chosen["version"], "locked_at": _iso(chosen["locked_at"]),
                     "pre_open": chosen["pre_open"], "backfilled": chosen["backfilled"],
                     "posture": rec.get("market_posture"), "key_points": rec.get("key_points") or [],
                     "invalidation": rec.get("invalidation") or [], "flat_plan": bool(rec.get("flat_plan")),
                     "plan_facts": chosen["payload"].get("plan_facts") or {}}
    elif ref:
        g = store.get_recap(db, owner, ref)
        if g["plans"] or g["recap"]:
            plan_source = "draft"
            plans = [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in p.items()} for p in g["plans"]]
            rec = g["recap"] or {}
            plan_meta = {"posture": rec.get("market_posture"), "key_points": rec.get("key_points") or [],
                         "invalidation": rec.get("invalidation") or [], "flat_plan": bool(rec.get("flat_plan"))}
    cores, cores_err = None, None
    if ref:
        try:
            cores = _cores(db, ref, cal)
        except Exception as e:  # noqa: BLE001 —— 核心池挂了不拖垮整页
            db.rollback()
            logger.warning("盘中跟踪：%s 人气核心池读取失败: %r", ref, e)
            cores_err = f"{ref} 人气核心池读取失败（{type(e).__name__}）"
    cohort_ids = {k: [m.stock_id for m in _cohort_snapshots(db, ref, k)] for k in
                  ("limit_up", "multi_board", "broken_board")} if ref else {}
    ids = {i for v in cohort_ids.values() for i in v}
    id_code = dict(db.query(Stock.id, Stock.code).filter(Stock.id.in_(ids))) if ids else {}
    cohort_codes = {k: [id_code[i] for i in v if i in id_code] for k, v in cohort_ids.items()}

    core_items = (cores or {}).get("items") or []
    all_codes = list(dict.fromkeys(
        [p["stock_code"] for p in plans] + [w.stock_code for w in watch]
        + [c for r in rels for c in (r.leader_code, r.related_code)]
        + [i["stock_code"] for i in core_items] + [c for v in cohort_codes.values() for c in v]))
    info = _names(db, all_codes)
    prev = _prev_snaps(db, all_codes, ref)
    prev_amount = None
    if ref:
        try:
            row = db.query(MarketBreadthDaily.deal_amount).filter(MarketBreadthDaily.date == ref).first()
            prev_amount = row[0] if row else None
        except Exception:  # noqa: BLE001 —— 只是个对照值，缺了照样出此刻的成交额
            db.rollback()

    # ── 网络：个股行情 + 全市场几路一起发 ──
    t1 = monotonic()
    f_q = _pool.submit(_quotes, [(c, info[c][2]) for c in all_codes]) if all_codes else None
    f_live = {}
    if live:
        f_live = {"breadth": _pool.submit(_cached, ("breadth", data_d), MARKET_TTL_S, _breadth),
                  "pools": _pool.submit(_cached, ("pools", data_d), MARKET_TTL_S, lambda: _limit_pools(data_d)),
                  "amount": _pool.submit(_cached, ("amount", data_d), MARKET_TTL_S, _amount)}
    quotes, q_err = _wait(f_q, t1, "个股行情") if f_q else ({}, None)
    quotes = quotes or {}
    if market_dt is None and quotes:
        # 指数没拿到：用个股行情自带的时间重新定时段。上面按本机时钟推的「执行哪天的预案」对不上就
        # 不换——预案、核心池都是按那一天查的，换了时段不换数据会张冠李戴
        mdt = max((_naive(q.trade_dt) for q in quotes.values() if q.trade_dt), default=None)
        alt = session_of(mdt, local_now, cal) if mdt else None
        if alt is not None and alt["ref_date"] == ref:
            market_dt, sess = mdt, alt
            data_d, live = sess["data_date"], sess["phase"] in LIVE_PHASES
    got = {k: _wait(f, t1, k) for k, f in f_live.items()}
    for k in ("breadth", "pools", "amount"):
        got.setdefault(k, (None, "指数行情这次没拿到，没取全市场数据；下次刷新再取"))

    name_of = {}
    for i in core_items:
        name_of.setdefault(i["stock_code"], i.get("stock_name"))
    for w in watch:
        name_of.setdefault(w.stock_code, w.stock_name)
    for p in plans:
        name_of.setdefault(p["stock_code"], p.get("stock_name"))
    for r in rels:
        name_of.setdefault(r.leader_code, r.leader_name)
        name_of.setdefault(r.related_code, r.related_name)
    facts = {c: live_fact(c, quotes.get(c), data_d, info[c][1], info[c][0] or name_of.get(c)) for c in all_codes}

    pools, pools_err = got.get("pools", (None, None))
    first_time = {x["code"]: x["first_time"] for x in (pools or {}).get("sealed") or []}
    first_time.update({x["code"]: x["first_time"] for x in (pools or {}).get("broken") or []
                       if x["code"] not in first_time})
    for c, f in facts.items():
        f["first_limit_time"] = first_time.get(c) if f.get("limit") in ("sealed", "broken") else None
        f["events"] = live_events(c, f, prev.get(c), f["first_limit_time"]) if live else []
        p = prev.get(c)
        f["prev"] = ({"pct": p.pct_change, "is_limit_up": bool(p.is_limit_up), "board_count": p.board_count}
                     if p is not None else None)

    # ── 市场 ──
    indices = []
    for c, _m, name in INDEXES:
        q = (idx_quotes or {}).get(c)
        ok = q is not None and q.price and q.trade_date == data_d
        indices.append({"code": c, "name": name, "price": q.price if ok else None,
                        "pct": q.pct_change if ok else None, "amount_yi": round(q.amount / 1e8, 2) if ok and q.amount else None,
                        "observed_at": _iso(_naive(q.trade_dt)) if q else None,
                        "status": "ok" if ok else ("stale" if q else "missing")})
    market: Dict[str, Any] = {
        "indices": indices,
        "indices_meta": _meta("ok" if all(i["status"] == "ok" for i in indices) else
                              "missing" if idx_quotes is None else "partial", "腾讯实时行情",
                              _iso(market_dt), idx_err),
    }
    if live:
        b, b_err = got["breadth"]
        market["breadth"] = {**(b or {}), "meta": _meta("ok" if b else "missing", "东财涨跌分布（实时）",
                                                        (b or {}).get("observed_at"), b_err)}
        if pools:
            lu, br = len(pools["sealed"]), len(pools["broken"])
            _, rate = seal_rate_of(lu, br)
            boards = [x["board"] or 1 for x in pools["sealed"]]
            market["limit"] = {"sealed": lu, "broken": br, "seal_rate": rate, "max_board": max(boards, default=None),
                               "meta": _meta("ok", "东财涨停池 + 炸板池（实时）", pools["observed_at"],
                                             None if rate is not None else "涨停或炸板任一为 0，封板率先不算")}
        else:
            market["limit"] = {"meta": _meta("missing", "东财涨停池 + 炸板池（实时）", None, pools_err)}
        a, a_err = got["amount"]
        if a and a.get("date") == (data_d.isoformat() if data_d else None):
            market["amount"] = {"amount": a["amount"], "estimate": a.get("estimate"), "is_trading": a.get("is_trading"),
                                "prev_date": ref, "prev_amount": prev_amount,
                                "estimate_vs_prev_pct": (round((a["estimate"] / prev_amount - 1) * 100, 2)
                                                         if a.get("estimate") and prev_amount else None),
                                "meta": _meta("ok", "新浪 5 分钟 / 东财分钟成交额（沪 000001 + 深 399106）", None,
                                              "预估全天 = 此刻累计 +（昨日全天 − 昨日同时段），假设余下时段跟昨天一样")}
        else:
            market["amount"] = {"prev_date": ref, "prev_amount": prev_amount,
                                "meta": _meta("missing", "分钟成交额", None, a_err or "分钟数据还不是今天的")}
    market["cohorts"] = {k: _cohort(v, facts) for k, v in cohort_codes.items()} if ref else {}

    # ── 预案 ──
    plan_rows = []
    for p in plans:
        f = facts.get(p["stock_code"]) or {}
        stop = p.get("planned_stop")
        price = f.get("price")
        plan_rows.append({**{k: p.get(k) for k in ("stock_code", "stock_name", "scenario", "sector_name", "reason",
                                                   "entry_trigger", "invalidation", "planned_stop", "risk_budget",
                                                   "position_limit", "leader_code", "relation_note", "watch_trigger")},
                          "live": f,
                          "below_stop": (price < stop) if price is not None and stop else None,
                          "to_stop_pct": round((price / stop - 1) * 100, 2) if price is not None and stop else None})

    # ── 重点高标与关联 ──
    by_leader: Dict[str, list] = {}
    for r in rels:
        by_leader.setdefault(r.leader_code, []).append(r)
    leaders = []
    for c in deep_codes:
        lf = facts.get(c) or {}
        lev = [e["event_type"] for e in lf.get("events") or []]
        rows, seen = [], {}
        for r in by_leader.get(c, []):
            rf = facts.get(r.related_code) or {}
            if r.related_code in seen:      # 一只票挂几个属性，只列一行，属性合并
                seen[r.related_code]["attributes"].append({"type": r.attribute_type, "value": r.attribute_value,
                                                          "basis": r.relation_basis})
                continue
            row = {"code": r.related_code, "name": r.related_name or rf.get("name"), "live": rf,
                   "attributes": [{"type": r.attribute_type, "value": r.attribute_value, "basis": r.relation_basis}],
                   "system_response": pc.classify_response(rf, lf, lev) if live else None}
            seen[r.related_code] = row
            rows.append(row)
        resp: Dict[str, int] = {}
        for x in rows:
            if x["system_response"]:
                resp[x["system_response"]] = resp.get(x["system_response"], 0) + 1
        leaders.append({"code": c, "name": lf.get("name"), "live": lf, "relations": rows, "by_response": resp})

    # ── 观察池、核心池 ──
    watch_rows = [{"code": w.stock_code, "name": w.stock_name or (facts.get(w.stock_code) or {}).get("name"),
                   "deep": bool(w.deep), "note": w.note, "live": facts.get(w.stock_code)} for w in watch]
    watched = {w.stock_code for w in watch}
    core_rows = [{"code": i["stock_code"], "name": i.get("stock_name"), "board": i.get("board"),
                  "sources": [s["source"] for s in i.get("selection_sources") or []],
                  "lifecycle_state": i.get("lifecycle_state"), "sector": i.get("sector"),
                  "ref_pct": i.get("pct_change"), "ref_board": i.get("current_board"),
                  "watched": i["stock_code"] in watched, "live": facts.get(i["stock_code"])} for i in core_items]

    pool_rows = []
    if pools:
        core_set = {i["stock_code"] for i in core_items}
        for x in pools["sealed"]:
            pool_rows.append({**x, "in_core": x["code"] in core_set, "watched": x["code"] in watched})

    missing = [c for c, f in facts.items() if f["status"] == "missing"]
    stale = [c for c, f in facts.items() if f["status"] == "stale"]
    phase = sess["phase"]
    return {
        "session": {**{k: sess[k] for k in ("phase", "data_date", "ref_date", "target_date", "target_is_provisional")},
                    "phase_zh": PHASE_ZH[phase], "live": live, "market_time": _iso(market_dt),
                    "server_time": _iso(local_now), "poll_seconds": poll_seconds(phase, local_now),
                    "calendar_behind": bool(sess["calendar"].get("behind")),
                    "calendar_last": sess["calendar"].get("last"),
                    "note": _session_note(sess, market_dt)},
        "provisional_note": PROVISIONAL_NOTE,
        "quotes": {"requested": len(all_codes), "ok": sum(1 for f in facts.values() if f["status"] == "ok"),
                   "missing": missing, "stale": stale, "no_trade": [c for c, f in facts.items() if f["status"] == "no_trade"],
                   "error": q_err, "source": "实时行情（腾讯 / 新浪，东财兜底）"},
        "market": market,
        "plan": {"source": plan_source, "ref_date": ref, **plan_meta, "rows": plan_rows},
        "leaders": leaders,
        "watch": watch_rows,
        "cores": {"ref_date": ref, "frozen": (cores or {}).get("frozen"), "built_at": (cores or {}).get("built_at"),
                  "rows": core_rows, "source_labels": pc.SOURCE_LABEL,
                  "note": cores_err or (None if cores is not None else "没有上一交易日")},
        "limit_pool": pool_rows,
        "labels": {"events": LIVE_EVENT_LABEL, "responses": pc.RESPONSE_LABEL, "phases": PHASE_ZH},
    }


def _session_note(sess: dict, market_dt: Optional[datetime]) -> str:
    p, ref, d = sess["phase"], sess["ref_date"], sess["data_date"]
    if p == "unknown":
        return "实时行情这次没拿到，市场时间不知道；稍后自动再试"
    if p == "pre_open":
        return f"还没开盘：价格是 {d} 的收盘，执行的是 {ref} 锁定的预案"
    if p == "non_trading":
        return f"今天休市：价格是 {d} 的收盘，{ref} 锁定的预案要到 {sess['target_date']} 才执行"
    if p == "stale":
        return f"已过开盘时间，但行情时间还停在 {market_dt:%m-%d %H:%M}：今天可能休市，或者行情源没更新"
    if p == "closed":
        return f"已收盘。数字是收盘行情，但正式复盘以盘后同步为准（同步后到 {d} 那天的盘后研究里看）"
    return f"执行的是 {ref} 锁定的预案。{PROVISIONAL_NOTE}"
