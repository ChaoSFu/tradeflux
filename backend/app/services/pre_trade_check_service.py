"""
买入检查：按 as_of 取事实 → 跑规则 → 存档（2026-09-11 新增）。

Backend = 事实获取 + 规则判断；前端只负责输入、展示证据、收人工回答。

## as_of 是所有事实的时间边界

| 事实                     | LIVE（此刻）                  | HISTORICAL（过去某一刻）                         |
|--------------------------|-------------------------------|--------------------------------------------------|
| 个股 / 指数日内          | 实时行情 + 当日分钟           | 分钟 bar，`dt <= as_of`（intraday_context_service）|
| 涨跌家数 / 涨跌停家数    | 东财涨跌分布（实时）          | **UNKNOWN**：库里只有收盘后的值                  |
| 截至 as_of 触及涨停家数  | 东财涨停池 + 炸板池（实时）   | 明细表里 `首封/首次触板时间 <= as_of` → 可精确还原 |
| 昨日高标今日表现         | 实时行情                      | 分钟 bar                                         |
| 昨日涨停今日表现         | 实时行情（几十只一批）        | **UNKNOWN**：要逐只取分钟，v1 不做               |
| 板块涨跌家数 / 中位涨跌  | 成分股实时行情                | **UNKNOWN**：同上                                |
| 板块昨日 → 今日延续      | 成分股实时行情                | 首封时间 <= as_of（再次触板）                    |
| 生命周期 / RS / 辨识度   | 只用 as_of **前一交易日**的收盘事实（两种模式都一样）    |
| 交易纪律                 | 只数 `trade_time < as_of` 的记录（被复盘的这笔本身不算）|

**不在历史模式里读"当前值"**：Stock 表上的 board_count_60d、Sector 表上的今日涨幅、
MarketBreadthDaily 当天那行、market_state 的温度——它们都是"最后一次更新时"的值，
拿来冒充过去某一刻就是 look-ahead。

## 不用任何合成分

弱转强的市场/板块/Leader 闸门、情绪温度底下都是加权分，按用户定的规矩新模块不依赖。
这里能复用的只有中立的事实函数：结构状态机、修复关键位、生命周期回放、涨跌停价。
"""
import logging
import statistics
import copy
import threading
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from time import monotonic, perf_counter      # 模块里的 time 是 datetime.time
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional

import httpx
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.leader_cycle import LeaderCycleSnapshot
from ..models.limit_up_detail import BrokenBoardDailyDetail, LimitUpDailyDetail
from ..models.market_index import MarketBreadthDaily
from ..models.pre_trade_check import PreTradeCheck
from ..models.sector import Sector, SectorDailySnapshot, StockSectorRelation
from ..models.stock import Stock, StockDailySnapshot
from ..models.trade_journal import TradeJournal
from . import pre_trade_rules as rules
from .eastmoney_fetcher import (
    HEADERS, SH_TZ, exact_limit_price, fetch_stock_quotes_batch, get_actual_limit_pct, market_int,
)
from .intraday_context_service import (
    APPROX, EXACT, STALE, UNKNOWN, IntradayContext, get_intraday_context, get_intraday_contexts,
    get_quote_contexts, replay_structure,
)
from .leader_cycle_state_service import replay_price_lifecycle
from .limit_up_detail_fetcher import fetch_broken_board_pool, fetch_limit_up_pool
from .market_effect_service import _cohort_snapshots
from .trading_calendar import get_trading_days, last_n_trading_days, prev_trading_day
from .w2s_config_service import KEY_PULLBACK_MIN_PCT, get_numeric
# 私有函数，但它就是"实时涨跌分布"这件事的唯一实现，不另写一份
from .windvane_service import _fetch_updown

CORE_INDEXES = [("000001", 1, "上证指数"), ("399001", 0, "深证成指"), ("399006", 0, "创业板指")]
SECTOR_LIVE_MAX_MEMBERS = 400        # 成分太多的概念（融资融券之类）不做实时广度
HIGH_BOARD_MIN, HIGH_BOARD_TOP = 3, 3
_QUALITY_RANK = {EXACT: 0, APPROX: 1, STALE: 2, UNKNOWN: 3}

# ── 实战日志 ─────────────────────────────────────────────────────────────────
# 每次取数写一行：各路耗时、缓存命中、个股分钟源、哪些模块拿不到；慢了、失败了记 WARNING。
# 2026-09-12 用户决定先保留市场许可，实战一段时间后看这个文件再决定优化什么
logger = logging.getLogger("tradeflux.pretrade")
LOG_FILE = "pre_trade_check.log"
SLOW_CHECK_S = 8.0          # 一次取数超过这个记 WARNING
SLOW_SOURCE_S = 3.0         # 单路超过这个也点名
_PHASES = ("个股", "指数高标", "涨停池", "涨跌分布", "昨日涨停", "板块")
_HIT_ZH = {"touch": "涨停池", "breadth": "涨跌分布", "cohort": "昨日涨停"}
_FAIL_WORDS = ("失败", "没回", "异常")
LIVE_SOURCE_BUDGET_S = 6.0  # 全市场三路实时数最多等这么久（2026-09-12 生产：周六涨停池 ConnectTimeout 等满 20 秒）
_LIVE_SRC = {"touch": "东财涨停池", "breadth": "东财涨跌分布", "cohort": "实时行情"}


def _unknown_live(k: str, note: str) -> dict:
    """全市场某一路这次没取到：形状跟取到时一样，只是 UNKNOWN。"""
    out: Dict[str, Any] = {"meta": _meta(_LIVE_SRC[k], quality=UNKNOWN, notes=[note])}
    if k == "touch":
        out.update(touched=None, codes=[])
    return out


def _gave_up_note(e: Exception) -> str:
    if isinstance(e, TimeoutError):
        return f"{LIVE_SOURCE_BUDGET_S:g} 秒没回，这次不等了（晚到的结果会进缓存）"
    return f"取数异常（{type(e).__name__}）"


