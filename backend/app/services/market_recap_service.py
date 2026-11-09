"""
每日盘面复盘的**市场事实**：按指定交易日从原有各表现算，不存、不预测、不打分。

## 历史时点必须一致

复盘 09-07，指数、板块、高标都得是 09-07 的，不能有一块悄悄用了 11-08。所以每个模块
都只读「那一天」的数据（或那一天及以前的序列），并且各自带上
`{status, trade_date, source, note}`：

    ok        完整
    partial   部分缺失（缺的是哪部分写在 note 里）
    stale     拿到的不是这一天的（比如板块状态的基准日退到了前一天）
    missing   这一天没有数据
    error     这一块查挂了（其他模块照常）

没有的就是没有：不拿最新数据顶替历史，不拿 0 顶替缺失，未收盘的日子不出复盘。

## 复用，不重造

指数 / 宽度 / 涨跌停明细 / 股票日快照直接读表；晋级率用 limit_moves_analysis_service，
次日反馈用 market_effect_service，板块状态用 sector_mainline_service（as_of），生命周期
直接调 /leader-cycle 那个接口函数——同一套判定，不在这里写第二份。
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, time as dtime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from ..models.large_cap import LargeCapMarketDaily
from ..models.limit_up_detail import BrokenBoardDailyDetail, LimitUpDailyDetail
from ..models.market_index import IndexDailySnapshot, MarketBreadthDaily
from ..models.regulatory import RegulatoryStatusDaily
from ..models.sector import Sector, StockSectorRelation
from ..models.stock import Stock, StockDailySnapshot
from ..models.turnover_pool import TurnoverPoolDaily

OK, PARTIAL, STALE, MISSING, ERROR = "ok", "partial", "stale", "missing", "error"

RECAP_INDICES = [("000001", "上证指数"), ("399001", "深证成指"),
                 ("399006", "创业板指"), ("000688", "科创50")]
MA_WINDOWS = (5, 10, 20, 60)
FEEDBACK_COHORTS = ("limit_up", "first_board", "multi_board", "broken_board")
LIFECYCLE_CORE = ("STREAKING", "BROKEN", "REPAIRING", "CROSS_SUCCESS", "CROSS_WEAKENING", "CROSS_FAILED")
# 每组最多取几只进候选：连板、区间（N天M板）、生命周期、成交额。合起来去重约 10~20 只
CANDIDATE_QUOTA = {"board": 8, "interval": 5, "lifecycle": 6, "turnover": 5}


def _snap_pct20(x) -> Optional[float]:
    """
    股票日快照的 pct_change_20d 是非空列、默认 0.0：没算过的行也是 0。分不出「真 0%」和
    「没算」，就当不知道——宁可空着，也不让一个假 0 出现在关联股、候选股的表里。
    """
    v = getattr(x, "pct_change_20d", None) if x is not None else None
    return v if v else None


def _meta(status: str, d: Optional[date], source: str, note: Optional[str] = None) -> dict:
    return {"status": status, "trade_date": d, "source": source, "note": note}


def _isolated(db: Session, name: str, fn: Callable[[], dict]) -> dict:
    """
    一个模块查挂了不能拖垮整页：只把这一块标成 error。**要回滚**——Postgres 里一条查询
    出错，整个事务就作废了，不回滚的话后面几个模块全会跟着报「transaction is aborted」。
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return {"meta": _meta(ERROR, None, name, f"{type(e).__name__}: {str(e)[:120]}")}


def _ma(closes: Sequence[float], i: int, n: int) -> Optional[float]:
    if i < 0 or i + 1 < n:
        return None
    return sum(closes[i - n + 1:i + 1]) / n


def _direction(now: Optional[float], before: Optional[float]) -> Optional[str]:
    """均线方向：跟 5 个交易日前比。差不到万分之五算走平。"""
    if now is None or before is None or before == 0:
        return None
    chg = now / before - 1
    return "up" if chg > 0.0005 else "down" if chg < -0.0005 else "flat"


def _ret(closes: Sequence[float], n: int) -> Optional[float]:
    if len(closes) <= n or not closes[-1 - n]:
        return None
    return round((closes[-1] / closes[-1 - n] - 1) * 100, 2)


# ── 模块：指数 ──────────────────────────────────────────────────────────────

