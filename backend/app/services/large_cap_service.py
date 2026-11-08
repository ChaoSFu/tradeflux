"""
大市值概览：从全市场日快照（LargeCapMarketDaily）现算门槛内的公司、行业 / 概念统计、
跨入跨出和放量。**只读库**，打开页面不会去抓远端。

## 统计母体

「当前达到市值门槛的大市值公司」，不是行业里的全部上市公司。银行行业 20 只 ≠ 银行业
20 家上市公司。5D/20D 这些是「当前成员回看近 N 日」，不是行业指数的历史收益。

## 不造分

只有家数、中位数、加权涨幅、成交额、量比、成交额倍数这类能复算的事实。没有强度分、
资金分、热度分。

## 跨入 / 跨出（见 compute_events）

两天都要有完整收盘快照、两天都要有这只票的有效总市值，才比较。门槛对两天用同一个值，
所以改配置永远不会被算成「今天有一批公司跨入」。
"""
from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from pathlib import Path

from sqlalchemy.orm import Session

from ..models.app_config import AppConfig
from ..models.large_cap import LargeCapMarketDaily, LargeCapStockBoards, LargeCapSyncBatch
from ..models.sector import Sector
from ..models.stock import Stock

UNIVERSE_SCOPE = "沪深主板 + 科创板 + 创业板（不含北交所）"

CONFIG_KEY = "large_cap_settings"
DEFAULTS = {
    "threshold_yi": 1000.0,          # 总市值门槛（亿元）
    "surge_amount_multiple": 2.0,    # 成交额 ÷ 前 20 日均额
    "surge_volume_ratio": 2.0,       # 量比
    "history_days": 20,              # 默认统计窗口（5/10/20/60）
    "include_st": False,
}
THRESHOLD_PRESETS = [500, 800, 1000, 1500, 2000]
WINDOWS = (5, 10, 20, 60)
AMOUNT_HISTORY_DAYS = 20
# 市值变化和股价涨跌差这么多（百分点）以上，就提示可能有股本变动 / 口径变化
CAP_PRICE_TOLERANCE = 0.5
# 今天的收盘快照最晚这个点该有了（15:30 日更跑完后接着同步）；之前只算「待同步」
EXPECT_TODAY_AFTER = dtime(16, 30)

# 东财行业层级：二级 → 一级（128 个二级 → 31 个一级）。2026-11-08 从东财 F10 采集：每个
# 二级行业取一只成分股，F10 板块列表里排在二级前一位的就是一级。层级很少变，所以随代码
# 发版；生产上 F10 被掐（11-08 每个请求都读超时）也不影响一级行业。F10 缓存里有更新的
# 映射时以缓存为准。
_L2_TO_L1_FILE = Path(__file__).parent / "data" / "em_industry_l2_to_l1.json"