def setup_file_log(log_dir: Optional[Path] = None) -> Optional[logging.Handler]:
    """
    挂上 backend/logs/pre_trade_check.log（每天零点轮转，留 30 天）。服务启动时调一次；
    测试不调，跑测试不会往真实日志里写。目录写不了就算了——日志不能拦住检查本身。
    """
    log_dir = Path(log_dir) if log_dir else Path(__file__).resolve().parents[2] / "logs"
    target = str((log_dir / LOG_FILE).resolve())
    for h in logger.handlers:
        if getattr(h, "baseFilename", None) == target:
            return h
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        h = TimedRotatingFileHandler(target, when="midnight", backupCount=30, encoding="utf-8")
    except OSError:
        return None
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)
    return h


def _log_context(tag: str, ctx: dict) -> None:
    """
    一次取数一行，格式固定方便 grep / awk，例如：
    context LIVE 600354 as_of=2026-09-12T10:01:02 总4.8s | 个股0.9 指数高标0.7 涨停池4.2 涨跌分布=缓存 …
      | 个股源 腾讯0.4 行情0.3 | 未知[…] 过时[…] | 慢[涨停池4.2s] | 失败[…]
    """
    tm = ctx.get("timings") or {}
    hit = set(tm.get("缓存命中") or [])
    phases = " ".join(f"{p}=缓存" if p in hit else f"{p}{tm[p]:.1f}" for p in _PHASES if p in tm)
    src = " ".join(f"{k}{v:.1f}" for k, v in (tm.get("个股分钟源") or {}).items()) or "—"
    dq = ctx.get("data_quality") or []
    unk = [r["module"] for r in dq if r.get("quality") == UNKNOWN]
    stale = [r["module"] for r in dq if r.get("quality") == STALE]
    fails = [f"{r['module']}:{n}" for r in dq for n in (r.get("notes") or []) if any(w in n for w in _FAIL_WORDS)]
    slow = [f"{p}{tm[p]:.1f}s" for p in _PHASES if p in tm and p not in hit and tm[p] >= SLOW_SOURCE_S]
    total = tm.get("总") or 0.0
    msg = (f"{tag} {ctx.get('mode')} {(ctx.get('stock') or {}).get('code')} as_of={ctx.get('as_of')} 总{total:.1f}s"
           f" | {phases} | 个股源 {src}"
           + (f" | 未知[{','.join(unk)}]" if unk else "") + (f" 过时[{','.join(stale)}]" if stale else "")
           + (f" | 慢[{','.join(slow)}]" if slow else "") + (f" | 失败[{'; '.join(fails)}]" if fails else ""))
    (logger.warning if (total >= SLOW_CHECK_S or slow or fails) else logger.info)(msg)


# 全市场的数（涨跌分布、涨停池、昨日涨停群体）对每只票都一样：半分钟内连查几只不重拉。
# 东财涨跌分布一次要 3～10 秒（三个市场串行），是实时模式里最慢的一路
_LIVE_TTL_S = 30
_live_cache: Dict[tuple, tuple] = {}
_live_lock = threading.Lock()


def _cached_live(key: tuple, fn, hits: Optional[set] = None) -> dict:
    """取到的才缓存（UNKNOWN 不缓存，下次再试）。给副本：调用方改了不串到下一次。
    observed_at 还是当初取数的时刻——缓存不冒充「刚取的」。"""
    now = monotonic()
    with _live_lock:
        hit = _live_cache.get(key)
    if hit and now - hit[0] < _LIVE_TTL_S:
        if hits is not None:
            hits.add(key[0])
        return copy.deepcopy(hit[1])
    val = fn()
    if (val.get("meta") or {}).get("quality") != UNKNOWN:
        with _live_lock:
            _live_cache[key] = (now, copy.deepcopy(val))
    return val


def now_sh() -> datetime:
    return datetime.now(SH_TZ).replace(tzinfo=None)


def _market_of(code: str, stock: Optional[Stock]) -> int:
    """库里有就用库里的；没有就按代码前缀——market_int(None, ..) 会把沪市票拼成 sz。"""
    if stock is not None:
        return market_int(stock.market, code)
    return market_int("SH" if code.startswith(("6", "5", "900")) else "SZ", code)


def _meta(source: str, observed_at=None, quality: str = UNKNOWN, notes=None) -> dict:
    if isinstance(observed_at, datetime):
        observed_at = observed_at.isoformat(timespec="seconds")
    return {"source": source, "observed_at": observed_at, "quality": quality, "notes": list(notes or [])}


def _worst(qs) -> str:
    qs = [q for q in qs if q]
    return max(qs, key=lambda q: _QUALITY_RANK.get(q, 3)) if qs else UNKNOWN


def _jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    return v


def _prev_trade_date(db: Session, d: date, cal) -> Optional[date]:
    if cal:
        return prev_trading_day(cal, d)
    return db.query(func.max(StockDailySnapshot.date)).filter(StockDailySnapshot.date < d).scalar()


def _last_weekday_before(d: date) -> date:
    x = d - timedelta(days=1)
    while x.weekday() >= 5:
        x -= timedelta(days=1)
    return x


def _calendar_status(cal: Optional[List[date]], d: date) -> dict:
    """
    交易日历（fuyao）只到拉取当天、不含未来日：盘中 d 还没进日历是常态，**不等于「非交易日」**。
    但日历最后一天和 d 之间如果还夹着工作日，就是日历落后了——前一交易日可能算错，
    T-1 的事实（高标周期、昨日涨停、板块昨日表现）不能再当成 T-1 用。
    """
    weekday = d.weekday() < 5
    if not cal:
        return {"last": None, "behind": None, "is_trading_day": None if weekday else False}
    last = cal[-1]
    if d <= last:
        return {"last": last, "behind": False, "is_trading_day": d in set(cal)}
    gap = [last + timedelta(days=i) for i in range(1, (d - last).days)]
    return {"last": last, "behind": any(x.weekday() < 5 for x in gap),
            "is_trading_day": None if weekday else False}


# ── 个股 ──────────────────────────────────────────────────────────────────────

def _stock_block(code: str, stock: Optional[Stock], sctx) -> dict:
    is_st = bool(stock.is_st) if stock else False
    lp = get_actual_limit_pct(code, is_st)
    pc = sctx.prev_close
    return {
        "code": code, "name": stock.name if stock else None, "in_db": stock is not None,
        "is_st": is_st, "limit_pct": lp,
        "limit_up_price": exact_limit_price(pc, lp, True) if pc else None,
        "limit_down_price": exact_limit_price(pc, lp, False) if pc else None,
    }


# ── 市场 ──────────────────────────────────────────────────────────────────────