def index_module(db: Session, d: date) -> dict:
    items = []
    for code, name in RECAP_INDICES:
        rows = (db.query(IndexDailySnapshot.date, IndexDailySnapshot.close, IndexDailySnapshot.pct_change)
                .filter(IndexDailySnapshot.index_code == code, IndexDailySnapshot.date <= d,
                        IndexDailySnapshot.close.isnot(None))
                .order_by(IndexDailySnapshot.date.desc()).limit(80).all())[::-1]
        if not rows or rows[-1][0] != d:
            items.append({"code": code, "name": name, "status": MISSING})
            continue
        closes = [r[1] for r in rows]
        i = len(closes) - 1
        ma = {n: _ma(closes, i, n) for n in MA_WINDOWS}
        if all(ma[n] is not None for n in MA_WINDOWS):
            seq = [ma[n] for n in MA_WINDOWS]
            arrangement = ("多头" if all(a > b for a, b in zip(seq, seq[1:])) else
                           "空头" if all(a < b for a, b in zip(seq, seq[1:])) else "交织")
        else:
            arrangement = None
        items.append({
            "code": code, "name": name, "status": OK if ma[60] is not None else PARTIAL,
            "close": round(closes[-1], 2), "pct": rows[-1][2],
            **{f"ma{n}": round(ma[n], 2) if ma[n] is not None else None for n in MA_WINDOWS},
            "arrangement": arrangement,
            "ma20_dir": _direction(ma[20], _ma(closes, i - 5, 20)),
            "ma60_dir": _direction(ma[60], _ma(closes, i - 5, 60)),
            "above_ma20": closes[-1] >= ma[20] if ma[20] is not None else None,
            "above_ma60": closes[-1] >= ma[60] if ma[60] is not None else None,
            "r5": _ret(closes, 5), "r20": _ret(closes, 20), "bars": len(closes),
        })
    have = sum(1 for x in items if x["status"] != MISSING)
    status = OK if have == len(items) and all(x["status"] == OK for x in items) else \
        MISSING if have == 0 else PARTIAL
    return {"meta": _meta(status, d, "index_daily_snapshots",
                          None if status == OK else "部分指数这天没有日线或不足 60 根（MA60 算不出）"),
            "items": items}


# ── 模块：宽度 + 成交 ───────────────────────────────────────────────────────

def seal_rate_of(lu: int, broken: int) -> Tuple[Optional[int], Optional[float]]:
    """
    (炸板数, 封板率)。涨停明细或炸板明细任一天 0 行都当「没抓到」，两个都给 None：
    炸板池跟涨停池是两次请求，可能只挂一个（2026-11-09 炸板池超时、涨停 54 只照常写入），
    按 0 炸板算就是假的 100% 封板率。
    """
    if lu <= 0 or broken <= 0:
        return None, None
    return broken, round(lu / (lu + broken), 3)


def breadth_module(db: Session, d: date) -> dict:
    row = db.query(MarketBreadthDaily).filter(MarketBreadthDaily.date == d).first()
    lu = db.query(sqlfunc.count(LimitUpDailyDetail.id)).filter(LimitUpDailyDetail.trade_date == d).scalar() or 0
    broken = db.query(sqlfunc.count(BrokenBoardDailyDetail.id)).filter(BrokenBoardDailyDetail.trade_date == d).scalar() or 0
    notes = []
    # 明细一天 0 行 = 没抓到，不是一只都没有（体检也是这么判的）。炸板池跟涨停池是两次请求，
    # 可能只挂一个：2026-11-09 炸板池超时、涨停 54 只照常写入，按 0 炸板算封板率就是假的 100%
    broken_count, seal_rate = seal_rate_of(lu, broken)
    if lu == 0:
        notes.append("这天没有涨停明细，炸板数、封板率算不出")
    elif broken == 0:
        notes.append("这天没有炸板明细（拉取失败或还没补），炸板数、封板率算不出——不按 0 炸板算成 100%")
    counts = {k: getattr(row, k) if row else None
              for k in ("up_count", "down_count", "flat_count", "limit_up_count", "limit_down_count")}
    if row is None or counts["up_count"] is None:
        notes.append("这天没有涨跌家数统计")
    have = sum(v is not None for v in counts.values()) + (lu > 0) + (broken > 0)
    status = OK if have == 7 else MISSING if have == 0 else PARTIAL
    return {"meta": _meta(status, d, "market_breadth_daily + 涨停/炸板明细", "；".join(notes) or None),
            **counts, "broken_count": broken_count,
            "limit_up_detail_count": lu if lu > 0 else None, "seal_rate": seal_rate}


