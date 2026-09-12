"""
日内上下文（买入检查用，2026-09-11 新增）：as_of 那一刻，这只票（或指数）走到哪了。

## 唯一的纪律：as_of 之后的任何成交都不能进来

分钟 bar 按**结束**时刻标时间，过滤一律 `bar.dt <= as_of`。as_of 落在一根 bar 中间，
这一根整根丢掉——它里面混着 as_of 之后的成交。5 分钟 bar 因此最多落后 5 分钟，
质量标 APPROX，不假装精确。

## 数据从哪来（按优先级）

LIVE（as_of = 此刻）
  · 实时行情（fetch_stock_quotes_batch）：现价/今开/最高/最低/昨收/量额 → EXACT
  · 当日分钟：只用来回放结构（H1/L1），行情再补一个"此刻"的点
HISTORICAL（as_of 在过去）
  · 新浪 1 分钟（有 OHLC）：那天完整（第一根是 09:31）才用 → EXACT
  · 腾讯 5 日分钟（只有每分钟的价）：as_of 价 EXACT，最高最低只能按分钟价 → APPROX
  · 新浪 5 分钟（约 22 个交易日）→ APPROX
  · 再往前没有分钟数据 → UNKNOWN。**不拿日 K 收盘去填**

昨收优先用腾讯给的 prec（官方昨收，除权日也对），其次同一序列里前一天最后一根。

## 结构回放不另写算法

逐根把价格喂给弱转强雷达的 `compute_structural_transition`——它就是
「收复修复关键位 → H1 → 有效回踩 L1 → 再突破 H1」的纯函数状态机，雷达用的是
同一套判定。修复关键位 = max(昨收, 截至当根的累计 VWAP)（compute_repair_anchor）。
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time
from time import perf_counter          # 模块里的 time 是 datetime.time
from typing import Dict, List, Optional, Sequence, Tuple

from .eastmoney_fetcher import (
    SH_TZ, MinuteBar, fetch_minute_bars_sina, fetch_minute_days_tencent, fetch_stock_quotes_batch,
)
from .w2s_state_machine import (
    STRUCT_CONFIRMED, STRUCT_FAILED, STRUCT_PULLBACK, STRUCT_READY, STRUCT_REPAIRING, STRUCT_WATCH,
    compute_repair_anchor, compute_structural_transition,
)

EXACT, APPROX, STALE, UNKNOWN = "EXACT", "APPROX", "STALE", "UNKNOWN"
_FIRST_BAR_1M = time(9, 31)      # 新浪 1 分钟：一天完整的话第一根是 09:31
_FIRST_BAR_5M = time(9, 35)      # 新浪 5 分钟：第一根是 09:35

# 结构机状态 → 买入检查的四档。雷达的 READY 是"竞价超预期"，这里不用竞价路径，
# 等同 WATCH
STRUCTURE_STATUS = {
    STRUCT_CONFIRMED: "CONFIRMED",
    STRUCT_REPAIRING: "PARTIAL",
    STRUCT_PULLBACK: "PARTIAL",
    STRUCT_WATCH: "NOT_CONFIRMED",
    STRUCT_READY: "NOT_CONFIRMED",
    STRUCT_FAILED: "NOT_CONFIRMED",
}


logger = logging.getLogger("tradeflux.pretrade")


@dataclass
class IntradayContext:
    code: str
    as_of: datetime
    trade_date: date
    prev_close: Optional[float] = None
    open: Optional[float] = None
    price: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    vwap: Optional[float] = None
    volume: Optional[float] = None     # 股
    amount: Optional[float] = None     # 元
    bars: List[MinuteBar] = field(default_factory=list)   # 只含 dt <= as_of 的
    resolution: Optional[str] = None   # "1m" | "5m"
    source: str = ""
    quality: str = UNKNOWN
    observed_at: Optional[datetime] = None
    notes: List[str] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)   # 各路取数耗时（秒），给实战日志用

    @property
    def pct(self) -> Optional[float]:
        if self.price and self.prev_close:
            return round((self.price / self.prev_close - 1) * 100, 2)
        return None

    def summary(self) -> dict:
        return {
            "code": self.code, "trade_date": self.trade_date.isoformat(),
            "prev_close": self.prev_close, "open": self.open, "price": self.price,
            "pct": self.pct, "high": self.high, "low": self.low, "vwap": self.vwap,
            "volume": self.volume, "amount": self.amount, "bar_count": len(self.bars),
            "resolution": self.resolution, "source": self.source, "quality": self.quality,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "notes": list(self.notes),
        }


# 新浪分钟 K 同一个请求实测 0.5s～27s（2026-09-11），腾讯 0.5s～1.8s：新浪只作补充，而且限时
SINA_1M_BUDGET_S = 5.0
SINA_5M_BUDGET_S = 8.0          # 超出腾讯 5 日范围时它是唯一来源，多等一会
_TENCENT_HL_NOTE = "最高/最低按每分钟的价算，不含分钟内的冲高回落"


def _within(seconds: float, fn, *args, **kwargs):
    """最多等 seconds 秒，超时抛 TimeoutError 让调用方降级；后台那个请求自己跑完（httpx 有自己的超时）。"""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(fn, *args, **kwargs).result(timeout=seconds)
    finally:
        pool.shutdown(wait=False)


def _fail_note(src: str, e: Exception, budget: float) -> str:
    if isinstance(e, TimeoutError):
        return f"{src} {budget:g} 秒没回，不等了"
    return f"{src}取数失败（{type(e).__name__}）"


def _naive_sh(dt: Optional[datetime]) -> Optional[datetime]:
    """行情时间戳带时区（上海），分钟 bar 是不带时区的上海时间：统一成后者再比较。"""
    if dt is not None and dt.tzinfo is not None:
        return dt.astimezone(SH_TZ).replace(tzinfo=None)
    return dt


def _sane_vwap(v: Optional[float], ref: Optional[float]) -> Optional[float]:
    """量的单位一错（手/股差 100 倍）VWAP 就离谱。离昨收超过 ±50% 当作算错了，不用。"""
    if v is None or not ref:
        return v
    return v if 0.5 * ref <= v <= 1.5 * ref else None


def _vwap(bars: Sequence[MinuteBar]) -> Optional[float]:
    vol = amt = 0.0
    for b in bars:
        if b.volume and b.amount:
            vol += b.volume
            amt += b.amount
    return amt / vol if vol > 0 else None


def _fill_from_bars(ctx: IntradayContext, upto: List[MinuteBar], *, ohlc: bool) -> None:
    ctx.bars = upto
    if not upto:
        return
    first, last = upto[0], upto[-1]
    ctx.open = first.open if (ohlc and first.open) else first.close
    ctx.price = last.close
    ctx.high = max((b.high if (ohlc and b.high) else b.close) for b in upto)
    ctx.low = min((b.low if (ohlc and b.low) else b.close) for b in upto)
    ctx.volume = sum(b.volume or 0 for b in upto) or None
    ctx.amount = sum(b.amount or 0 for b in upto) or None
    ctx.vwap = _sane_vwap(_vwap(upto), ctx.prev_close)
    ctx.observed_at = last.dt


def get_intraday_context(code: str, market: int, as_of: datetime, *, live: bool,
                         is_index: bool = False, detail: bool = True) -> IntradayContext:
    """
    as_of 那一刻的日内事实。**任何一路失败都不抛**：降级、写进 notes、质量如实标。

    detail=False：调用方只用 as_of 那一刻的价（指数、昨日高标），腾讯分钟价就够，不拉新浪 1 分钟。
    """
    d = as_of.date()
    ctx = IntradayContext(code=code, as_of=as_of, trade_date=d)

    # 新浪 1 分钟只在「历史复盘、要精确的开高低」时用：跟腾讯同时发，最多等 SINA_1M_BUDGET_S 秒。
    # 实时模式的开高低、量额来自行情；只要价的票（detail=False）腾讯分钟价就够
    pool = ThreadPoolExecutor(max_workers=1) if (detail and not live) else None
    t_sina = perf_counter()
    f_sina1 = pool.submit(fetch_minute_bars_sina, code, market, scale=1) if pool else None

    tencent: Dict[date, Tuple[Optional[float], List[MinuteBar]]] = {}
    t = perf_counter()
    try:
        tencent = fetch_minute_days_tencent(code, market)
    except Exception as e:  # noqa: BLE001
        ctx.notes.append(f"腾讯分钟取数失败（{type(e).__name__}）")
    ctx.timings["腾讯"] = round(perf_counter() - t, 2)
    if d in tencent and tencent[d][0]:
        ctx.prev_close = tencent[d][0]

    sina1: List[MinuteBar] = []
    waited = False
    try:
        if f_sina1 is not None and (d in tencent or not tencent):
            waited = True
            sina1 = f_sina1.result(timeout=SINA_1M_BUDGET_S)
        elif not tencent:           # 腾讯整个挂了：新浪是剩下唯一的分钟来源
            waited, t_sina = True, perf_counter()
            sina1 = _within(SINA_1M_BUDGET_S, fetch_minute_bars_sina, code, market, scale=1)
    except Exception as e:  # noqa: BLE001
        ctx.notes.append(_fail_note("新浪 1 分钟", e, SINA_1M_BUDGET_S))
    if waited:
        ctx.timings["新浪1m"] = round(perf_counter() - t_sina, 2)
    if pool:
        pool.shutdown(wait=False)
    day1 = [b for b in sina1 if b.dt.date() == d]

    if day1 and day1[0].dt.time() <= _FIRST_BAR_1M:
        if ctx.prev_close is None:
            prev = [b for b in sina1 if b.dt.date() < d]
            ctx.prev_close = prev[-1].close if prev else None
        _fill_from_bars(ctx, [b for b in day1 if b.dt <= as_of], ohlc=True)
        ctx.resolution, ctx.source = "1m", "新浪 1 分钟K"
        ctx.quality = EXACT if ctx.bars else UNKNOWN
    elif d in tencent:
        _fill_from_bars(ctx, [b for b in tencent[d][1] if b.dt <= as_of], ohlc=False)
        ctx.resolution, ctx.source = "1m", "腾讯 5 日分钟"
        if detail:
            ctx.quality = APPROX if ctx.bars else UNKNOWN
            ctx.notes.append(_TENCENT_HL_NOTE)
        else:           # 只用 as_of 那一分钟的价：分钟收盘价本身是准的，开高低用不到
            ctx.quality = EXACT if ctx.bars else UNKNOWN
    else:
        sina5: List[MinuteBar] = []
        # as_of 这天比腾讯最新的一天还新（没开盘、不是交易日）：新浪 5 分钟也不会有，别白等
        not_yet = bool(tencent) and d > max(tencent)
        if not not_yet:
            t = perf_counter()
            try:
                sina5 = _within(SINA_5M_BUDGET_S, fetch_minute_bars_sina, code, market, scale=5)
            except Exception as e:  # noqa: BLE001
                ctx.notes.append(_fail_note("新浪 5 分钟", e, SINA_5M_BUDGET_S))
            ctx.timings["新浪5m"] = round(perf_counter() - t, 2)
        day5 = [b for b in sina5 if b.dt.date() == d]
        if day5 and day5[0].dt.time() <= _FIRST_BAR_5M:
            if ctx.prev_close is None:
                prev = [b for b in sina5 if b.dt.date() < d]
                ctx.prev_close = prev[-1].close if prev else None
            _fill_from_bars(ctx, [b for b in day5 if b.dt <= as_of], ohlc=True)
            ctx.resolution, ctx.source = "5m", "新浪 5 分钟K"
            ctx.quality = APPROX if ctx.bars else UNKNOWN
            ctx.notes.append("5 分钟 bar：as_of 所在那根还没走完，整根不用，最多落后 5 分钟")
        elif not_yet:
            ctx.notes.append(f"{d} 还没有分钟数据（没开盘或不是交易日）")
        else:
            ctx.notes.append(f"{d} 超出分钟数据的回溯范围（新浪 5 分钟约 22 个交易日），"
                             f"日内事实无法还原——不拿收盘数据去填")

    if live:
        t = perf_counter()
        try:
            q = fetch_stock_quotes_batch([(code, market)]).get(code)
        except Exception as e:  # noqa: BLE001
            q = None
            ctx.notes.append(f"实时行情失败（{type(e).__name__}）")
        ctx.timings["行情"] = round(perf_counter() - t, 2)
        if q and q.price:
            if q.trade_date == d:
                ctx.price, ctx.open = q.price, q.open or ctx.open
                ctx.high, ctx.low = q.high or ctx.high, q.low or ctx.low
                if q.high and q.low:        # 换成行情给的真值后，「按分钟价算」的说明不再成立
                    ctx.notes = [n for n in ctx.notes if n != _TENCENT_HL_NOTE]
                ctx.prev_close = q.prev_close or ctx.prev_close
                if q.volume and q.amount:
                    ctx.volume, ctx.amount = q.volume, q.amount
                    ctx.vwap = _sane_vwap(q.amount / q.volume, ctx.prev_close)
                ctx.observed_at = _naive_sh(q.trade_dt) or as_of
                ctx.quality = EXACT
                ctx.source = (ctx.source + " + 实时行情") if ctx.source else "实时行情"
                # 结构回放要"此刻"这个点：分钟 bar 最多到上一分钟
                if not ctx.bars or ctx.observed_at > ctx.bars[-1].dt:
                    ctx.bars = list(ctx.bars) + [MinuteBar(dt=ctx.observed_at, close=q.price)]
            else:
                ctx.quality = STALE if ctx.quality != UNKNOWN else UNKNOWN
                ctx.notes.append(f"实时行情的日期是 {q.trade_date}，不是今天（非交易时段？）")

    if is_index:
        ctx.vwap = None        # 指数的"均价"没有意义
    # 停牌 / 整段没成交：腾讯照样给一排「价格 = 昨收、量为空」的分钟行。
    # 当成 +0.00% 会把停牌票算进高标反馈，所以按拿不到处理
    if (not is_index and ctx.bars and ctx.volume is None and ctx.prev_close
            and ctx.high == ctx.low and abs((ctx.price or 0) - ctx.prev_close) < 1e-6):
        ctx.notes.append("as_of 前没有任何成交、价格一直停在昨收：大概率停牌")
        ctx.quality, ctx.price, ctx.vwap, ctx.bars = UNKNOWN, None, None, []
    return ctx


def get_intraday_contexts(items: Sequence[Tuple[str, int, bool]], as_of: datetime, *,
                          live: bool, detail: bool = True, max_workers: int = 6) -> Dict[str, IntradayContext]:
    """一批 (code, market, is_index) 并发取，结果按 code。单只失败只影响它自己。"""
    out: Dict[str, IntradayContext] = {}
    if not items:
        return out
    with ThreadPoolExecutor(max_workers=min(max_workers, len(items))) as ex:
        futs = {ex.submit(get_intraday_context, c, m, as_of, live=live, is_index=ix, detail=detail): c
                for c, m, ix in items}
        for f, c in futs.items():
            try:
                out[c] = f.result()
            except Exception as e:  # noqa: BLE001
                logger.warning("分钟取数异常 %s: %r", c, e)
                ctx = IntradayContext(code=c, as_of=as_of, trade_date=as_of.date())
                ctx.notes.append(f"取数异常（{type(e).__name__}）")
                out[c] = ctx
    return out


def get_quote_contexts(items: Sequence[Tuple[str, int, bool]], as_of: datetime) -> Dict[str, IntradayContext]:
    """
    实时模式下只要涨跌幅的票（指数、昨日高标）：**一次批量行情**全拿到，不拉分钟 K。
    之前每只都拉腾讯 + 新浪分钟，6 只就是十几个请求，还被新浪拖住。
    """
    d = as_of.date()
    out = {c: IntradayContext(code=c, as_of=as_of, trade_date=d, source="实时行情") for c, _, _ in items}
    if not items:
        return out
    try:
        quotes = fetch_stock_quotes_batch([(c, m) for c, m, _ in items])
    except Exception as e:  # noqa: BLE001
        for ctx in out.values():
            ctx.notes.append(f"实时行情失败（{type(e).__name__}）")
        return out
    for c, _, is_index in items:
        ctx, q = out[c], quotes.get(c)
        if q is None or not q.price:
            ctx.notes.append("实时行情里没有这只")
        elif q.trade_date != d:
            ctx.quality = STALE
            ctx.notes.append(f"实时行情的日期是 {q.trade_date}，不是今天（非交易时段？）")
        elif not is_index and not q.volume and q.prev_close and abs(q.price - q.prev_close) < 1e-6:
            ctx.notes.append("今天没有成交、价格停在昨收：大概率停牌")
        else:
            ctx.price, ctx.prev_close, ctx.open, ctx.high, ctx.low = q.price, q.prev_close, q.open, q.high, q.low
            ctx.volume, ctx.amount = q.volume, q.amount
            ctx.observed_at = _naive_sh(q.trade_dt) or as_of
            ctx.resolution, ctx.quality = "quote", EXACT
    return out


def replay_structure(ctx: IntradayContext, pullback_min_pct: float) -> dict:
    """
    把 as_of 之前的分钟价逐根喂给结构状态机，返回 H1 / L1 / 再突破 各自发生在哪。

    价格点用每根的**收盘价**：H1 = 修复阶段的最高收盘，再突破 = 收盘价站上 H1。
    这跟雷达拿实时快照喂状态机是同一个口径，不用分钟内的最高最低去"抢"突破。
    """
    base = {"status": UNKNOWN, "state": None, "anchor": None, "pullback_min_pct": pullback_min_pct,
            "first_repair_at": None, "h1": None, "h1_at": None, "l1": None, "l1_at": None,
            "breakout_at": None, "failed_at": None, "transitions": []}
    if not ctx.bars or not ctx.prev_close:
        base["reason"] = "没有 as_of 之前的分钟数据或昨收" if not ctx.bars else "拿不到昨收"
        return base

    state, rh, pl, ps = STRUCT_WATCH, None, None, False
    cum_vol = cum_amt = 0.0
    vwap = None
    first_repair = h1_at = l1_at = breakout_at = failed_at = None
    transitions = []
    for b in ctx.bars:
        if b.volume and b.amount:
            cum_vol += b.volume
            cum_amt += b.amount
        if cum_vol > 0:
            vwap = _sane_vwap(cum_amt / cum_vol, ctx.prev_close)
        r = compute_structural_transition(
            structural_state=state, price=b.close, prev_close=ctx.prev_close, vwap=vwap, ma5=None,
            recovery_high=rh, pullback_low=pl, pullback_started=ps,
            pullback_min_pct=pullback_min_pct, auction_gap=None, auction_gap_min=float("inf"),
            is_after_auction=False)
        new = r["new_structural_state"]
        if new == STRUCT_REPAIRING and state != STRUCT_REPAIRING:
            first_repair = first_repair or b.dt
            h1_at = b.dt
            l1_at = None
            if state == STRUCT_FAILED:       # 失效后重新收复：新的一轮，前面的 H1/L1 作废
                breakout_at = None
        if new == STRUCT_REPAIRING and r["recovery_high"] is not None and (
                rh is None or r["recovery_high"] > rh):
            h1_at = b.dt
        if new == STRUCT_PULLBACK and r["pullback_low"] is not None and (
                pl is None or r["pullback_low"] < pl):
            l1_at = b.dt
        if new == STRUCT_CONFIRMED and state != STRUCT_CONFIRMED:
            breakout_at = b.dt
        if new == STRUCT_FAILED and state != STRUCT_FAILED:
            failed_at = b.dt
        if new != state:
            transitions.append({"at": b.dt.isoformat(), "from": state, "to": new,
                                "reasons": list(r.get("trigger_reasons") or [])})
        state, rh, pl, ps = new, r["recovery_high"], r["pullback_low"], r["pullback_started"]

    base.update({
        "status": STRUCTURE_STATUS.get(state, UNKNOWN), "state": state,
        "anchor": compute_repair_anchor(ctx.prev_close, vwap, None),
        "first_repair_at": first_repair.isoformat() if first_repair else None,
        "h1": rh, "h1_at": h1_at.isoformat() if (h1_at and rh is not None) else None,
        "l1": pl, "l1_at": l1_at.isoformat() if (l1_at and pl is not None) else None,
        "breakout_at": breakout_at.isoformat() if breakout_at else None,
        "failed_at": failed_at.isoformat() if (failed_at and state == STRUCT_FAILED) else None,
        "transitions": transitions,
    })
    return base