def _prev_high_boards(db: Session, prev_d: Optional[date]) -> List[dict]:
    if not prev_d:
        return []
    rows = (db.query(Stock.code, Stock.name, Stock.market, StockDailySnapshot.board_count)
            .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
            .filter(StockDailySnapshot.date == prev_d, StockDailySnapshot.is_limit_up.is_(True),
                    StockDailySnapshot.board_count >= HIGH_BOARD_MIN)
            .order_by(StockDailySnapshot.board_count.desc()).limit(HIGH_BOARD_TOP).all())
    return [{"code": c, "name": n, "market": market_int(m, c), "board_prev": b} for c, n, m, b in rows]


def _limit_touch_from_db(db: Session, d: date, as_of: datetime) -> dict:
    """
    截至 as_of **触及过**涨停的家数：涨停明细（首封时间）∪ 炸板明细（首次触板时间）。
    这是能从收盘后的明细精确还原的——事件发生在 as_of 之前。还原不了的是"as_of 那一刻
    是封着还是开着"，所以只报"触及过"，不报"封着"。
    """
    t = as_of.time()
    zt = db.query(LimitUpDailyDetail.stock_code, LimitUpDailyDetail.first_limit_time,
                  LimitUpDailyDetail.board_count, LimitUpDailyDetail.refreshed_at).filter(
        LimitUpDailyDetail.trade_date == d).all()
    zb = db.query(BrokenBoardDailyDetail.stock_code, BrokenBoardDailyDetail.first_limit_time,
                  BrokenBoardDailyDetail.board_count, BrokenBoardDailyDetail.refreshed_at).filter(
        BrokenBoardDailyDetail.trade_date == d).all()
    if not zt and not zb:
        return {"touched": None, "codes": [], "meta": _meta("涨停/炸板明细", quality=UNKNOWN,
                                                          notes=[f"库里没有 {d} 的涨停明细"])}
    touched: Dict[str, Optional[int]] = {}
    for code, ft, bc, _ in list(zt) + list(zb):
        if ft is not None and ft <= t:
            touched[code] = max(touched.get(code) or 0, bc or 0)
    refreshed = [r for *_, r in list(zt) + list(zb) if r]
    last = max(refreshed) if refreshed else None
    quality, notes = EXACT, ["按首封 / 首次触板时间 ≤ as_of 还原；as_of 那一刻封着还是开着还原不了"]
    if last is not None and last < as_of:
        quality = STALE
        notes.append(f"明细最后刷新在 {last:%H:%M}，早于 as_of，之后才触板的票不在里面")
    elif last is not None:
        # 数据时点写 as_of，入库时间放说明里：看到 17:39 不要误以为用了未来信息（2026-09-12 评审）
        notes.append(f"明细 {last:%m-%d %H:%M} 入库（盘后数据），只用了 as_of 之前发生的触板事件")
    return {"touched": len(touched), "max_board": max(touched.values(), default=None) or None,
            "broken_now": None, "codes": sorted(touched),
            "meta": _meta("涨停明细 + 炸板明细（库）", as_of, quality, notes)}


def _limit_touch_live(d: date) -> dict:
    try:
        with ThreadPoolExecutor(max_workers=2) as ex:       # 两个池子互不依赖，一起取
            fzt, fzb = ex.submit(fetch_limit_up_pool, d), ex.submit(fetch_broken_board_pool, d)
            zt, zb = fzt.result(), fzb.result()
    except Exception as e:  # noqa: BLE001
        return {"touched": None, "codes": [], "meta": _meta("东财涨停池", quality=UNKNOWN,
                                                          notes=[f"实时涨停池取数失败（{type(e).__name__}）"])}
    codes = {x.code for x in zt} | {x.code for x in zb}
    return {"touched": len(codes), "max_board": max((x.board_count or 0 for x in zt), default=None) or None,
            "broken_now": len(zb), "codes": sorted(codes),
            "meta": _meta("东财涨停池 + 炸板池（实时）", now_sh(), EXACT)}


def _breadth_live() -> dict:
    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=10) as c:
            ud = _fetch_updown(c)
    except Exception as e:  # noqa: BLE001
        return {"meta": _meta("东财涨跌分布", quality=UNKNOWN,
                              notes=[f"实时涨跌分布取数失败（{type(e).__name__}）"])}
    return {"up": ud.up, "down": ud.down, "flat": ud.flat, "limit_up": ud.limit_up,
            "limit_down": ud.limit_down, "meta": _meta("东财涨跌分布（实时）", now_sh(), EXACT)}


def _prev_cohort_codes(db: Session, prev_d: Optional[date]) -> Optional[List[tuple]]:
    """昨日涨停群体的成员（查库，留在主线程）。成员定义走 market_effect 的 _cohort_snapshots——同一个群体。"""
    if not prev_d:
        return None
    members = _cohort_snapshots(db, prev_d, "limit_up")
    if not members:
        return []
    stocks = db.query(Stock.code, Stock.market).filter(Stock.id.in_([m.stock_id for m in members])).all()
    return [(c, market_int(m, c)) for c, m in stocks]


def _cohort_quotes(codes: Optional[List[tuple]], prev_d: Optional[date], d: date) -> dict:
    """昨日涨停的票此刻涨跌。只走网络，可以放进线程池。"""
    if codes is None:
        return {"meta": _meta("—", quality=UNKNOWN, notes=["拿不到前一交易日"])}
    if not codes:
        return {"meta": _meta("—", quality=UNKNOWN, notes=[f"{prev_d} 没有涨停股"])}
    try:
        quotes = fetch_stock_quotes_batch(codes)
    except Exception as e:  # noqa: BLE001
        return {"meta": _meta("实时行情", quality=UNKNOWN, notes=[f"行情取数失败（{type(e).__name__}）"])}
    pcts = [q.pct_change for q in quotes.values() if q.trade_date == d and q.pct_change is not None]
    if not pcts:
        return {"meta": _meta("实时行情", quality=UNKNOWN, notes=["没有今天的行情"])}
    return {"median_pct": round(statistics.median(pcts), 2), "n": len(pcts), "members": len(codes),
            "red_ratio": round(sum(1 for p in pcts if p > 0) / len(pcts), 3),
            "meta": _meta("实时行情", now_sh(), EXACT)}