def amount_module(db: Session, d: date, cal: Sequence[date]) -> dict:
    """两市成交额。官方收盘值次日才公布：当天没有就用盘中最后一次快照，并明说。"""
    days = [x for x in cal if x <= d][-61:]
    rows = {r.date: r for r in db.query(MarketBreadthDaily.date, MarketBreadthDaily.deal_amount,
                                        MarketBreadthDaily.intraday_amount)
            .filter(MarketBreadthDaily.date.in_(days))} if days else {}
    cur = rows.get(d)
    amount, basis = (cur.deal_amount, "official") if cur and cur.deal_amount else \
        (cur.intraday_amount, "intraday_last") if cur and cur.intraday_amount else (None, None)
    prev_d = days[-2] if len(days) >= 2 else None
    prev = rows.get(prev_d).deal_amount if prev_d and rows.get(prev_d) else None
    hist = [rows[x].deal_amount for x in days[:-1] if x in rows and rows[x].deal_amount]
    avg60 = sum(hist[-60:]) / len(hist[-60:]) if hist else None
    note = None
    if basis == "intraday_last":
        note = "官方收盘成交额次日才公布，这里是盘中最后一次快照"
    elif amount is None:
        note = "这天没有两市成交额"
    status = OK if basis == "official" and prev and len(hist) >= 60 else \
        MISSING if amount is None else PARTIAL
    return {"meta": _meta(status, d, "market_breadth_daily", note),
            "amount": amount, "basis": basis, "prev_date": prev_d, "prev_amount": prev,
            "change_pct": round((amount / prev - 1) * 100, 2) if amount and prev else None,
            "avg60": avg60, "avg60_n": len(hist[-60:]),
            "vs_avg60_pct": round((amount / avg60 - 1) * 100, 2) if amount and avg60 else None}


# ── 模块：短线接力反馈 ─────────────────────────────────────────────────────

def feedback_module(db: Session, d: date, prev_d: Optional[date]) -> dict:
    from .limit_moves_analysis_service import compute_advance_ladder
    from .market_effect_service import compute_cohort_outcome
    if prev_d is None:
        return {"meta": _meta(MISSING, d, "market_effect + 晋级", "拿不到前一交易日"), "cohorts": [], "ladder": []}
    cohorts = [compute_cohort_outcome(db, prev_d, d, ct) for ct in FEEDBACK_COHORTS]
    ladder = compute_advance_ladder(db, d)
    rows = (db.query(StockDailySnapshot.board_count)
            .filter(StockDailySnapshot.date == d, StockDailySnapshot.is_limit_up.is_(True),
                    StockDailySnapshot.board_count >= 1).all())
    boards = [b for (b,) in rows if b]
    dist = {"1": sum(1 for b in boards if b == 1), "2": sum(1 for b in boards if b == 2),
            "3": sum(1 for b in boards if b == 3), "4+": sum(1 for b in boards if b >= 4)}
    have = sum(1 for c in cohorts if c["valid_count"])
    status = OK if have == len(cohorts) and boards else MISSING if not have and not boards else PARTIAL
    return {"meta": _meta(status, d, "market_effect_service + limit_moves_analysis_service",
                          None if status == OK else "部分群体没有有效样本，比例为空不是 0"),
            "prev_date": prev_d, "cohorts": cohorts, "ladder": ladder.get("rows", []),
            "ladder_notes": ladder.get("notes", []),
            "max_board": max(boards) if boards else None, "board_distribution": dist if boards else None}


# ── 模块：板块 ──────────────────────────────────────────────────────────────