def bundled_l2_to_l1() -> Dict[str, str]:
    try:
        return json.loads(_L2_TO_L1_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ── 配置 ────────────────────────────────────────────────────────────────────

def _load_custom(db: Session) -> dict:
    row = db.query(AppConfig).filter(AppConfig.key == CONFIG_KEY).first()
    if not row or not row.value:
        return {}
    try:
        v = json.loads(row.value)
    except ValueError:
        return {}
    return {k: v[k] for k in DEFAULTS if k in v}


def get_config(db: Session) -> dict:
    custom = _load_custom(db)
    return {**DEFAULTS, **custom, "defaults": DEFAULTS, "custom_keys": sorted(custom),
            "threshold_presets": THRESHOLD_PRESETS}


def _validate(key: str, v):
    if key == "include_st":
        if not isinstance(v, bool):
            raise ValueError("include_st 必须是 true / false")
        return v
    if key == "history_days":
        if v not in WINDOWS:
            raise ValueError(f"history_days 只能是 {WINDOWS}")
        return int(v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key} 必须是数字")
    if not (f > 0):
        raise ValueError(f"{key} 必须是正数")
    if key == "threshold_yi" and f > 1_000_000:
        raise ValueError("市值门槛不能超过 100 万亿")
    if key != "threshold_yi" and f > 100:
        raise ValueError(f"{key} 不能超过 100")
    return f


def set_config(db: Session, patch: dict, reset: bool = False) -> dict:
    """patch 里的值为 None 表示这一项恢复默认；reset=True 全部恢复默认。"""
    custom = {} if reset else _load_custom(db)
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        if v is None:
            custom.pop(k, None)
        else:
            val = _validate(k, v)
            if val == DEFAULTS[k]:
                custom.pop(k, None)
            else:
                custom[k] = val
    row = db.query(AppConfig).filter(AppConfig.key == CONFIG_KEY).first()
    if not custom:
        if row:
            db.delete(row)
    elif row:
        row.value = json.dumps(custom)
    else:
        db.add(AppConfig(key=CONFIG_KEY, value=json.dumps(custom)))
    db.commit()
    return get_config(db)


# ── 事实层 ──────────────────────────────────────────────────────────────────

@dataclass
class Obs:
    code: str
    name: Optional[str] = None
    market: Optional[str] = None
    total_cap: Optional[float] = None       # 亿元
    float_cap: Optional[float] = None
    price: Optional[float] = None
    pct: Optional[float] = None
    pct_5d: Optional[float] = None
    pct_10d: Optional[float] = None
    pct_20d: Optional[float] = None
    pct_60d: Optional[float] = None
    amount: Optional[float] = None          # 亿元
    turnover_rate: Optional[float] = None
    volume_ratio: Optional[float] = None
    industry: Optional[str] = None
    is_st: bool = False
    concepts: Optional[List[str]] = None    # None = 没存（<300 亿）/ 源没给；[] = 确实没有
    listing_date: Optional[date] = None


_OBS_COLS = (
    LargeCapMarketDaily.stock_code, LargeCapMarketDaily.stock_name, LargeCapMarketDaily.market,
    LargeCapMarketDaily.total_market_cap, LargeCapMarketDaily.float_market_cap,
    LargeCapMarketDaily.price, LargeCapMarketDaily.pct_change,
    LargeCapMarketDaily.pct_change_5d, LargeCapMarketDaily.pct_change_10d,
    LargeCapMarketDaily.pct_change_20d, LargeCapMarketDaily.pct_change_60d,
    LargeCapMarketDaily.amount, LargeCapMarketDaily.turnover_rate,
    LargeCapMarketDaily.volume_ratio, LargeCapMarketDaily.industry, LargeCapMarketDaily.is_st,
    LargeCapMarketDaily.concepts, LargeCapMarketDaily.listing_date,
)


def load_obs(db: Session, d: date) -> Dict[str, Obs]:
    """只取需要的列，不建 ORM 对象——一天五千多行，两天就一万多。"""
    out = {}
    for r in db.query(*_OBS_COLS).filter(LargeCapMarketDaily.trade_date == d):
        out[r[0]] = Obs(code=r[0], name=r[1], market=r[2], total_cap=r[3], float_cap=r[4],
                        price=r[5], pct=r[6], pct_5d=r[7], pct_10d=r[8], pct_20d=r[9],
                        pct_60d=r[10], amount=r[11], turnover_rate=r[12], volume_ratio=r[13],
                        industry=r[14], is_st=bool(r[15]), concepts=_json_list(r[16]),
                        listing_date=r[17])
    return out


def _json_list(text: Optional[str]) -> Optional[List[str]]:
    if not text:
        return None
    try:
        v = json.loads(text)
    except ValueError:
        return None
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else None


def membership(o: Obs, threshold: float, include_st: bool) -> Optional[bool]:
    """None = 总市值未知，判不了（不是「不在池里」）。"""
    if o.total_cap is None:
        return None
    if o.is_st and not include_st:
        return False
    return o.total_cap >= threshold


def _cap_change_pct(prev_cap: float, cap: float) -> float:
    return (cap / prev_cap - 1) * 100


def _event_row(p: Obs, o: Obs) -> dict:
    chg = _cap_change_pct(p.total_cap, o.total_cap)
    mismatch = o.pct is not None and abs(chg - o.pct) > CAP_PRICE_TOLERANCE
    return {"code": o.code, "name": o.name or p.name, "industry": o.industry or p.industry,
            "prev_cap": p.total_cap, "cap": o.total_cap, "cap_change_pct": round(chg, 2),
            "pct": o.pct, "pct_5d": o.pct_5d, "pct_20d": o.pct_20d, "amount": o.amount,
            "cap_price_mismatch": mismatch}


def compute_events(today: Dict[str, Obs], prev: Optional[Dict[str, Obs]],
                   threshold: float, include_st: bool) -> Optional[dict]:
    """
    prev 是**日历上前一个交易日**的完整快照；拿不到就传 None，返回 None（一个事件都不生成）。

      跨入      昨天有效市值 < 门槛，今天 ≥ 门槛
      跨出      昨天 ≥ 门槛，今天有效市值 < 门槛——跨出的票今天已经不在池里，照样列出
      首次观测  今天 ≥ 门槛，但昨天没有这只票的有效市值（新股、昨天停牌没给市值……）
      今日缺失  昨天 ≥ 门槛，今天没有有效市值——**不算跨出**
      ST 变化   一边被 ST 规则排除：成员变了，但不是市值跨越，单独列
    """
    if prev is None:
        return None
    entered, exited, first_seen, missing, st_change = [], [], [], [], []
    for code, o in today.items():
        if membership(o, threshold, include_st) is not True:
            continue
        p = prev.get(code)
        if p is None or p.total_cap is None:
            first_seen.append({"code": code, "name": o.name, "industry": o.industry,
                               "cap": o.total_cap, "pct": o.pct, "amount": o.amount,
                               "reason": "昨日快照里没有这只" if p is None else "昨日没有有效总市值"})
        elif p.total_cap < threshold:
            if p.is_st and not include_st:
                st_change.append({"code": code, "name": o.name, "change": "摘帽进入"})
            else:
                entered.append(_event_row(p, o))
        elif p.is_st and not include_st:
            st_change.append({"code": code, "name": o.name, "change": "摘帽进入"})
    for code, p in prev.items():
        if membership(p, threshold, include_st) is not True:
            continue
        o = today.get(code)
        if o is None or o.total_cap is None:
            missing.append({"code": code, "name": p.name, "industry": p.industry,
                            "prev_cap": p.total_cap,
                            "reason": "今日快照里没有这只" if o is None else "今日没有有效总市值"})
        elif o.total_cap < threshold:
            if o.is_st and not include_st:
                st_change.append({"code": code, "name": o.name, "change": "戴帽移出"})
            else:
                exited.append(_event_row(p, o))
        elif o.is_st and not include_st:
            st_change.append({"code": code, "name": o.name, "change": "戴帽移出"})
    entered.sort(key=lambda r: -r["cap"])
    exited.sort(key=lambda r: -r["prev_cap"])
    return {"entered": entered, "exited": exited, "first_observed": first_seen,
            "missing_today": missing, "st_change": st_change}


def amount_multiples(today: Dict[str, Optional[float]],
                     history: Sequence[Dict[str, Optional[float]]],
                     need: int = AMOUNT_HISTORY_DAYS) -> Dict[str, Tuple[Optional[float], Optional[float]]]:
    """
    {code: (前 need 日均额, 当日 ÷ 均额)}。history 是**前 need 个交易日**各一份 {code: 成交额}。

    少一天、某天没成交额、某天额为 0（停牌），这只票就是 (None, None)——不拿 19 天
    或 5 天的均值冒充「20 日」。
    """
    out: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    for code, amt in today.items():
        if len(history) < need:
            out[code] = (None, None)
            continue
        vals = [h.get(code) for h in history[-need:]]
        if any(v is None or v <= 0 for v in vals):
            out[code] = (None, None)
            continue
        avg = sum(vals) / need
        out[code] = (avg, amt / avg if amt is not None and avg > 0 else None)
    return out


def _median(vals: Iterable[Optional[float]]) -> Tuple[Optional[float], int]:
    v = [x for x in vals if x is not None]
    return (round(statistics.median(v), 2) if v else None), len(v)


def group_stats(members: Sequence[Obs], weights: Dict[str, float],
                multiples: Dict[str, Tuple[Optional[float], Optional[float]]],
                surge_multiple: float, surge_vr: float) -> dict:
    """
    一组公司的统计。每个数都带有效样本数 n——缺涨跌幅的不当 0% 算进去。

    weighted_pct：Σ(权重 × 今日涨幅) / Σ权重，权重由调用方给（前一交易日总市值，
    拿不到时才用当日市值），缺权重或缺涨幅的公司不参与。
    """
    pcts = [o.pct for o in members]
    valid_pct = [p for p in pcts if p is not None]
    num = den = 0.0
    n_w = 0
    contrib: List[Tuple[float, Obs]] = []
    for o in members:
        w = weights.get(o.code)
        if o.pct is None or w is None or w <= 0:
            continue
        num += w * o.pct
        den += w
        n_w += 1
        contrib.append((w * o.pct, o))
    top = None
    if den > 0 and contrib:
        c, t = max(contrib, key=lambda x: abs(x[0]))
        top = {"code": t.code, "name": t.name, "pp": round(c / den, 2)}
    med, n_pct = _median(pcts)
    amounts = [o.amount for o in members if o.amount is not None]
    have_mult = [multiples.get(o.code, (None, None))[1] for o in members]
    any_mult = any(m is not None for m in have_mult)
    out = {
        "count": len(members),
        "total_cap": round(sum(o.total_cap or 0 for o in members), 2),
        "up": sum(1 for p in valid_pct if p > 0),
        "flat": sum(1 for p in valid_pct if p == 0),
        "down": sum(1 for p in valid_pct if p < 0),
        "median_pct": med, "n_pct": n_pct,
        "weighted_pct": round(num / den, 2) if den > 0 else None, "n_weighted": n_w,
        # 对加权涨幅贡献最大（按绝对值）的那一只，单位是百分点：一组的加权涨幅是不是被
        # 一只巨头拉出来的，跟中位数、上涨家数对着看就知道
        "top_contributor": top,
        "amount": round(sum(amounts), 2) if amounts else None, "n_amount": len(amounts),
        "surge_multiple": (sum(1 for m in have_mult if m is not None and m >= surge_multiple)
                           if any_mult else None),
        "surge_volume_ratio": sum(1 for o in members
                                  if o.volume_ratio is not None and o.volume_ratio >= surge_vr),
    }
    for w in WINDOWS:
        m, n = _median(getattr(o, f"pct_{w}d") for o in members)
        out[f"median_{w}d"], out[f"n_{w}d"] = m, n
    return out


# ── 分类 ────────────────────────────────────────────────────────────────────

def parse_boards(text: Optional[str]) -> List[list]:
    if not text:
        return []
    try:
        v = json.loads(text)
    except ValueError:
        return []
    return [b for b in v if isinstance(b, list) and len(b) >= 3]


def l2_to_l1(cache: Dict[str, List[list]], industry_by_code: Dict[str, Optional[str]]) -> Dict[str, str]:
    """
    二级行业 → 一级行业。F10 里行业按一/二/三级排在最前面：找到名字等于这只票东财二级
    行业（clist f100）的那一项，排在它前一位的就是一级。对不上的票不参与（宁缺）。
    同一个二级被映射到不同一级时取多数。
    """
    votes: Dict[str, Counter] = defaultdict(Counter)
    for code, boards in cache.items():
        l2 = industry_by_code.get(code)
        if not l2:
            continue
        ranked = sorted((b for b in boards if b[2] is not None), key=lambda b: b[2])
        for i, b in enumerate(ranked):
            if b[1] == l2:
                if i > 0 and ranked[i - 1][2] == b[2] - 1:
                    votes[l2][ranked[i - 1][1]] += 1
                break
    return {l2: c.most_common(1)[0][0] for l2, c in votes.items()}


# ── 时效 ────────────────────────────────────────────────────────────────────

def expected_latest(cal: Sequence[date], now: datetime) -> Tuple[Optional[date], bool]:
    """
    (此刻应该已经有收盘快照的最近交易日, 今天的是否还在「待同步」时段)。
    今天是交易日、但还没到 16:30：期望的是上一个交易日，今天的算待同步，不算过期。
    """
    past = [d for d in cal if d <= now.date()]
    if not past:
        return None, False
    if past[-1] == now.date() and now.time() < EXPECT_TODAY_AFTER:
        pending = now.time() >= dtime(15, 0)
        return (past[-2] if len(past) > 1 else None), pending
    return past[-1], False


# ── 组装 ────────────────────────────────────────────────────────────────────

def _batch_dict(b: Optional[LargeCapSyncBatch]) -> Optional[dict]:
    if b is None:
        return None
    try:
        warnings = json.loads(b.warnings) if b.warnings else []
    except ValueError:
        warnings = [b.warnings]
    return {
        "id": b.id, "trade_date": b.trade_date, "source": b.source,
        "started_at": b.started_at, "finished_at": b.finished_at,
        "expected_count": b.expected_count, "observed_count": b.observed_count,
        "valid_market_cap_count": b.valid_market_cap_count,
        "pages_expected": b.pages_expected, "pages_ok": b.pages_ok,
        "is_complete": b.is_complete, "is_settled": b.is_settled, "applied": b.applied,
        "status": b.status, "warnings": warnings, "error": b.error,
    }


def _applied_batch(db: Session, d: date) -> Optional[LargeCapSyncBatch]:
    return (db.query(LargeCapSyncBatch)
            .filter(LargeCapSyncBatch.trade_date == d, LargeCapSyncBatch.applied.is_(True))
            .order_by(LargeCapSyncBatch.id.desc()).first())


def _stock_row(o: Obs, l1: str, l2: str, concepts: List[str],
               mult: Tuple[Optional[float], Optional[float]], prev_cap: Optional[float],
               has_detail: bool) -> dict:
    return {
        "code": o.code, "name": o.name, "market": o.market,
        # 本系统的个股详情只收录进过候选池的股票（11-08 生产：162 只里 47 只没有），
        # 没有的前端改链到东财行情页，不能点进一个「未找到该股票」
        "has_detail": has_detail, "listing_date": o.listing_date,
        "total_cap": o.total_cap, "float_cap": o.float_cap, "prev_cap": prev_cap,
        "price": o.price, "pct": o.pct, "pct_5d": o.pct_5d, "pct_10d": o.pct_10d,
        "pct_20d": o.pct_20d, "pct_60d": o.pct_60d,
        "amount": o.amount, "turnover_rate": o.turnover_rate, "volume_ratio": o.volume_ratio,
        "amount_avg_20d": round(mult[0], 4) if mult[0] is not None else None,
        "amount_multiple": round(mult[1], 2) if mult[1] is not None else None,
        "industry_l1": l1, "industry_l2": l2, "concepts": concepts,
    }


class NoSuchDate(LookupError):
    pass


def build_overview(db: Session, trade_date: Optional[date] = None,
                   threshold: Optional[float] = None, now: Optional[datetime] = None) -> dict:
    from .eastmoney_fetcher import SH_TZ
    from .trading_calendar import get_trading_days, last_n_trading_days, prev_trading_day

    cfg = get_config(db)
    T = float(threshold) if threshold is not None else float(cfg["threshold_yi"])
    if not T > 0:
        raise ValueError("市值门槛必须是正数")
    inc_st = bool(cfg["include_st"])
    now = now or datetime.now(SH_TZ).replace(tzinfo=None)

    latest_attempt = db.query(LargeCapSyncBatch).order_by(LargeCapSyncBatch.id.desc()).first()
    applied = [d for (d,) in db.query(LargeCapSyncBatch.trade_date)
               .filter(LargeCapSyncBatch.applied.is_(True), LargeCapSyncBatch.trade_date.isnot(None))
               .distinct().order_by(LargeCapSyncBatch.trade_date.desc()).limit(250)]
    base = {"universe_scope": UNIVERSE_SCOPE, "threshold_yi": T, "config": cfg,
            "latest_attempt": _batch_dict(latest_attempt), "available_dates": applied[:120]}
    if not applied:
        return {**base, "status": "no_data", "date": None,
                "message": "还没有任何一次完整的收盘快照"}
    D = trade_date or applied[0]
    if D not in applied:
        raise NoSuchDate(f"{D} 没有完整的收盘快照")

    cal = get_trading_days(db) or []
    exp_latest, pending_today = expected_latest(cal, now) if cal else (None, False)
    is_latest_view = D == applied[0]
    stale = bool(is_latest_view and exp_latest and applied[0] < exp_latest)

    batch = _applied_batch(db, D)
    # 日历没覆盖到 D（缓存只到上次刷新那天）时，「D 之前最近的一天」可能隔着好几个交易日，
    # 不能当成前一交易日——那正是「拿几天前的市值冒充昨日」
    cal_covers = D in set(cal)
    prev_d = prev_trading_day(cal, D) if cal_covers else None
    prev_batch = _applied_batch(db, prev_d) if prev_d else None
    today = load_obs(db, D)
    prev = load_obs(db, prev_d) if prev_batch else None

    members = [o for o in today.values() if membership(o, T, inc_st) is True]
    members.sort(key=lambda o: -(o.total_cap or 0))
    member_codes = {o.code for o in members}
    unknown_cap = sum(1 for o in today.values() if o.total_cap is None)

    events = compute_events(today, prev, T, inc_st)
    if prev is not None:
        weights = {c: p.total_cap for c, p in prev.items() if p.total_cap}
        weight_basis = "prev_cap"
    else:
        weights = {o.code: o.total_cap for o in members}
        weight_basis = "current_cap"

    # 20 日成交额：前 20 个交易日都得有完整快照
    hist_days = last_n_trading_days(cal, prev_d, AMOUNT_HISTORY_DAYS) if prev_d else []
    applied_set = set(applied)
    have_days = [d for d in hist_days if d in applied_set]
    history: List[Dict[str, Optional[float]]] = []
    if len(hist_days) == AMOUNT_HISTORY_DAYS and len(have_days) == AMOUNT_HISTORY_DAYS:
        by_day: Dict[date, Dict[str, Optional[float]]] = {d: {} for d in hist_days}
        for d, c, a in (db.query(LargeCapMarketDaily.trade_date, LargeCapMarketDaily.stock_code,
                                 LargeCapMarketDaily.amount)
                        .filter(LargeCapMarketDaily.trade_date.in_(hist_days),
                                LargeCapMarketDaily.stock_code.in_(member_codes))):
            by_day[d][c] = a
        history = [by_day[d] for d in hist_days]
    mults = amount_multiples({o.code: o.amount for o in members}, history)

    # 分类：二级行业、概念都是当天快照里的（clist f100 / f103，按日保存）；
    # 一级行业 = 二级按东财层级映射，F10 缓存里有更新的映射时以缓存为准
    cache_rows = db.query(LargeCapStockBoards.stock_code, LargeCapStockBoards.boards,
                          LargeCapStockBoards.status).all()
    cache = {c: parse_boards(b) for c, b, st_ in cache_rows if st_ == "ok"}
    l1_map = {**bundled_l2_to_l1(), **l2_to_l1(cache, {c: o.industry for c, o in today.items()})}
    # 概念板块按名字对 sectors 表，拿 BK 码和是否关注；表里没有的就用名字当键
    concept_sec: Dict[str, Sector] = {}
    for sec in db.query(Sector.code, Sector.name, Sector.is_watched).filter(Sector.sector_type == "concept"):
        if sec.name not in concept_sec or sec.is_watched:
            concept_sec[sec.name] = sec

    concept_name_by_code = {sec.code: name for name, sec in concept_sec.items()}

    def l1_of(o: Obs) -> str:
        return l1_map.get(o.industry, "一级未映射") if o.industry else "未分类"

    def concept_key(name: str) -> str:
        sec = concept_sec.get(name)
        return sec.code if sec is not None else f"name:{name}"

    def concepts_of(o: Obs) -> List[str]:
        return list(dict.fromkeys(concept_key(n) for n in (o.concepts or [])))

    ev_codes = {"entered": set(), "exited": set()}
    if events:
        ev_codes["entered"] = {e["code"] for e in events["entered"]}
        ev_codes["exited"] = {e["code"] for e in events["exited"]}

    def group(kind: str) -> List[dict]:
        buckets: Dict[str, List[Obs]] = defaultdict(list)
        ev_count: Dict[str, Counter] = defaultdict(Counter)
        key_of = {"industry_l1": lambda o: [l1_of(o)],
                  "industry_l2": lambda o: [o.industry or "未分类"],
                  "concept": concepts_of}[kind]
        for o in members:
            for k in key_of(o):
                buckets[k].append(o)
        for kind_ev, codes in ev_codes.items():
            for c in codes:
                o = today.get(c)
                if o is not None:
                    for k in key_of(o):
                        ev_count[k][kind_ev] += 1
        rows = []
        for k in set(buckets) | set(ev_count):
            ms = buckets.get(k, [])
            st = group_stats(ms, weights, mults, cfg["surge_amount_multiple"], cfg["surge_volume_ratio"])
            row = {"key": k, "name": k, **st, "members": [o.code for o in ms],
                   "entered": ev_count[k]["entered"] if events else None,
                   "exited": ev_count[k]["exited"] if events else None}
            if kind == "concept":
                name = k[5:] if k.startswith("name:") else concept_name_by_code.get(k, k)
                sec = concept_sec.get(name)
                row.update(name=name, watched=bool(sec and sec.is_watched))
            rows.append(row)
        rows.sort(key=lambda r: (-r["count"], -(r["total_cap"] or 0)))
        return rows

    groups = {"industry_l1": group("industry_l1"), "industry_l2": group("industry_l2"),
              "concept": group("concept")}

    summary = group_stats(members, weights, mults, cfg["surge_amount_multiple"], cfg["surge_volume_ratio"])
    summary.update(entered=len(events["entered"]) if events else None,
                   exited=len(events["exited"]) if events else None,
                   first_observed=len(events["first_observed"]) if events else None,
                   unknown_cap=unknown_cap, universe_count=len(today))

    linked = set(member_codes)
    if events:
        for k in ("entered", "exited", "first_observed", "missing_today", "st_change"):
            linked |= {e["code"] for e in events[k]}
    detail_codes = {c for (c,) in db.query(Stock.code).filter(Stock.code.in_(linked))} if linked else set()
    if events:
        for k in ("entered", "exited", "first_observed", "missing_today", "st_change"):
            for e in events[k]:
                e["has_detail"] = e["code"] in detail_codes

    stocks = [_stock_row(o, l1_of(o), o.industry or "未分类", concepts_of(o),
                         mults.get(o.code, (None, None)),
                         prev[o.code].total_cap if prev and o.code in prev else None,
                         o.code in detail_codes)
              for o in members]

    surges = [s for s in stocks
              if (s["amount_multiple"] is not None and s["amount_multiple"] >= cfg["surge_amount_multiple"])
              or (s["volume_ratio"] is not None and s["volume_ratio"] >= cfg["surge_volume_ratio"])]
    surges.sort(key=lambda s: (-(s["amount_multiple"] or 0), -(s["volume_ratio"] or 0)))

    classification = {
        "members": len(members),
        "with_concept": sum(1 for o in members if o.concepts),
        "concept_unknown": sum(1 for o in members if o.concepts is None),
        "with_industry_l2": sum(1 for o in members if o.industry),
        "with_industry_l1": sum(1 for o in members if o.industry and o.industry in l1_map),
        "note": "二级行业和概念取当天快照里的东财分类（随快照按日保存，回看历史就是当时的分类）；"
                "一级行业按东财行业层级由二级映射。概念存在多重归属，各概念的家数和市值不能相加。",
    }

    # warnings = 数据有问题、要人去看；notes = 正常的状态说明（第一天、历史还在攒）。
    # 混在一个黄框里，真出问题时反而看不出来
    warnings: List[str] = []
    notes: List[str] = []
    events_reason: Optional[str] = None
    if stale:
        warnings.append(f"最新收盘快照是 {applied[0]}，{exp_latest} 的还没同步成功")
    if pending_today and is_latest_view and applied[0] < now.date():
        notes.append("今天的收盘快照在盘后日更之后同步（约 15:45），现在显示的是上一交易日")
    if prev_d is None:
        why = (f"交易日历没覆盖到 {D}（缓存止于 {cal[-1]}）" if cal and not cal_covers
               else "交易日历不可用")
        events_reason = f"{why}，无法确定前一交易日"
        warnings.append(f"{events_reason}：跨入跨出不生成，加权涨幅改用当日市值权重")
    elif prev_batch is None:
        if D == applied[-1]:
            events_reason = "这是第一份收盘快照，跨入 / 跨出从下一个交易日起才有"
            notes.append(f"{events_reason}；今日加权涨幅暂用当日市值权重")
        else:
            events_reason = f"前一交易日 {prev_d} 没有完整快照，不拿更早的市值冒充昨日"
            warnings.append(f"{events_reason}：跨入跨出不生成，加权涨幅改用当日市值权重")
    if len(have_days) < AMOUNT_HISTORY_DAYS:
        notes.append(f"20 日成交额倍数还在攒历史（{len(have_days)}/{AMOUNT_HISTORY_DAYS} 个交易日），"
                     "放量先按量比看")
    if batch is not None and batch.source != "eastmoney_clist":
        warnings.append("这一天的快照来自腾讯行情备用源：没有 5/10/20/60 日涨跌幅，行业沿用之前的东财分类")
    if classification["concept_unknown"]:
        warnings.append(f"{classification['concept_unknown']} 只没有概念数据"
                        "（快照只给总市值 ≥300 亿的存概念，或那天走的备用源），不在概念统计里")
    unmapped = len(members) - classification["with_industry_l1"]
    if unmapped:
        warnings.append(f"{unmapped} 只的一级行业没映射上（二级行业不受影响）")

    return {
        **base, "status": "ok", "date": D,
        "batch": _batch_dict(batch), "is_stale": stale, "pending_today": pending_today,
        "expected_latest": exp_latest,
        "prev_date": prev_d, "prev_available": prev_batch is not None,
        "weight_basis": weight_basis,
        "amount_history": {"required": AMOUNT_HISTORY_DAYS, "available": len(have_days)},
        "summary": summary, "groups": groups,
        "events": ({**events, "available": True, "reason": None} if events else
                   {"available": False, "reason": events_reason, "entered": [], "exited": [],
                    "first_observed": [], "missing_today": [], "st_change": []}),
        "volume_surges": surges, "stocks": stocks,
        "classification": classification, "warnings": warnings, "notes": notes,
    }