def _prev_cohort_live(db: Session, prev_d: Optional[date], d: date) -> dict:
    return _cohort_quotes(_prev_cohort_codes(db, prev_d), prev_d, d)


def _prev_day_market(db: Session, prev_d: Optional[date]) -> dict:
    if not prev_d:
        return {}
    out: Dict[str, Any] = {"date": prev_d.isoformat()}
    try:
        row = db.query(MarketBreadthDaily).filter(MarketBreadthDaily.date == prev_d).first()
        if row:
            out.update(limit_up=row.limit_up_count, limit_down=row.limit_down_count)
    except Exception:  # noqa: BLE001  —— 测试库建不了这张表（JSONB）
        db.rollback()
    out["max_height"] = db.query(func.max(StockDailySnapshot.board_count)).filter(
        StockDailySnapshot.date == prev_d, StockDailySnapshot.is_limit_up.is_(True)).scalar()
    return out


def _market_block(db, live, as_of, d, prev_d, intr, high_boards, live_facts=None) -> dict:
    lf = live_facts or {}          # build_context 在线程池里预先取好的全市场实时数
    indexes, notes = [], []
    for code, _, name in CORE_INDEXES:
        c = intr.get(code)
        if c is None:
            continue
        indexes.append({"code": code, "name": name, "pct": c.pct, "price": c.price,
                        "quality": c.quality, "source": c.source,
                        "observed_at": c.observed_at.isoformat() if c.observed_at else None})
        notes += c.notes
    touch = (lf.get("touch") or _limit_touch_live(d)) if live else _limit_touch_from_db(db, d, as_of)
    breadth = (lf.get("breadth") or _breadth_live()) if live else {"meta": _meta("—", quality=UNKNOWN, notes=[
        "历史时刻的涨跌家数、涨跌停家数无法还原：库里只有收盘后的值，不拿它冒充 as_of"])}
    cohort = (lf.get("cohort") or _prev_cohort_live(db, prev_d, d)) if live else {"meta": _meta("—", quality=UNKNOWN, notes=[
        "历史时刻的昨日涨停表现要逐只取分钟数据（几十只），v1 不做"])}
    hb = []
    for h in high_boards:
        c = intr.get(h["code"])
        hb.append({**h, "pct": c.pct if c else None, "quality": c.quality if c else UNKNOWN})
    return {
        "indexes": indexes,
        "indexes_meta": _meta("实时行情" if live else "腾讯分钟价", quality=_worst(i["quality"] for i in indexes), notes=list(dict.fromkeys(notes))[:3]),
        "breadth": breadth, "limit_touch": touch, "prev_cohort": cohort,
        "high_boards": hb, "prev_day": _prev_day_market(db, prev_d),
    }


# ── 板块 ──────────────────────────────────────────────────────────────────────