def sector_module(db: Session, d: date) -> dict:
    from .sector_mainline_service import get_sector_mainline_state
    st = get_sector_mainline_state(db, as_of=d)
    state_date = st.get("state_date")
    items = [{"code": s["code"], "name": s["name"], "state": s["state"], "state_label": s["state_label"],
              "reason": s["state_reason"], "trail": s["trail"],
              "pct": s["facts"]["pct"], "r5": s["facts"]["r5"], "rs20": s["facts"]["rs20"],
              "lu": s["facts"]["lu"], "height": s["facts"]["height"], "lu_trend": s["facts"]["lu_trend"]}
             for s in st.get("sectors", []) if s["state"] not in ("NONE", "UNKNOWN")]
    if state_date is None:
        status, note = MISSING, "；".join(st.get("notes") or []) or "没有可用的板块状态"
    elif state_date != d.isoformat():
        status, note = STALE, f"板块状态基准日是 {state_date}，不是 {d}：" + "；".join(st.get("notes") or [])
    else:
        status, note = OK, None
    return {"meta": _meta(status, d, "sector_mainline_service", note),
            "state_date": state_date, "counts": st.get("counts"), "items": items}


# ── 模块：核心股票候选 ─────────────────────────────────────────────────────

def _lifecycle_items(db: Session, d: date) -> Dict[str, Any]:
    from ..routers.leader_cycle import get_leader_cycle
    resp = get_leader_cycle(trade_date=d, db=db)
    if resp.trade_date != d:
        return {}
    return {it.code: it for it in [*resp.running, *resp.broken]}


def candidate_module(db: Session, d: date, watched: Iterable[str] = ()) -> dict:
    snaps = {s.code: (s, snap) for s, snap in
             db.query(Stock, StockDailySnapshot).join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
             .filter(StockDailySnapshot.date == d)}
    details = {r.stock_code: r for r in db.query(LimitUpDailyDetail).filter(LimitUpDailyDetail.trade_date == d)}
    turnover = {r.stock_code: r for r in db.query(TurnoverPoolDaily).filter(TurnoverPoolDaily.date == d)}
    lifecycle = _lifecycle_items(db, d)
    if not snaps and not details:
        return {"meta": _meta(MISSING, d, "stock_daily_snapshots", "这天没有股票日快照"), "items": []}

    picks: Dict[str, set] = {}
    board = sorted(((c, sn.board_count) for c, (_s, sn) in snaps.items()
                    if sn.is_limit_up and (sn.board_count or 0) >= 2), key=lambda x: -x[1])
    for c, _b in board[:CANDIDATE_QUOTA["board"]]:
        picks.setdefault(c, set()).add("board")
    # N天M板：不是连续连板的那种（N > M），跟连板分开显示
    interval = sorted(((c, r.limit_stat_count) for c, r in details.items()
                       if r.limit_stat_count and r.limit_stat_days and r.limit_stat_count >= 3
                       and r.limit_stat_days > r.limit_stat_count), key=lambda x: -x[1])
    for c, _m in interval[:CANDIDATE_QUOTA["interval"]]:
        picks.setdefault(c, set()).add("interval")
    life_order = {s: i for i, s in enumerate(("REPAIRING", "CROSS_SUCCESS", "STREAKING", "BROKEN",
                                              "CROSS_WEAKENING", "CROSS_FAILED"))}
    life = sorted((c for c, it in lifecycle.items() if it.lifecycle_state in LIFECYCLE_CORE),
                  key=lambda c: (life_order.get(lifecycle[c].lifecycle_state, 9),
                                 -(lifecycle[c].peak_board_count or 0)))
    for c in life[:CANDIDATE_QUOTA["lifecycle"]]:
        picks.setdefault(c, set()).add("lifecycle")
    for r in sorted(turnover.values(), key=lambda r: r.rank)[:CANDIDATE_QUOTA["turnover"]]:
        picks.setdefault(r.stock_code, set()).add("turnover")

    codes = set(picks) | set(watched)
    lc_rows = {r.stock_code: r for r in db.query(LargeCapMarketDaily.stock_code, LargeCapMarketDaily.industry,
                                                 LargeCapMarketDaily.pct_change_20d)
               .filter(LargeCapMarketDaily.trade_date == d, LargeCapMarketDaily.stock_code.in_(codes))} if codes else {}
    reg = {r.security_code: r for r in db.query(RegulatoryStatusDaily)
           .filter(RegulatoryStatusDaily.date == d, RegulatoryStatusDaily.security_code.in_(codes))} if codes else {}

    items = []
    for c in codes:
        s, sn = snaps.get(c, (None, None))
        det, tv, lc, lcd = details.get(c), turnover.get(c), lifecycle.get(c), lc_rows.get(c)
        # 板块要当天的口径：涨停明细里的东财行业 → 当天全市场快照里的行业 → 最后才用
        # 股票表上的「当前主板块」，并标出来是当前口径
        if det is not None and det.em_industry:
            sector, sector_basis = det.em_industry, "当日涨停明细"
        elif lcd is not None and lcd.industry:
            sector, sector_basis = lcd.industry, "当日行情快照"
        elif s is not None and s.primary_sector_name:
            sector, sector_basis = s.primary_sector_name, "当前主板块（非当日口径）"
        else:
            sector, sector_basis = None, None
        r = reg.get(c)
        items.append({
            "code": c, "name": (s.name if s else None) or (det.stock_name if det else None)
            or (tv.stock_name if tv else None) or (lc.name if lc else None),
            "groups": sorted(picks.get(c, set())), "watched": c in set(watched),
            "pct": sn.pct_change if sn else (det.pct_change if det else (tv.pct_change if tv else None)),
            "is_limit_up": (True if det is not None else (sn.is_limit_up if sn else None)),
            "board_count": (sn.board_count if sn else None) or (det.board_count if det else None),
            "board_count_60d": sn.board_count_60d if sn else None,
            "limit_stat": (f"{det.limit_stat_days}天{det.limit_stat_count}板"
                           if det is not None and det.limit_stat_days and det.limit_stat_count else None),
            "limit_up_days_10d": sn.limit_up_days_10d if sn else None,
            "limit_up_days_20d": sn.limit_up_days_20d if sn else None,
            "pct_20d": (lcd.pct_change_20d if lcd is not None and lcd.pct_change_20d is not None
                        else _snap_pct20(sn)),
            "first_limit_time": det.first_limit_time.strftime("%H:%M:%S") if det and det.first_limit_time else None,
            "limit_reason": det.limit_reason if det else None,
            "lifecycle_state": lc.lifecycle_state if lc else None,
            "peak_board_count": lc.peak_board_count if lc else None,
            "turnover_rank": tv.rank if tv else None,
            "amount": (sn.amount if sn and sn.amount else (tv.amount if tv else None)),
            "sector": sector, "sector_basis": sector_basis,
            "regulatory": ({"status": r.status, "reason": r.reason_type, "until": r.predict_end} if r else None),
            "has_snapshot": sn is not None,
        })
    items.sort(key=lambda x: (-(x["board_count"] or 0) if "board" in x["groups"] else 0,
                              0 if x["groups"] else 1, -(x["pct"] or -99)))
    notes = []
    if not lifecycle:
        notes.append("这天没有生命周期快照，生命周期组为空")
    if not turnover:
        notes.append("这天没有成交额前列名单")
    return {"meta": _meta(PARTIAL if notes else OK, d, "股票日快照 + 涨停明细 + 生命周期 + 成交额前列",
                          "；".join(notes) or None),
            "items": items, "quota": CANDIDATE_QUOTA}