def _sector_block(db, live, as_of, d, prev_d, stock, thesis_id, sctx, touch_codes, cal=None) -> dict:
    if stock is None:
        return {"thesis": None, "options": [], "meta": _meta("—", notes=["股票不在库里，没有板块归属"])}
    rels = (db.query(Sector.id, Sector.name, Sector.is_watched, Sector.stock_count)
            .join(StockSectorRelation, StockSectorRelation.sector_id == Sector.id)
            .filter(StockSectorRelation.stock_id == stock.id).all())
    if not rels:
        return {"thesis": None, "options": [], "meta": _meta("—", notes=["库里没有这只票的板块归属"])}
    # 板块涨停数**只有一个口径**：成分股日快照里 is_limit_up 的只数（下拉框、昨日、序列、延续性都用它）。
    # 不用 SectorDailySnapshot.limit_up_count——那是板块阶段服务的另一套算法，
    # 两套并排会出现「昨日板块涨停 7 只」旁边「涨停数 … → 2」这种自相矛盾
    have_prev = bool(prev_d) and db.query(StockDailySnapshot.id).filter(StockDailySnapshot.date == prev_d).first() is not None
    prev_lu: Dict[int, int] = {}
    if have_prev:
        prev_lu = dict(db.query(StockSectorRelation.sector_id, func.count(StockDailySnapshot.id))
                       .join(StockDailySnapshot, StockDailySnapshot.stock_id == StockSectorRelation.stock_id)
                       .filter(StockSectorRelation.sector_id.in_([r[0] for r in rels]),
                               StockDailySnapshot.date == prev_d, StockDailySnapshot.is_limit_up.is_(True))
                       .group_by(StockSectorRelation.sector_id).all())
    options = [{"id": sid, "name": n, "is_watched": bool(w), "stock_count": sc or 0,
                "is_primary": sid == stock.primary_sector_id,
                "prev_limit_up": prev_lu.get(sid, 0) if have_prev else None}
               for sid, n, w, sc in rels]
    # 默认的「本次交易逻辑板块」：主板块 > 关注板块里昨日涨停最多的 > 成分最少的（最具体）。
    # 一只票属于很多概念，**用户可以改**——系统只是给个起点
    by_id = {o["id"]: o for o in options}
    thesis = by_id.get(thesis_id) or next((o for o in options if o["is_primary"]), None) or (
        max((o for o in options if o["is_watched"]), key=lambda o: o["prev_limit_up"] or 0, default=None)
    ) or min(options, key=lambda o: o["stock_count"] or 10 ** 6)
    options.sort(key=lambda o: (not o["is_primary"], not o["is_watched"], -(o["prev_limit_up"] or 0)))

    out: Dict[str, Any] = {"thesis": {"id": thesis["id"], "name": thesis["name"]}, "options": options}
    members = (db.query(Stock.id, Stock.code, Stock.name, Stock.market, Stock.is_st)
               .join(StockSectorRelation, StockSectorRelation.stock_id == Stock.id)
               .filter(StockSectorRelation.sector_id == thesis["id"]).all())
    member_ids = [m[0] for m in members]
    prev_limit = set()
    if prev_d and members:
        prev_limit = {sid for (sid,) in db.query(StockDailySnapshot.stock_id).filter(
            StockDailySnapshot.date == prev_d, StockDailySnapshot.is_limit_up.is_(True),
            StockDailySnapshot.stock_id.in_(member_ids))}
        out["sector_max_board_prev"] = db.query(func.max(StockDailySnapshot.board_count)).filter(
            StockDailySnapshot.date == prev_d, StockDailySnapshot.stock_id.in_(member_ids)).scalar()
        days = last_n_trading_days(cal, prev_d, 5) if cal else [prev_d]
        per_day = dict(db.query(StockDailySnapshot.date, func.count(StockDailySnapshot.id))
                       .filter(StockDailySnapshot.stock_id.in_(member_ids), StockDailySnapshot.date.in_(days),
                               StockDailySnapshot.is_limit_up.is_(True))
                       .group_by(StockDailySnapshot.date).all())
        # 那天库里有没有日快照：没有就是「不知道」，不能写成 0
        covered = {x for (x,) in db.query(StockDailySnapshot.date)
                   .filter(StockDailySnapshot.date.in_(days)).distinct()}
        out["trend"] = [{"date": x.isoformat(), "limit_up_count": per_day.get(x, 0) if x in covered else None}
                        for x in days]
        if prev_d in covered:
            out["prev_day"] = {"date": prev_d.isoformat(), "limit_up_count": len(prev_limit),
                               "board_height": out["sector_max_board_prev"]}
    prev_limit_codes = {m[1] for m in members if m[0] in prev_limit}

    if live and 0 < len(members) <= SECTOR_LIVE_MAX_MEMBERS:
        try:
            quotes = fetch_stock_quotes_batch([(c, market_int(mk, c)) for _, c, _, mk, _ in members])
        except Exception as e:  # noqa: BLE001
            quotes = {}
            out["live"] = {"meta": _meta("成分股实时行情", quality=UNKNOWN, notes=[f"取数失败（{type(e).__name__}）"])}
        today = {c: q for c, q in quotes.items() if q.trade_date == d and q.pct_change is not None}
        if today:
            pcts = [q.pct_change for q in today.values()]
            lu_now = ld_now = 0
            st_by_code = {c: bool(s) for _, c, _, _, s in members}
            for c, q in today.items():
                if q.prev_close and q.price:
                    lp = get_actual_limit_pct(c, st_by_code.get(c, False))
                    if q.price >= exact_limit_price(q.prev_close, lp, True) - 1e-6:
                        lu_now += 1
                    elif q.price <= exact_limit_price(q.prev_close, lp, False) + 1e-6:
                        ld_now += 1
            median = round(statistics.median(pcts), 2)
            out["live"] = {"members": len(today), "up": sum(1 for p in pcts if p > 0),
                           "down": sum(1 for p in pcts if p < 0), "flat": sum(1 for p in pcts if p == 0),
                           "median_pct": median, "limit_up_now": lu_now, "limit_down_now": ld_now,
                           "meta": _meta("成分股实时行情（等权）", now_sh(), EXACT)}
            # 板块里谁比它强：跑赢大盘不等于是板块里的强者（2026-09-12 评审）
            names = {c: n for _, c, n, _, _ in members}
            ordered = sorted(today.items(), key=lambda kv: -kv[1].pct_change)
            mine = today.get(stock.code)
            out["live"]["ranking"] = {
                "top": [{"code": c, "name": names.get(c) or c, "pct": round(q.pct_change, 2)} for c, q in ordered[:3]],
                "rank": next((n + 1 for n, (c, _) in enumerate(ordered) if c == stock.code), None),
                "total": len(ordered),
                "leader_gap": round(ordered[0][1].pct_change - mine.pct_change, 2) if mine else None,
            }
            if sctx.pct is not None:
                out["stock_pct"], out["stock_vs_sector"] = sctx.pct, round(sctx.pct - median, 2)
            prev_today = [today[c] for c in prev_limit_codes if c in today]
            out["continuation"] = {"prev_limit_ups": len(prev_limit_codes),
                                   "down_now": sum(1 for q in prev_today if q.pct_change < 0),
                                   "retouched": None}
        elif "live" not in out:
            out["live"] = {"meta": _meta("成分股实时行情", quality=UNKNOWN, notes=["没有今天的行情"])}
    elif live:
        out["live"] = {"meta": _meta("—", quality=UNKNOWN,
                                     notes=[f"「{thesis['name']}」成分 {len(members)} 只，太宽泛，不做实时广度"])}
    else:
        out["live"] = {"meta": _meta("—", quality=UNKNOWN, notes=[
            "历史时刻的板块涨跌家数无法还原：没有成分股的分钟数据，逐只去取成本太高"])}
        out["continuation"] = {"prev_limit_ups": len(prev_limit_codes), "down_now": None,
                               "retouched": len(prev_limit_codes & set(touch_codes or []))}
    out["meta"] = _meta("板块归属 + 成分股日快照 + 成分股行情", quality=(out.get("live") or {}).get("meta", {}).get("quality", UNKNOWN))
    return out


# ── Leader / 个股资格 ────────────────────────────────────────────────────────

def _leader_block(db, stock, prev_d, cal, sector_block, sctx, stock_block) -> dict:
    out: Dict[str, Any] = {"is_st": stock_block.get("is_st")}
    up = stock_block.get("limit_up_price")
    if up and sctx.price:
        out["limit_room_pct"] = round((up / sctx.price - 1) * 100, 2)
    if stock is None or prev_d is None:
        out["meta"] = _meta("—", notes=["股票不在库里或拿不到前一交易日"])
        return out
    snap = db.query(StockDailySnapshot).filter(StockDailySnapshot.stock_id == stock.id,
                                               StockDailySnapshot.date == prev_d).first()
    if snap:
        out.update(board_prev=snap.board_count, board_count_60d=snap.board_count_60d,
                   limit_up_days_20d=snap.limit_up_days_20d, pct_change_20d=snap.pct_change_20d)
    out["sector_max_board_prev"] = sector_block.get("sector_max_board_prev")
    # look-ahead guard：只喂 as_of 前一交易日（含）之前的行，当天的行一行都不进
    rows = (db.query(LeaderCycleSnapshot).filter(LeaderCycleSnapshot.stock_id == stock.id,
                                                 LeaderCycleSnapshot.date <= prev_d)
            .order_by(LeaderCycleSnapshot.date).all())
    if rows:
        st = replay_price_lifecycle(rows, prev_d, trading_days=cal)
        last = rows[-1]
        out["lifecycle"] = {"date": prev_d.isoformat(), "state": st.state,
                            "last_valid_state": st.last_valid_state, "days_in_state": st.days_in_state,
                            "entry_reasons": st.entry_reasons,
                            "days_since_break": last.days_since_break if last.date == prev_d else None}
        if last.date == prev_d:
            out.update(rs_market_20=last.rs_market_20, rs_market_20_delta_3d=last.rs_market_20_delta_3d,
                       rs_sector_20=last.rs_sector_20)
    out["meta"] = _meta(f"{prev_d} 收盘的日快照 + 高标周期快照", prev_d.isoformat(), EXACT if snap else UNKNOWN)
    return out


# ── 个人行为纪律 ─────────────────────────────────────────────────────────────

def _journal_row(t: TradeJournal) -> dict:
    return {"id": t.id, "stock_code": t.stock_code, "stock_name": t.stock_name, "action": t.action,
            "trade_time": t.trade_time.isoformat(), "price": t.price, "position_pct": t.position_pct,
            "realized_pnl": t.realized_pnl, "pnl_pct": t.pnl_pct}


def _journal_entry(db, owner: Optional[str], journal_id: Optional[int]) -> Optional[dict]:
    """从交易记录进来复盘：带出当时写的理由，跟复盘时补写的分开放——事后的解释不能冒充当时的想法。"""
    if not journal_id or not owner:
        return None
    t = db.query(TradeJournal).filter(TradeJournal.id == journal_id, TradeJournal.owner == owner).first()
    if t is None:
        return None
    return {"id": t.id, "stock_code": t.stock_code, "trade_time": t.trade_time.isoformat(), "action": t.action,
            "price": t.price, "position_pct": t.position_pct, "planned_stop": t.planned_stop,
            "reason": t.reason, "emotion_tag": t.emotion_tag, "note": t.note}


def _discipline_block(db, owner, code, as_of, cal) -> dict:
    if not owner:
        return {"available": False, "reason": "未登录"}
    # 只数 as_of **之前**的记录。复盘某一笔时，那一笔本身（trade_time == as_of）不算
    before = db.query(TradeJournal).filter(TradeJournal.owner == owner, TradeJournal.trade_time < as_of)
    day_start = datetime.combine(as_of.date(), time(0, 0))
    today_buys = before.filter(TradeJournal.action == "买入", TradeJournal.trade_time >= day_start) \
        .order_by(TradeJournal.trade_time).all()

    notes = []
    days = last_n_trading_days(cal, as_of.date(), rules.REENTRY_TRADING_DAYS) if cal else None
    if days:
        window_start = datetime.combine(days[0], time(0, 0))
    else:
        window_start = as_of - timedelta(days=7)
        notes.append("拿不到交易日历，5 个交易日按 7 个自然日近似")
    recent = before.filter(TradeJournal.stock_code == code, TradeJournal.trade_time >= window_start) \
        .order_by(TradeJournal.trade_time.desc()).all()
    last_sell = next((t for t in recent if t.action == "卖出" and (t.realized_pnl is not None or t.pnl_pct is not None)), None)
    last_pnl = None
    if last_sell is not None:
        last_pnl = last_sell.realized_pnl if last_sell.realized_pnl is not None else last_sell.pnl_pct

    streak = 0
    for t in before.filter(TradeJournal.action == "卖出").order_by(TradeJournal.trade_time.desc()).limit(10):
        pnl = t.realized_pnl if t.realized_pnl is not None else t.pnl_pct
        if pnl is None:
            notes.append("有卖出记录没填盈亏，连续亏损数到那一笔为止")
            break
        if pnl < 0:
            streak += 1
        else:
            break

    same = before.filter(TradeJournal.stock_code == code).order_by(TradeJournal.trade_time).all()
    net, approx = 0.0, False
    for t in same:
        if t.position_pct is None:
            approx = True
            continue
        net += t.position_pct if t.action == "买入" else -t.position_pct
    holding = net > 0.01 if not approx else bool(same and same[-1].action == "买入")
    return {"available": True, "today_buys": [_journal_row(t) for t in today_buys],
            "recent_same_stock": [_journal_row(t) for t in recent], "last_same_stock_pnl": last_pnl,
            "consecutive_losses": streak,
            "holding": {"holding": holding, "net_position_pct": round(net, 1) if not approx else None,
                        "approx": approx},
            "meta": _meta("交易复盘（Trade Journal）", as_of, EXACT, notes)}


# ── 汇总 ──────────────────────────────────────────────────────────────────────

def _result_or(fut, fallback, name: str = "", timeout: Optional[float] = None):
    try:
        return fut.result(timeout=timeout)
    except Exception as e:  # noqa: BLE001
        if not isinstance(e, TimeoutError):     # 超时会在检查那一行记「失败[…没回]」，这里不重复
            # 各路自己都兜底了，走到这里是没想到的异常（比如那次时区 TypeError）：必须留痕
            logger.warning("取数异常 %s: %r", name, e)
        return fallback(e)


def _timed(timings: dict, name: str, fn, *args, **kwargs):
    """在线程里跑 fn，耗时记进 timings[name]（每个 name 只有一个线程写）。"""
    t = perf_counter()
    try:
        return fn(*args, **kwargs)
    finally:
        timings[name] = round(perf_counter() - t, 2)


def _failed_ctx(code: str, as_of: datetime, e: Exception) -> IntradayContext:
    ctx = IntradayContext(code=code, as_of=as_of, trade_date=as_of.date())
    ctx.notes.append(f"取数异常（{type(e).__name__}）")
    return ctx