# ── 汇总 ────────────────────────────────────────────────────────────────────

def recap_dates(db: Session, limit: int = 60) -> List[date]:
    """能做复盘的交易日：交易日历里、已收盘、并且有股票日快照。"""
    from .eastmoney_fetcher import SH_TZ, bar_is_settled
    from .trading_calendar import get_trading_days
    now = datetime.now(SH_TZ).replace(tzinfo=None)
    cal = get_trading_days(db) or []
    have = {d for (d,) in db.query(StockDailySnapshot.date).distinct()
            .order_by(StockDailySnapshot.date.desc()).limit(limit + 10)}
    return [d for d in reversed(cal) if d in have and bar_is_settled(d, now)][:limit]


def build_context(db: Session, d: date, watched: Iterable[str] = (),
                  now: Optional[datetime] = None) -> dict:
    from .eastmoney_fetcher import SH_TZ, bar_is_settled
    from .trading_calendar import get_trading_days
    now = now or datetime.now(SH_TZ).replace(tzinfo=None)
    cal = get_trading_days(db, need_through=d) or []
    if d not in set(cal):
        return {"date": d, "status": "not_trading_day", "message": f"{d} 不在交易日历里"}
    if not bar_is_settled(d, now):
        return {"date": d, "status": "not_settled",
                "message": f"{d} 还没收盘：复盘只用收盘后的数据，不拿盘中价当结论"}
    i = cal.index(d)
    prev_d = cal[i - 1] if i > 0 else None
    watched = list(watched)
    modules = {
        "indices": _isolated(db, "indices", lambda: index_module(db, d)),
        "breadth": _isolated(db, "breadth", lambda: breadth_module(db, d)),
        "amount": _isolated(db, "amount", lambda: amount_module(db, d, cal)),
        "feedback": _isolated(db, "feedback", lambda: feedback_module(db, d, prev_d)),
        "sectors": _isolated(db, "sectors", lambda: sector_module(db, d)),
        "candidates": _isolated(db, "candidates", lambda: candidate_module(db, d, watched)),
    }
    return {"date": d, "prev_date": prev_d, "status": "ok", **modules,
            "data_status": {k: v["meta"] for k, v in modules.items()}}