def build_context(db: Session, owner: Optional[str], code: str, as_of: Optional[datetime],
                  thesis_sector_id: Optional[int] = None, journal_id: Optional[int] = None,
                  log_tag: str = "context") -> dict:
    t0 = perf_counter()
    now = now_sh()
    live = as_of is None
    if as_of is not None and as_of.tzinfo is not None:
        as_of = as_of.astimezone(SH_TZ).replace(tzinfo=None)
    as_of = now if live else as_of
    if as_of > now + timedelta(seconds=5):
        raise ValueError("as_of 在未来：历史复盘只能选已经发生的时刻")
    d = as_of.date()
    try:
        # 只要求日历覆盖到 d 的前一个工作日：盘中 d 本身不在日历里是常态，
        # 按 d 要求会让每次实时检查都去重拉一次日历
        cal = get_trading_days(db, need_through=_last_weekday_before(d))
    except Exception:  # noqa: BLE001
        cal = None
    prev_d = _prev_trade_date(db, d, cal)
    cal_st = _calendar_status(cal, d)
    stock = db.query(Stock).filter(Stock.code == code).first()
    market = _market_of(code, stock)
    pullback = get_numeric(db, KEY_PULLBACK_MIN_PCT)

    high_boards = _prev_high_boards(db, prev_d)
    # 指数、昨日高标只要 as_of 那一刻的涨跌幅：实时 = 一次批量行情，历史 = 腾讯分钟价。
    # 跟个股分开放：个股是 000001（平安银行）时不会跟上证指数 000001 串号
    others = ([(c, m, True) for c, m, _ in CORE_INDEXES]
              + [(h["code"], h["market"], False) for h in high_boards if h["code"] != code])
    cohort_codes = _prev_cohort_codes(db, prev_d) if live else None   # 查库留在主线程，线程里只走网络
    # 外部请求一起发。之前是一段接一段：最慢的东财涨跌分布要等 7 路分钟数据全取完才开始
    timings: Dict[str, Any] = {}
    hits: set = set()
    # 非交易日做实时检查：全市场的实时数没有意义，东财的池子周末还可能连不上——直接不取
    trading_day = cal_st["is_trading_day"] is not False
    ex = ThreadPoolExecutor(max_workers=5)
    try:
        t_submit = monotonic()
        f_stock = ex.submit(_timed, timings, "个股", get_intraday_context, code, market, as_of, live=live)
        f_others = (ex.submit(_timed, timings, "指数高标", get_quote_contexts, others, as_of) if live
                    else ex.submit(_timed, timings, "指数高标", get_intraday_contexts, others, as_of,
                                   live=False, detail=False))
        f_live = {}
        if live and trading_day:
            f_live = {"touch": ex.submit(_timed, timings, "涨停池", _cached_live, ("touch", d),
                                         lambda: _limit_touch_live(d), hits),
                      "breadth": ex.submit(_timed, timings, "涨跌分布", _cached_live, ("breadth", d),
                                           lambda: _breadth_live(), hits),
                      "cohort": ex.submit(_timed, timings, "昨日涨停", _cached_live, ("cohort", prev_d, d),
                                          lambda: _cohort_quotes(cohort_codes, prev_d, d), hits)}
        sctx = _result_or(f_stock, lambda e: _failed_ctx(code, as_of, e), "个股")
        intr = _result_or(f_others, lambda e: {}, "指数高标")
        live_facts = None
        if live and not trading_day:
            live_facts = {k: _unknown_live(k, f"{d} 不是交易日，不取实时数据") for k in _LIVE_SRC}
        elif live:
            # 全市场三路从发出算起最多等 LIVE_SOURCE_BUDGET_S 秒：没回来的这次不等，线程自己跑完——
            # 取到了照样进缓存，下一次检查直接用
            live_facts = {}
            for k, f in f_live.items():
                left = max(0.05, LIVE_SOURCE_BUDGET_S - (monotonic() - t_submit))
                live_facts[k] = _result_or(f, lambda e, k=k: _unknown_live(k, _gave_up_note(e)),
                                           _HIT_ZH[k], timeout=left)
                timings.setdefault(_HIT_ZH[k], round(monotonic() - t_submit, 2))
    finally:
        ex.shutdown(wait=False)             # 不等还在跑的慢线程
    timings = dict(timings)                 # 快照：慢线程晚到的耗时不再改这次的记录

    structure = replay_structure(sctx, pullback)
    sh = intr.get("000001")
    intraday = {**sctx.summary(), "structure": structure,
                "vs_market": round(sctx.pct - sh.pct, 2) if (sctx.pct is not None and sh and sh.pct is not None) else None}
    stock_block = _stock_block(code, stock, sctx)
    market = _market_block(db, live, as_of, d, prev_d, intr, high_boards, live_facts)
    t = perf_counter()
    sector = _sector_block(db, live, as_of, d, prev_d, stock, thesis_sector_id, sctx,
                           (market.get("limit_touch") or {}).get("codes"), cal)
    timings["板块"] = round(perf_counter() - t, 2)
    leader = _leader_block(db, stock, prev_d, cal, sector, sctx, stock_block)
    if cal_st["behind"] and leader.get("meta"):
        leader["meta"]["quality"] = STALE
        leader["meta"]["notes"].append(f"交易日历只到 {cal_st['last']}，{prev_d} 不一定是真正的前一交易日")
    discipline = _discipline_block(db, owner, code, as_of, cal)
    journal_entry = _journal_entry(db, owner, journal_id)

    cal_note = ([f"日历只到 {cal_st['last']}，之后还有工作日没入库：前一交易日按 {prev_d} 算，可能是旧的"]
                if cal_st["behind"] else [] if cal else ["拿不到交易日历，前一交易日按库里最近一天的快照算"])
    data_quality = [
        {"module": "交易日历 / 前一交易日",
         **_meta("fuyao 交易日历" if cal else "日快照反推", cal_st["last"],
                 UNKNOWN if not cal else STALE if cal_st["behind"] else EXACT, cal_note)},
        {"module": "日内（个股）", **_meta(sctx.source or "—", sctx.observed_at, sctx.quality, sctx.notes)},
        {"module": "市场·指数", **market["indexes_meta"]},
        {"module": "市场·涨跌家数", **(market["breadth"].get("meta") or {})},
        {"module": "市场·触板家数", **(market["limit_touch"].get("meta") or {})},
        {"module": "市场·昨日涨停今日", **(market["prev_cohort"].get("meta") or {})},
        {"module": "板块", **((sector.get("live") or {}).get("meta") or sector.get("meta") or {})},
        {"module": "Leader / 生命周期", **(leader.get("meta") or {})},
        {"module": "交易纪律", **(discipline.get("meta") or _meta("—", notes=[discipline.get("reason", "")]))},
    ]
    timings["总"] = round(perf_counter() - t0, 2)
    # 耗时也进事实快照：存档的检查以后可以直接在库里统计
    timings.update({"个股分钟源": sctx.timings, "缓存命中": sorted(_HIT_ZH[k] for k in hits)})
    out = _jsonable({
        "mode": "LIVE" if live else "HISTORICAL", "as_of": as_of.isoformat(timespec="seconds"),
        "trade_date": d, "prev_trade_date": prev_d,
        "is_trading_day": cal_st["is_trading_day"],
        "calendar": {"last": cal_st["last"], "behind": cal_st["behind"]},
        "rule_version": rules.RULE_VERSION, "pullback_min_pct": pullback,
        # 问题原文和默认参数由后端下发：前端不另抄一份，改问题只改 pre_trade_rules 一处
        "manual_questions": [{"key": k, "text": q} for k, q in rules.MANUAL_QUESTIONS],
        "reason_fields": [{"key": k, "label": lb, "placeholder": ph} for k, lb, ph in rules.REASON_FIELDS],
        "invalidation_types": [{"key": k, "label": lb, "hint": h} for k, lb, h in rules.INVALIDATION_TYPES],
        "journal_entry": journal_entry,
        "defaults": {"account_risk_budget_pct": rules.DEFAULT_ACCOUNT_RISK_BUDGET_PCT,
                     "stress_loss_pct": rules.DEFAULT_STRESS_LOSS_PCT,
                     "earliest_normal_entry": rules.EARLIEST_NORMAL_ENTRY.strftime("%H:%M"),
                     "reentry_trading_days": rules.REENTRY_TRADING_DAYS,
                     "max_trades_per_day": rules.MAX_TRADES_PER_DAY},
        "stock": stock_block, "intraday": intraday, "market": market, "sector": sector,
        "leader": leader, "discipline": discipline, "data_quality": data_quality,
        "timings": timings,
    })
    _log_context(log_tag, out)
    return out


def evaluate_and_save(db: Session, owner: str, req) -> dict:
    ctx = build_context(db, owner, req.stock_code, req.as_of, req.thesis_sector_id,
                        journal_id=req.journal_id, log_tag="evaluate")
    answers = req.manual_answers.model_dump()
    inp = {"intended_price": req.intended_price, "position_pct": req.position_pct,
           "planned_stop": req.planned_stop, "reason": req.reason, "answers": answers,
           "account_risk_budget_pct": req.account_risk_budget_pct, "stress_loss_pct": req.stress_loss_pct}
    res = rules.evaluate(ctx, inp)
    thesis = (ctx.get("sector") or {}).get("thesis") or {}
    row = PreTradeCheck(
        owner=owner, stock_code=req.stock_code, stock_name=(ctx.get("stock") or {}).get("name"),
        as_of=datetime.fromisoformat(ctx["as_of"]), mode=ctx["mode"],
        intended_price=req.intended_price, position_pct=req.position_pct, planned_stop=req.planned_stop,
        reason=req.reason or None, thesis_sector=thesis.get("name"),
        verdict=res["decision"]["verdict"], rule_version=rules.RULE_VERSION,
        facts_json=ctx, checks_json=_jsonable(res), manual_answers_json=answers,
        data_quality_json=ctx["data_quality"])
    db.add(row)
    db.commit()
    db.refresh(row)
    dec = res["decision"]
    logger.info("evaluate #%s %s %s verdict=%s 否决%d 未满足%d 警惕%d 未知%d", row.id, row.stock_code, row.mode,
                dec["verdict"], len(dec["vetoes"]), len(dec["unmet"]), len(dec["cautions"]), len(dec["unknowns"]))
    return row_to_response(row)


def row_to_response(row: PreTradeCheck) -> dict:
    checks = row.checks_json or {}
    return {"id": row.id, "mode": row.mode, "as_of": row.as_of, "stock_code": row.stock_code,
            "stock_name": row.stock_name, "modules": checks.get("modules") or [],
            "decision": checks.get("decision") or {}, "context": row.facts_json or {},
            "data_quality": row.data_quality_json or []}


# ── 后续走势：事后揭晓，**不回写 verdict** ────────────────────────────────────

def compute_outcome(db: Session, row: PreTradeCheck) -> dict:
    """
    as_of 之后发生了什么：+30 分钟、当日收盘、T+1、T+3。

    只在用户点「查看后续走势」时才算，存进 outcome_json；判定快照一个字都不改——
    复盘要看的是当时的决策质量，不是拿结果倒推当时对不对。
    """
    now = now_sh()
    stock = db.query(Stock).filter(Stock.code == row.stock_code).first()
    market = _market_of(row.stock_code, stock)
    facts = row.facts_json or {}
    base = row.intended_price or (facts.get("intraday") or {}).get("price")
    out: Dict[str, Any] = {"base_price": base, "computed_at": now.isoformat(timespec="seconds"), "notes": []}

    def ret(p):
        return round((p / base - 1) * 100, 2) if (p and base) else None

    close_at = datetime.combine(row.as_of.date(), time(15, 0))
    day = get_intraday_context(row.stock_code, market, min(close_at, now), live=False)
    later = [b for b in day.bars if b.dt > row.as_of]
    t30 = row.as_of + timedelta(minutes=30)
    if t30 <= now:
        p30 = [b for b in later if b.dt <= t30]
        out["after_30m"] = {"price": p30[-1].close if p30 else None, "ret": ret(p30[-1].close) if p30 else None}
    if later:
        out["intraday_high_after"] = max((b.high or b.close) for b in later)
        out["intraday_low_after"] = min((b.low or b.close) for b in later)
        out["mfe"], out["mae"] = ret(out["intraday_high_after"]), ret(out["intraday_low_after"])
    if close_at <= now and day.bars:
        out["day_close"] = {"price": day.bars[-1].close, "ret": ret(day.bars[-1].close)}
    if stock is not None:
        nxt = (db.query(StockDailySnapshot.date, StockDailySnapshot.close_price)
               .filter(StockDailySnapshot.stock_id == stock.id, StockDailySnapshot.date > row.as_of.date(),
                       StockDailySnapshot.close_price.isnot(None), StockDailySnapshot.is_settled.is_(True))
               .order_by(StockDailySnapshot.date).limit(3).all())
        if len(nxt) >= 1:
            out["t1"] = {"date": nxt[0][0].isoformat(), "price": nxt[0][1], "ret": ret(nxt[0][1])}
        if len(nxt) >= 3:
            out["t3"] = {"date": nxt[2][0].isoformat(), "price": nxt[2][1], "ret": ret(nxt[2][1])}
    else:
        out["notes"].append("股票不在库里，T+1 / T+3 收盘拿不到")
    out["notes"] += day.notes[:2]
    row.outcome_json, row.outcome_at = _jsonable(out), now
    db.commit()
    return row.outcome_json