# ── 个股当天事实（关联股、预案、验证共用）──────────────────────────────────

def stock_facts(db: Session, codes: Iterable[str], d: date) -> Dict[str, dict]:
    """
    任意一批股票在 d 这天的事实。来源按覆盖面拼：全市场快照（11-06 起全市场）→ 股票日快照
    （只有进过候选池的）→ 涨停明细（当天涨停的全有，带首封时间）。哪个都没有就是 missing。
    """
    codes = list(dict.fromkeys(c for c in codes if c))
    if not codes:
        return {}
    lc = {r.stock_code: r for r in db.query(
        LargeCapMarketDaily.stock_code, LargeCapMarketDaily.stock_name, LargeCapMarketDaily.pct_change,
        LargeCapMarketDaily.pct_change_5d, LargeCapMarketDaily.pct_change_20d, LargeCapMarketDaily.amount,
        LargeCapMarketDaily.price).filter(LargeCapMarketDaily.trade_date == d,
                                          LargeCapMarketDaily.stock_code.in_(codes))}
    sn = {s.code: (s, x) for s, x in db.query(Stock, StockDailySnapshot)
          .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
          .filter(StockDailySnapshot.date == d, Stock.code.in_(codes))}
    det = {r.stock_code: r for r in db.query(LimitUpDailyDetail)
           .filter(LimitUpDailyDetail.trade_date == d, LimitUpDailyDetail.stock_code.in_(codes))}
    lu_day_known = db.query(LimitUpDailyDetail.id).filter(LimitUpDailyDetail.trade_date == d).first() is not None
    in_db = {c for (c,) in db.query(Stock.code).filter(Stock.code.in_(codes))}
    out = {}
    for c in codes:
        a, (s, x), t = lc.get(c), sn.get(c, (None, None)), det.get(c)
        sources = [n for n, v in (("全市场快照", a), ("股票日快照", x), ("涨停明细", t)) if v is not None]
        if t is not None:
            limit_up = True
        elif x is not None:
            limit_up = bool(x.is_limit_up)
        elif lu_day_known and a is not None:
            limit_up = False        # 当天涨停明细是全的，又不在里面
        else:
            limit_up = None
        out[c] = {
            "code": c, "name": (a.stock_name if a else None) or (s.name if s else None) or (t.stock_name if t else None),
            "status": OK if sources else MISSING, "sources": sources,
            "pct": a.pct_change if a and a.pct_change is not None else (x.pct_change if x else (t.pct_change if t else None)),
            "pct_5d": a.pct_change_5d if a else None,
            "pct_20d": a.pct_change_20d if a and a.pct_change_20d is not None else _snap_pct20(x),
            "close": a.price if a and a.price else (x.close_price if x else (t.price if t else None)),
            "amount_yi": a.amount if a and a.amount is not None else
            (round(x.amount / 1e8, 4) if x and x.amount else (round(t.amount / 1e8, 4) if t and t.amount else None)),
            "is_limit_up": limit_up,
            "board_count": (t.board_count if t and t.board_count else (x.board_count if x else None)),
            "first_limit_time": t.first_limit_time.strftime("%H:%M:%S") if t and t.first_limit_time else None,
            "has_detail": c in in_db,
        }
    return out


def first_limit_time(db: Session, code: str, d: date) -> Optional[dtime]:
    r = (db.query(LimitUpDailyDetail.first_limit_time)
         .filter(LimitUpDailyDetail.trade_date == d, LimitUpDailyDetail.stock_code == code).first())
    return r[0] if r else None


# ── 关联建议（只是建议，人确认了才进关联表）──────────────────────────────────

def _strip_name(name: str) -> str:
    n = name.replace("*", "").replace(" ", "")
    for p in ("ST", "N", "C"):
        if n.startswith(p) and len(n) > 3:
            n = n[len(p):]
    return n.split("-")[0]


def name_suggestions(db: Session, leader_code: str, leader_name: str, d: date, limit: int = 30) -> List[dict]:
    """
    名称联想：领头股名字里每两个相邻字，在全市场名字里找包含它的。**客观字符串匹配**，
    依据只能是 C 类「名称包含某两个字」，证明不了业务关联，更证明不了资金传导。
    """
    base = _strip_name(leader_name or "")
    grams = list(dict.fromkeys(base[i:i + 2] for i in range(len(base) - 1)))
    if not grams:
        return []
    names = dict(db.query(LargeCapMarketDaily.stock_code, LargeCapMarketDaily.stock_name)
                 .filter(LargeCapMarketDaily.trade_date == (
                     db.query(sqlfunc.max(LargeCapMarketDaily.trade_date))
                     .filter(LargeCapMarketDaily.trade_date <= d).scalar_subquery())).all())
    scope = "全市场"
    if not names:
        names = dict(db.query(Stock.code, Stock.name).all())
        scope = "股票库"      # 那天还没有全市场快照，只能在本系统股票表里数
    # 每两个字在全市场名字里出现几次：「东方」「科技」这种常见词一配就是几十只，噪声大，
    # 数字摆出来让人自己判断，并且越稀有的排越前
    gram_n = {g: sum(1 for c, nm in names.items() if c != leader_code and nm and g in nm) for g in grams}
    out = []
    for code, nm in names.items():
        if code == leader_code or not nm:
            continue
        hit = [g for g in grams if g in nm]
        if hit:
            rare = min(hit, key=lambda g: gram_n[g])
            out.append({"related_code": code, "related_name": nm, "attribute_type": "name",
                        "attribute_value": rare, "source": "name_rule", "hits": len(hit),
                        "gram_matches": {g: gram_n[g] for g in hit},
                        "evidence": "名称都含" + "".join(f"「{g}」" for g in hit)
                                    + f"（{scope}含「{rare}」的 {gram_n[rare]} 只）"})
    out.sort(key=lambda x: (-x["hits"], min(x["gram_matches"].values()), x["related_code"]))
    return out[:limit]


def concept_suggestions(db: Session, leader_code: str, d: date, limit: int = 30) -> List[dict]:
    """
    共享概念：领头股的概念（sectors 表里的概念板块关联）× 当天涨停的股票。只是「同属某概念
    且当天也涨停了」这个事实，B 类；有没有资金联动要另外看响应记录。
    """
    lead = db.query(Stock.id).filter(Stock.code == leader_code).scalar()
    if lead is None:
        return []
    concepts = {sid: name for sid, name in db.query(Sector.id, Sector.name)
                .join(StockSectorRelation, StockSectorRelation.sector_id == Sector.id)
                .filter(StockSectorRelation.stock_id == lead, Sector.sector_type == "concept")}
    if not concepts:
        return []
    today_lu = {r.stock_id: r for r in db.query(LimitUpDailyDetail)
                .filter(LimitUpDailyDetail.trade_date == d, LimitUpDailyDetail.stock_code != leader_code)}
    if not today_lu:
        return []
    shared: Dict[int, List[str]] = {}
    for sid, sec_id in db.query(StockSectorRelation.stock_id, StockSectorRelation.sector_id) \
            .filter(StockSectorRelation.stock_id.in_(list(today_lu)),
                    StockSectorRelation.sector_id.in_(list(concepts))):
        shared.setdefault(sid, []).append(concepts[sec_id])
    out = []
    for sid, names in shared.items():
        r = today_lu[sid]
        out.append({"related_code": r.stock_code, "related_name": r.stock_name, "attribute_type": "concept",
                    "attribute_value": names[0], "source": "sector_relation", "hits": len(names),
                    "evidence": f"同属概念：{'、'.join(sorted(names))}；{d} 也涨停"})
    out.sort(key=lambda x: (-x["hits"], x["related_code"]))
    return out[:limit]


# ── 板块关联：高标所在的每个板块里，当天的活跃股 ─────────────────────────────

ACTIVE_LABEL = {"strong": "强势池", "limit_up": "涨停", "limit_down": "跌停"}


def sector_peers(db: Session, leader_code: str, d: date) -> dict:
    """
    高标所在的每个板块（跟活跃股池里显示的板块标签同一份：Sector Config 里关注的行业 / 概念），
    各列出 d 这天的活跃股。一只票同时在高标的几个板块里，就在每个板块下都出现。

    「活跃」按 d 当天：强势池成员（那天的生命周期快照）、那天涨停（涨停明细，全的）、那天跌停
    （股票日快照）。**板块归属是当前口径**——板块关联表不存历史，页面上写明。
    """
    from ..models.leader_cycle import LeaderCycleSnapshot
    lead = db.query(Stock).filter(Stock.code == leader_code).first()
    if lead is None:
        return {"status": "missing", "note": "股票库里没有这只，查不到它的板块归属", "groups": []}
    secs = (db.query(Sector.id, Sector.code, Sector.name, Sector.sector_type)
            .join(StockSectorRelation, StockSectorRelation.sector_id == Sector.id)
            .filter(StockSectorRelation.stock_id == lead.id, Sector.is_watched.is_(True)).all())
    if not secs:
        return {"status": "missing", "groups": [],
                "note": "这只没有板块归属（板块关联靠 F10 同步，只关联关注板块；可以在下面手动录入关联）"}

    reasons: Dict[str, List[str]] = {}
    for (c,) in db.query(LeaderCycleSnapshot.stock_code).filter(LeaderCycleSnapshot.date == d):
        reasons.setdefault(c, []).append("strong")
    lu_known = False
    for (c,) in db.query(LimitUpDailyDetail.stock_code).filter(LimitUpDailyDetail.trade_date == d):
        lu_known = True
        reasons.setdefault(c, []).append("limit_up")
    for (c,) in (db.query(Stock.code).join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
                 .filter(StockDailySnapshot.date == d, StockDailySnapshot.is_limit_down.is_(True))):
        reasons.setdefault(c, []).append("limit_down")
    reasons.pop(leader_code, None)

    sec_ids = [s.id for s in secs]
    members: Dict[int, List[str]] = {}
    if reasons:
        for sid, c in (db.query(StockSectorRelation.sector_id, Stock.code)
                       .join(Stock, Stock.id == StockSectorRelation.stock_id)
                       .filter(StockSectorRelation.sector_id.in_(sec_ids), Stock.code.in_(list(reasons)))):
            members.setdefault(sid, []).append(c)
    codes = sorted({c for v in members.values() for c in v})
    facts = stock_facts(db, codes + [leader_code], d)
    in_secs: Dict[str, List[str]] = {}
    name_of = {s.id: s.name for s in secs}
    for sid, cs in members.items():
        for c in cs:
            in_secs.setdefault(c, []).append(name_of[sid])

    def key(c: str):
        f = facts.get(c) or {}
        return (not f.get("is_limit_up"), -(f.get("board_count") or 0), -(f.get("pct") or -99))

    order = sorted(secs, key=lambda s: (s.id != lead.primary_sector_id, s.sector_type != "industry", s.name))
    groups = [{"sector": {"code": s.code, "name": s.name, "type": s.sector_type,
                          "primary": s.id == lead.primary_sector_id},
               "stocks": [{"code": c, "active": reasons[c], "also_in": [n for n in in_secs[c] if n != s.name],
                           "facts": facts.get(c)} for c in sorted(members.get(s.id, []), key=key)]}
              for s in order]
    with_rel = {c for (c,) in db.query(Stock.code).join(StockSectorRelation, StockSectorRelation.stock_id == Stock.id)
                .filter(Stock.code.in_(list(reasons))).distinct()} if reasons else set()
    notes = ["板块归属是当前口径（板块关联表不存历史）；活跃股按当天：强势池、涨停、跌停"]
    if not lu_known:
        notes.append(f"{d} 没有涨停明细，涨停的活跃股可能不全")
    if reasons and len(with_rel) < len(reasons):
        notes.append(f"这天 {len(reasons)} 只活跃股里 {len(reasons) - len(with_rel)} 只没有板块归属，不会出现在任何板块下")
    return {"status": "ok", "leader": facts.get(leader_code), "groups": groups,
            "active_total": len(reasons), "peers_total": len(codes), "notes": notes}
