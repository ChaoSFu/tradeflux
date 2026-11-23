"""
每日复盘 · 买入识别 · 事实层（buy_recog_v2，2026-11-23）。规则在 buy_recognition_rules.py（纯函数）。

盘中走自己的接口 `GET /market-recap/live/buy-recognition`（v1 挂在 /live 里，v2 拆开：买入识别超时不拖慢
盘中跟踪，各自按自己的预算返回）；盘后研究走 `GET /market-recap/{date}/buy-recognition`（只摆可证的收盘事实，
盘中那几条标「盘中无法复核」）。买入检查 LIVE 评估复用这里的 `hard_risk`（跌停家数 + 最大空头两项）。

## 数据从哪来、怎么核

| 事实                 | 来源                                              | 核实 / 兜底                                    |
|----------------------|---------------------------------------------------|------------------------------------------------|
| 跌停家数             | 东财跌停池（按交易所 + 代码去重，剔除 ST / 北交所）| **逐只拿实时行情核「此刻仍封跌停」**；行情过期的只算进上界 |
| 对照口径             | 东财涨跌分布（含 ST、北交所）                     | 沪深两市都返回了才能当上界，否则只对照          |
| 最大空头             | 库里近 3 个已结算交易日日快照 + 跌停池封单额      | 分钟价（腾讯 5 日分钟）判止跌，查新鲜度        |
| 大盘                 | 四大指数分钟价 + 同期成交额（沪深两市按 bar 结束时刻对齐）| 只用已走完、没过期的分钟                 |
| 高标反馈             | 上一交易日的高标池（生命周期：连板中 / 刚断板 / 修复中）| 实时行情；两次观测比同一批票             |
| 主线                 | T-1 主升雷达状态 + 今天涨停 / 触板 × 关注板块成分 | 板块分时（东财 push2his）、成分股实时行情      |
| 带队                 | 涨停 / 炸板池首封时刻、分钟价（只在代表板块的成分里找）|                                           |

## 当天要记住的，存在 AppConfig（键 buy_recog_day:YYYY-MM-DD）

「封单锚」（当日首个可信跌停池快照里封单最大的票、之后出现的更重的）和「高标反馈观测」存进仓库通用的
AppConfig 键值表：刷新、服务重启、多个请求看到同一份。读不回来 → 封单锚显示「待核」（数据不足），不当作没有。
这不是复盘数据：盘中跟踪照旧不落任何复盘记录。

## 请求预算

不加轮询，跟盘中跟踪同一个刷新节奏。整份结果按「交易日 + 行情时间」缓存 RESULT_TTL_S 秒、同键并发合并
（多个标签页只算一次）。先取跌停池 + 少量行情把市场硬条件判出来：**跌停可信下界 >20（停手 · 空仓）就不再取成分股
和分时**；其他硬拦截照算主线（盯着用），但不补带队分时。整体最多等 BR_BUDGET_S 秒，没回来的那项这次数据不足。
"""
from __future__ import annotations

import json
import logging
import statistics
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from datetime import date, datetime
from time import monotonic
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models.app_config import AppConfig
from ..models.sector import Sector, StockSectorRelation
from ..models.stock import Stock, StockDailySnapshot
from . import buy_recognition_rules as R
from .eastmoney_fetcher import (
    SH_TZ, MinuteBar, _is_bj_code, exact_limit_price, fetch_board_minutes, fetch_minute_days_tencent,
    get_actual_limit_pct, market_int,
)
from .limit_up_detail_fetcher import fetch_limit_down_pool

logger = logging.getLogger(__name__)

BR_BUDGET_S = 9.0
RESULT_TTL_S = 10                # 同一行情时间的整份结果，10 秒内直接复用（行情本身 15 秒一更）
POOL_TTL_S = 30
MINUTE_TTL_S = 60
FACTS_TTL_S = 600
MAX_EVAL_GROUPS = 5
MAX_SHOWN = 3
MAX_MEMBERS_PER_SECTOR = 800     # 半导体概念在库里 457 只（2026-11-23 实测），400 会把大概念全挡成数据不足
MAX_MEMBER_QUOTES = 1000         # 一次刷新最多给成分股取这么多只行情；按活跃只数排在前面的组先用
MAX_MINUTE_PER_GROUP = 4
MAX_LEADER_GROUPS = 3
LIVE_OK_PHASES = ("trading", "lunch", "closing_auction", "closed")
HM_STATES = ("STREAKING", "BROKEN", "REPAIRING")
HM_STATE_ZH = {"STREAKING": "连板中", "BROKEN": "刚断板", "REPAIRING": "修复中"}
CORE_INDEX = ("000001", 1, "上证指数")
DAY_KEY = "buy_recog_day:{}"
HM_KEEP = 40

PHASE_NOTE = {
    "pre_open": "还没开盘：跌停池、指数分时都是上一交易日的，开盘后再判",
    "auction": "集合竞价中：竞价价格不是成交结果，开盘后再判",
    "auction_done": "竞价结束、等待开盘，开盘后再判",
    "non_trading": "今天休市，没有盘中观测",
    "stale": "行情时间没更新，没有此刻的盘中观测",
    "unknown": "行情时间未知，没有此刻的盘中观测",
}
ITEM_TITLES = (("limit_down", "跌停家数"), ("bear_3d", "最大空头·近 3 日领跌"), ("bear_seal", "最大空头·封单最重"),
               ("index", "大盘止跌放量"), ("highmark", "高标负反馈"))

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="buy-recog")


# ── 当日状态（AppConfig）：封单锚 + 高标观测 ─────────────────────────────────

def _empty_day() -> dict:
    return {"first_trusted_at": None, "none_at": None, "seal_locks": [], "hm": []}


def _load_day(db: Session, d: date) -> Tuple[dict, Optional[str]]:
    try:
        row = db.query(AppConfig).filter(AppConfig.key == DAY_KEY.format(d.isoformat())).first()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return _empty_day(), f"读库失败（{type(e).__name__}）"
    if row is None or not row.value:
        return _empty_day(), None
    try:
        st = json.loads(row.value)
        out = _empty_day()
        out.update({k: st.get(k) for k in out if k in st})
        if not isinstance(out["seal_locks"], list) or not isinstance(out["hm"], list):
            raise ValueError("格式不对")
        return out, None
    except Exception as e:  # noqa: BLE001
        return _empty_day(), f"存的当日状态读不懂（{type(e).__name__}）"


def _merge_day(a: dict, b: dict) -> dict:
    """两份当日状态合并（别的请求 / 进程先写了也不丢）：锁按代码并集取最早锁定的，时刻取早的，观测按时间并集。"""
    locks: Dict[str, dict] = {}
    for x in (a.get("seal_locks") or []) + (b.get("seal_locks") or []):
        if x["code"] not in locks or (x.get("locked_at") or "") < (locks[x["code"]].get("locked_at") or ""):
            locks[x["code"]] = x
    pick = lambda k: min((v for v in (a.get(k), b.get(k)) if v), default=None)  # noqa: E731
    hm = {o["ts"]: o for o in (a.get("hm") or []) + (b.get("hm") or [])}
    return {"first_trusted_at": pick("first_trusted_at"), "none_at": pick("none_at"),
            "seal_locks": sorted(locks.values(), key=lambda x: x.get("locked_at") or "")[:R.MAX_SEAL_LOCKS],
            "hm": [hm[k] for k in sorted(hm)][-HM_KEEP:]}


def _save_day(db: Session, d: date, st: dict) -> Optional[str]:
    key = DAY_KEY.format(d.isoformat())
    for _ in range(2):
        try:
            row = db.query(AppConfig).filter(AppConfig.key == key).with_for_update().first()
            cur = _empty_day()
            if row is not None and row.value:
                try:
                    cur.update(json.loads(row.value))
                except ValueError:
                    pass
            val = json.dumps(_merge_day(cur, st), ensure_ascii=False)
            if row is None:
                db.add(AppConfig(key=key, value=val))
            else:
                row.value = val
            db.commit()
            return None
        except IntegrityError:          # 别的请求同时插了这一行：重读合并再写一次
            db.rollback()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            logger.warning("买入识别：当日状态没存下来: %r", e)
            return f"没存下来（{type(e).__name__}）"
    return "没存下来（并发写冲突）"


def _seal_view(r: dict) -> dict:
    return {"code": r["code"], "name": r.get("name"), "market": r.get("market"),
            "seal_amount": r.get("seal_amount"), "down_days": r.get("down_days")}


def seal_anchor(db: Session, d: date, *, sealed: Sequence[dict], trusted: bool, at: str) -> dict:
    """
    封单锚：sealed = 此刻行情核过仍封跌停的跌停池行；trusted = 这次跌停池可信。第一次可信快照锁封单最大的那只；
    之后出现封单比已锁的都重的票，加进来重新核对（最多 MAX_SEAL_LOCKS 只）。锁过的开板离池照样跟踪。
    返回 {locks, anchor, none_at, first_trusted_at, error, save_error}。
    """
    st, err = _load_day(db, d)
    if err:
        return {"locks": [], "anchor": R.SEAL_UNRECOVERABLE, "error": err, "none_at": None, "first_trusted_at": None}
    changed = False
    if trusted:
        if not st["first_trusted_at"]:
            st["first_trusted_at"], changed = at, True
        rows = [r for r in sealed if r.get("seal_amount")]
        if rows:
            top = max(rows, key=lambda r: r["seal_amount"])
            locked = {x["code"] for x in st["seal_locks"]}
            if not st["seal_locks"]:
                st["seal_locks"].append({**_seal_view(top), "locked_at": at, "basis": f"{at} 首个可信跌停池快照里封单最大"})
                changed = True
            elif top["code"] not in locked and len(st["seal_locks"]) < R.MAX_SEAL_LOCKS and \
                    top["seal_amount"] > max(x.get("seal_amount") or 0 for x in st["seal_locks"]):
                st["seal_locks"].append({**_seal_view(top), "locked_at": at, "basis": f"{at} 出现更重的封单，重新核对"})
                changed = True
        elif not sealed and not st["seal_locks"] and not st["none_at"]:
            st["none_at"], changed = at, True
    save_err = _save_day(db, d, st) if changed else None
    if st["seal_locks"]:
        anchor = R.SEAL_LOCKED
    elif trusted and not sealed:
        anchor = R.SEAL_NONE
    else:
        anchor = R.SEAL_UNVERIFIED
    return {"locks": [dict(x) for x in st["seal_locks"]], "anchor": anchor, "none_at": at if anchor == R.SEAL_NONE else None,
            "first_trusted_at": st["first_trusted_at"], "error": None, "save_error": save_err}


def seal_codes(db: Session, d: date) -> List[str]:
    st, _ = _load_day(db, d)
    return [x["code"] for x in st["seal_locks"]]


def hm_observe(db: Session, d: date, market_dt: datetime, stats: dict) -> Tuple[Optional[dict], Optional[float], Optional[str]]:
    """
    记一次高标反馈观测（同一行情时间只记一次——重复刷新不算新观测），返回
    (行情时间至少早 HM_RECHECK_S 秒的上一次、按同一批票算的中位数, 今天最差的中位涨幅, 说明)。
    """
    st, err = _load_day(db, d)
    if err:
        return None, None, f"上一次观测读不回来（{err}）"
    ts = market_dt.isoformat(timespec="seconds")
    obs = st["hm"]
    worst = min((o["median_pct"] for o in obs if o.get("median_pct") is not None and o["ts"] != ts), default=None)
    older = [o for o in obs if (market_dt - datetime.fromisoformat(o["ts"])).total_seconds() >= R.HM_RECHECK_S]
    prev, note = None, None
    if older:
        o = older[-1]
        now_p, then_p = stats.get("pcts") or {}, o.get("pcts") or {}
        common = sorted(set(now_p) & set(then_p))
        need = max(R.HM_MIN_SAMPLE, R.HM_MIN_COVERAGE * (stats.get("sample") or 0))
        if len(common) >= need:
            prev = {"at": o["at"], "median_pct": round(statistics.median(then_p[c] for c in common), 2),
                    "median_now": round(statistics.median(now_p[c] for c in common), 2), "common": len(common)}
        else:
            note = f"上一次（{o['at']}）和这次都拿到行情的只有 {len(common)} 只，不是同一批票，不拿来比"
    if stats.get("median_pct") is not None and not any(o["ts"] == ts for o in obs):
        st["hm"] = obs + [{"ts": ts, "at": market_dt.strftime("%H:%M:%S"), "median_pct": stats["median_pct"],
                           "pcts": stats.get("pcts") or {}}]
        save_err = _save_day(db, d, st)
        if save_err:
            note = (note + "；" if note else "") + f"这次观测{save_err}"
    return prev, worst, note


# ── 小工具 ──────────────────────────────────────────────────────────────────

def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone(SH_TZ).replace(tzinfo=None) if dt.tzinfo else dt


def _today_pts(days: Optional[dict], d: date, upto: Optional[datetime]) -> Tuple[Optional[float], List[Tuple[datetime, float]]]:
    """腾讯 5 日分钟 → (昨收, 今天已走完的 (分钟结束时刻, 价))。"""
    if not days or d not in days:
        return None, []
    prev, bars = days[d]
    return prev, [(b.dt, b.close) for b in bars if upto is None or b.dt <= upto]


def _bars_pts(bars: Sequence[MinuteBar], d: date, upto: Optional[datetime]) -> List[Tuple[datetime, float]]:
    return [(b.dt, b.close) for b in bars if b.dt.date() == d and (upto is None or b.dt <= upto)]


def _fact(live, code: str, q, d: date, is_st: bool, name: Optional[str], market_dt: Optional[datetime]) -> dict:
    """live_fact + 时效：行情自带时间比此刻行情时间早 QUOTE_MAX_LAG_S 以上的当过期（新浪有的没带时间，只看日期）。"""
    f = live.live_fact(code, q, d, is_st, name)
    if f["status"] == "ok" and q is not None and q.trade_dt and market_dt:
        lag = (market_dt - _naive(q.trade_dt)).total_seconds()
        if lag > R.QUOTE_MAX_LAG_S:
            f.update(status="stale", note=f"行情停在 {_naive(q.trade_dt):%H:%M:%S}，比此刻晚 {lag / 60:.0f} 分钟")
    return f


class _Clock:
    def __init__(self, budget: float) -> None:
        self.t0, self.budget = monotonic(), budget

    def wait(self, f: Optional[Future], label: str) -> Tuple[Any, Optional[str]]:
        if f is None:
            return None, None
        try:
            return f.result(timeout=max(0.05, self.budget - (monotonic() - self.t0))), None
        except FutTimeout:
            return None, f"{label}这次没等到（{self.budget:g} 秒内没回，晚到的进缓存，下次刷新就有）"
        except Exception as e:  # noqa: BLE001
            logger.warning("买入识别 %s 取数失败: %r", label, e)
            return None, f"{label}取数失败（{type(e).__name__}: {str(e)[:80]}）"


def _done(v: Any) -> Future:
    f: Future = Future()
    f.set_result(v)
    return f


# ── 库里的事实（主线程查；按上一交易日缓存） ───────────────────────────────

def three_day_leader(db: Session, cal: Sequence[date], before: date) -> Optional[dict]:
    """近 3 个已结算交易日累计跌幅最大的票。范围：库里有日快照的非 ST、沪深票（跟踪范围，不是全市场）。"""
    days = [d for d in sorted(cal) if d < before][-3:]
    if len(days) < 3:
        return None
    rows = (db.query(Stock.code, Stock.name, Stock.market, StockDailySnapshot.date, StockDailySnapshot.pct_change)
            .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
            .filter(StockDailySnapshot.date.in_(days), Stock.is_st.is_(False),
                    StockDailySnapshot.pct_change.isnot(None)).all())
    acc: Dict[str, dict] = {}
    for code, name, mk, d, p in rows:
        if _is_bj_code(code) or "ST" in (name or ""):
            continue
        a = acc.setdefault(code, {"code": code, "name": name, "market": market_int(mk, code), "pcts": {}})
        a["pcts"][d] = p
    full = [a for a in acc.values() if len(a["pcts"]) == 3]
    if not full:
        return None
    for a in full:
        v = 1.0
        for d in days:
            v *= 1 + a["pcts"][d] / 100
        a["cum_pct"] = round((v - 1) * 100, 2)
    w = min(full, key=lambda a: a["cum_pct"])
    return {"code": w["code"], "name": w["name"], "market": w["market"], "cum_pct": w["cum_pct"],
            "days": [d.isoformat() for d in days], "scope_n": len(full),
            "basis": f"近 3 个已结算交易日（{days[0]:%m-%d}~{days[-1]:%m-%d}）累计 {w['cum_pct']:+.1f}% 最深"}


def highmark_sample(db: Session, ref: date) -> Optional[List[dict]]:
    """上一交易日高标池里，生命周期是连板中 / 刚断板 / 修复中的票（判不出当天的用最近一次有效状态）。"""
    from ..routers.leader_cycle import get_leader_cycle
    resp = get_leader_cycle(trade_date=ref, db=db)
    if resp.trade_date != ref:
        return None
    out = []
    for it in [*resp.running, *resp.broken]:
        st = it.lifecycle_state if it.lifecycle_state and it.lifecycle_state != "UNKNOWN" else it.last_valid_state
        if st in HM_STATES:
            out.append({"code": it.code, "name": it.name, "state": st})
    return out


def prev_mainlines(db: Session, ref: date) -> dict:
    """上一交易日（收盘）主升雷达里，处在点火 / 主升 / 加速 / 高潮 / 分歧的关注板块。state_date 不是 ref 就是回退了。"""
    from .sector_mainline_service import DIVERGENCE, IGNITION, MAINLINE_FAMILY, get_sector_mainline_state
    st = get_sector_mainline_state(db, as_of=ref)
    keep = [s for s in st.get("sectors") or [] if s["state"] in MAINLINE_FAMILY | {IGNITION}]
    sd = st.get("state_date")
    return {"state_date": sd.isoformat() if isinstance(sd, date) else sd,
            "sectors": [{"code": s["code"], "name": s["name"], "state": s["state"], "state_label": s["state_label"],
                         "trail": s.get("trail"), "origin": "repair" if s["state"] == DIVERGENCE else "continuation"}
                        for s in keep]}


def watched_sectors_of(db: Session, codes: Iterable[str]) -> Dict[str, List[tuple]]:
    """代码 → 它所在的关注板块 [(sid, code, name, type, stock_count)]。"""
    codes = list(set(codes))
    if not codes:
        return {}
    out: Dict[str, List[tuple]] = {}
    for c, sid, sc, sn, st, cnt in (db.query(Stock.code, Sector.id, Sector.code, Sector.name, Sector.sector_type,
                                             Sector.stock_count)
                                    .join(StockSectorRelation, StockSectorRelation.stock_id == Stock.id)
                                    .join(Sector, Sector.id == StockSectorRelation.sector_id)
                                    .filter(Stock.code.in_(codes), Sector.is_watched.is_(True))):
        out.setdefault(c, []).append((sid, sc, sn, st, cnt))
    return out


def sector_members(db: Session, sids: Sequence[int]) -> Dict[int, List[dict]]:
    out: Dict[int, List[dict]] = {}
    if not sids:
        return out
    for sid, code, name, is_st, mk, fs in (db.query(StockSectorRelation.sector_id, Stock.code, Stock.name, Stock.is_st,
                                                    Stock.market, Stock.float_shares)
                                           .join(Stock, Stock.id == StockSectorRelation.stock_id)
                                           .filter(StockSectorRelation.sector_id.in_(list(sids)))):
        out.setdefault(sid, []).append({"code": code, "name": name, "is_st": bool(is_st) or "ST" in (name or ""),
                                        "market": market_int(mk, code), "float_shares": fs})
    return out


# ── 硬风控：跌停家数 + 最大空头（盘中买入识别和买入检查 LIVE 评估共用这一份） ─────

def _pool_rows(db: Session, live, dt_res) -> Tuple[List[dict], dict, Dict[str, tuple]]:
    """跌停池原始行 → 按（交易所, 代码）去重、剔除 ST / 北交所。返回 (行, 剔除计数, 名称信息)。"""
    raw = dt_res[0] if dt_res else []
    info = live._names(db, [r.code for r in raw]) if raw else {}
    rows, seen, ex = [], set(), {"dup": 0, "excluded_st": 0, "excluded_bj": 0}
    for r in raw:
        k = (r.market, r.code)
        if k in seen:
            ex["dup"] += 1
            continue
        seen.add(k)
        if _is_bj_code(r.code):
            ex["excluded_bj"] += 1
            continue
        if (info.get(r.code, (None, False, 0))[1]) or "ST" in (r.name or ""):
            ex["excluded_st"] += 1
            continue
        rows.append({"code": r.code, "name": r.name, "market": r.market, "seal_amount": r.seal_amount,
                     "board_amount": r.board_amount, "down_days": r.down_days, "open_times": r.open_times,
                     "last_seal_time": r.last_seal_time.strftime("%H:%M:%S") if r.last_seal_time else None,
                     "pct": r.pct_change, "industry": r.em_industry})
    return rows, ex, info


def _market_hard(db: Session, live, *, data_d: date, cal: Sequence[date], market_dt: datetime, clock: _Clock,
                 f_dt: Future, f_breadth: Future, facts: Dict[str, dict], extra_codes: Sequence[str] = (),
                 notes: List[str]) -> dict:
    """
    跌停家数 + 最大空头两项。facts 会被补进这次取的行情（extra_codes 一起取，比如高标样本）。
    返回 {ld, bear_3d, bear_seal, rows, breadth, breadth_err, pool_trusted}。
    """
    upto, now_hms = market_dt, market_dt.strftime("%H:%M:%S")
    lead3, lead3_err = None, None
    try:
        lead3 = live._cached(("br-3d", data_d), FACTS_TTL_S, lambda: three_day_leader(db, cal, data_d))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        lead3_err = f"近 3 日领跌股没算出来（{type(e).__name__}）"
    f_lead3 = (_pool.submit(live._cached, ("br-min", lead3["code"], lead3["market"], data_d), MINUTE_TTL_S,
                            lambda: fetch_minute_days_tencent(lead3["code"], lead3["market"])) if lead3 else None)
    dt_res, dt_err = clock.wait(f_dt, "跌停池")
    rows, excluded, info = _pool_rows(db, live, dt_res)
    dt_tc = dt_res[1] if dt_res else None
    locked_before = seal_codes(db, data_d)
    need = [c for c in dict.fromkeys([r["code"] for r in rows] + locked_before + list(extra_codes)
                                     + ([lead3["code"]] if lead3 else [])) if c not in facts]
    more = live._names(db, [c for c in need if c not in info]) if need else {}
    info.update(more)
    for r in rows:
        if r.get("market") is not None and r["code"] in info:
            n, st, _ = info[r["code"]]
            info[r["code"]] = (n, st, int(r["market"]))
    f_q = _pool.submit(live._quotes, [(c, info[c][2]) for c in need]) if need else None
    quotes, q_err = clock.wait(f_q, "行情")
    if q_err:
        notes.append(q_err)
    names_pool = {r["code"]: r["name"] for r in rows}
    for c in need:
        n, st, _ = info[c]
        facts[c] = _fact(live, c, (quotes or {}).get(c), data_d, st, n or names_pool.get(c), market_dt)
    breadth, breadth_err = clock.wait(f_breadth, "涨跌分布")

    # ① 跌停家数
    verify = {}
    for r in rows:
        f = facts.get(r["code"]) or {}
        verify[r["code"]] = ("sealed" if f.get("status") == "ok" and f.get("limit") == "limit_down" else
                             "left" if f.get("status") == "ok" else
                             "stale" if f.get("status") == "stale" else "missing")
    hs_complete = bool(breadth) and {"000002", "399002"} <= set(breadth.get("markets_ok") or [])
    ld_meta = {"trade_date": data_d.isoformat(), "observed_at": now_hms,
               "source": "东财跌停池（非 ST、沪深，按交易所 + 代码去重）+ 实时行情逐只核",
               "fetched_age_s": live.cache_age(("br-dtpool", data_d)),
               "coverage": (f"跌停池 {len(rows)} 行（接口自报 {dt_tc if dt_tc is not None else '—'}"
                            + (f"，去重 {excluded['dup']}、剔除 ST {excluded['excluded_st']}、北交所 {excluded['excluded_bj']}"
                               if any(excluded.values()) else "") + f"），行情核上 "
                            f"{sum(1 for v in verify.values() if v in ('sealed', 'left'))} 只"),
               "compare_source": "东财涨跌分布（含 ST、沪深北）", "compare_error": breadth_err,
               "compare_markets": (breadth or {}).get("markets_ok")}
    ld = R.limit_down_item(pool_ok=dt_res is not None, pool_error=dt_err, pool_tc=dt_tc, rows=rows, verify=verify,
                           updown_ld=(breadth or {}).get("limit_down"), updown_complete=hs_complete,
                           market_time=market_dt, source_meta=ld_meta, excluded=excluded)
    pool_trusted = ld["facts"].get("upper_source") == "跌停池"

    # ② 最大空头：近 3 日领跌、封单最重，两项分开
    anc = seal_anchor(db, data_d, sealed=[r for r in rows if verify.get(r["code"]) == "sealed"],
                      trusted=pool_trusted, at=now_hms)
    t_info = live._names(db, [x["code"] for x in anc["locks"]] + ([lead3["code"]] if lead3 else []))
    f_tmin = {x["code"]: _pool.submit(live._cached, ("br-min", x["code"], x.get("market") or 0, data_d), MINUTE_TTL_S,
                                      lambda x=x: fetch_minute_days_tencent(x["code"], x.get("market") or 0))
              for x in anc["locks"] if not lead3 or x["code"] != lead3["code"]}
    if lead3:
        f_tmin[lead3["code"]] = f_lead3

    def judged(t: dict) -> dict:
        days, err = clock.wait(f_tmin.get(t["code"]), f"{t.get('name') or t['code']} 分钟")
        prev_close, pts = _today_pts(days, data_d, upto)
        f = facts.get(t["code"]) or {}
        price_now = f.get("price") if f.get("status") == "ok" else None
        prev_close = prev_close or f.get("prev_close")
        is_st = t_info.get(t["code"], (None, False, 0))[1]
        ldp = exact_limit_price(prev_close, get_actual_limit_pct(t["code"], is_st), False) if prev_close else None
        stale = None if err else R.minute_staleness(pts, market_dt)
        if err or stale:
            j = {"state": R.UNKNOWN, "evidence": err or stale}
        else:
            j = R.judge_bear(pts, prev_close=prev_close, ld_price=ldp, price_now=price_now)
        j["state_zh"] = R.BEAR_ZH[j["state"]]
        j["bars_to"] = R._hm(pts[-1][0]) if pts else None
        return {**t, "judge": j, "price": price_now,
                "pct": round((price_now / prev_close - 1) * 100, 2) if price_now and prev_close else None}

    b3_notes = []
    if lead3:
        b3_notes.append(f"范围：库里有日快照的非 ST 沪深票 {lead3['scope_n']} 只（跟踪范围，不是全市场）")
    bear_3d = R.bear_3d_item(judged({**lead3, "lock": "3d"}) if lead3 else None,
                             error=lead3_err or "近 3 个已结算交易日的日快照不全，领跌股没算出来", notes=b3_notes)
    seal_targets = [judged({**x, "lock": "seal", "basis": f"{x['basis']}（{(x.get('seal_amount') or 0) / 1e8:.2f} 亿）"})
                    for x in anc["locks"]]
    s_notes = []
    if anc.get("first_trusted_at"):
        s_notes.append(f"封单锚存在当日状态里，从 {anc['first_trusted_at']} 第一次可信跌停池快照起算；锁过的开板离池照样跟踪")
    if anc.get("save_error"):
        s_notes.append(f"这次的锁定{anc['save_error']}，下次刷新可能重新锁")
    if lead3 and any(x["code"] == lead3["code"] for x in anc["locks"]):
        s_notes.append(f"{lead3['name']} 同时是近 3 日领跌股")
    bear_seal = R.bear_seal_item(seal_targets, anchor=anc["anchor"], pool_trusted_now=pool_trusted,
                                 none_at=anc.get("none_at"), error=anc.get("error"), notes=s_notes)
    return {"ld": ld, "bear_3d": bear_3d, "bear_seal": bear_seal, "rows": rows, "breadth": breadth,
            "breadth_err": breadth_err, "pool_trusted": pool_trusted, "info": info}


def _session(live, db: Session, local_now: datetime) -> Tuple[dict, list, Optional[datetime], Optional[dict], Optional[str]]:
    from .trading_calendar import get_trading_days, last_weekday_before
    try:
        cal = get_trading_days(db, need_through=last_weekday_before(local_now.date())) or []
    except Exception:  # noqa: BLE001
        db.rollback()
        cal = []
    idx_quotes, idx_err = None, None
    try:
        idx_quotes = live._cached(("index",), live.QUOTE_TTL_S, live._index_quotes)
    except Exception as e:  # noqa: BLE001
        idx_err = f"指数行情取数失败（{type(e).__name__}）"
    market_dt = max((_naive(q.trade_dt) for q in (idx_quotes or {}).values() if q.trade_dt), default=None)
    return live.session_of(market_dt, local_now, cal), cal, market_dt, idx_quotes, idx_err


def hard_risk(db: Session, local_now: Optional[datetime] = None, budget_s: float = 6.0) -> dict:
    """
    买入检查 LIVE 评估用：此刻的跌停家数 + 最大空头两项（跟卡片同一套规则、同一份封单锚）。
    返回 {rule_version, as_of, phase, status, items}；不在盘中 → items 为空、status INSUFFICIENT。
    """
    from . import market_recap_live_service as live
    local_now = local_now or datetime.now(SH_TZ).replace(tzinfo=None)
    sess, cal, market_dt, _q, idx_err = _session(live, db, local_now)
    base = {"rule_version": R.RULE_VERSION, "as_of": _iso(market_dt), "phase": sess["phase"]}
    if sess["phase"] not in LIVE_OK_PHASES or sess["data_date"] is None or market_dt is None:
        return {**base, "status": R.INSUFFICIENT, "items": [],
                "reason": idx_err or PHASE_NOTE.get(sess["phase"], "没有此刻的盘中观测")}
    d = sess["data_date"]
    clock = _Clock(budget_s)
    f_dt = _pool.submit(live._cached, ("br-dtpool", d), POOL_TTL_S, lambda: fetch_limit_down_pool(d, timeout=8))
    f_breadth = _pool.submit(live._cached, ("breadth", d), live.MARKET_TTL_S, live._breadth)
    notes: List[str] = []
    h = _market_hard(db, live, data_d=d, cal=cal, market_dt=market_dt, clock=clock, f_dt=f_dt, f_breadth=f_breadth,
                     facts={}, notes=notes)
    items = [h["ld"], h["bear_3d"], h["bear_seal"]]
    st, worst = R.combine(items)
    return {**base, "status": st, "items": items, "notes": notes,
            "reason": "跌停家数、最大空头都通过" if st == R.PASS else f"{worst['title']}：{worst['reason']}"}


# ── 盘中 ────────────────────────────────────────────────────────────────────

def _skeleton(base: dict, note: str) -> dict:
    items = [R.item(k, t, R.INSUFFICIENT, "未开盘", note) for k, t in ITEM_TITLES]
    return {**base, "market": {"status": R.INSUFFICIENT, "status_zh": R.STATUS_ZH[R.INSUFFICIENT], "label": "数据不足",
                               "reason": note, "items": items},
            "mainlines": [], "mainline_note": note, "entry": None, "entry_reason": note, "focus": None, "notes": [note]}


def live_recognition(db: Session, local_now: Optional[datetime] = None) -> dict:
    """
    `GET /market-recap/live/buy-recognition`。自己定时段、取全市场几路（跟盘中跟踪共用缓存键，同键并发合并），
    整份结果按（交易日、行情时间、时段）缓存 RESULT_TTL_S 秒——多开几个标签页也只算一次、只记一次高标观测。
    跟用户无关（不读观察池 / 预案），所以不按用户分。
    """
    from . import market_recap_live_service as live
    local_now = local_now or datetime.now(SH_TZ).replace(tzinfo=None)
    sess, cal, market_dt, idx_quotes, idx_err = _session(live, db, local_now)
    key = ("br-result", sess["data_date"], _iso(market_dt), sess["phase"])

    def compute():
        try:
            return _compute(db, live, sess=sess, cal=cal, market_dt=market_dt, local_now=local_now,
                            idx_quotes=idx_quotes, idx_err=idx_err)
        except Exception as e:  # noqa: BLE001
            db.rollback()
            logger.exception("买入识别失败")
            return {"rule_version": R.RULE_VERSION, "error": f"买入识别没算出来（{type(e).__name__}: {str(e)[:120]}）"}
    out = live._cached(key, RESULT_TTL_S, compute)
    return {**out, "poll_seconds": live.poll_seconds(sess["phase"], local_now), "server_time": _iso(local_now)}


def _compute(db: Session, live, *, sess: dict, cal: Sequence[date], market_dt: Optional[datetime], local_now: datetime,
             idx_quotes: Optional[dict], idx_err: Optional[str]) -> dict:
    data_d, ref, phase = sess["data_date"], sess["ref_date"], sess["phase"]
    base = {"rule_version": R.RULE_VERSION, "mode": "live", "trade_date": data_d.isoformat() if data_d else None,
            "ref_date": ref.isoformat() if ref else None, "as_of": _iso(market_dt), "phase": phase,
            "thresholds": R.THRESHOLDS}
    if phase not in LIVE_OK_PHASES or data_d is None or ref is None or market_dt is None:
        return _skeleton(base, idx_err or PHASE_NOTE.get(phase, "没有此刻的盘中观测"))
    core_items: List[dict] = []
    try:
        core_items = (live._cores(db, ref, cal) or {}).get("items") or []
    except Exception:  # noqa: BLE001
        db.rollback()
    f_pools = _pool.submit(live._cached, ("pools", data_d), live.MARKET_TTL_S, lambda: live._limit_pools(data_d))
    f_breadth = _pool.submit(live._cached, ("breadth", data_d), live.MARKET_TTL_S, live._breadth)
    f_amount = _pool.submit(live._cached, ("amount", data_d), live.MARKET_TTL_S, live._amount)
    return build_live(db, sess=sess, cal=cal, market_dt=market_dt, local_now=local_now, core_items=core_items,
                      f_pools=f_pools, f_breadth=f_breadth, f_amount=f_amount, idx_quotes=idx_quotes)


def _volume(a: Optional[dict], a_err: Optional[str], data_d: date, market_dt: datetime) -> Tuple[Optional[dict], Optional[str]]:
    """同期成交额：今天的、沪深两市都有、截到同一个走完的 bar、没过期，才拿来算放量比。"""
    if not a:
        return None, a_err or "分钟成交额没拿到"
    if a.get("date") != data_d.isoformat():
        return None, f"分钟成交额是 {a.get('date')} 的，不是今天"
    if a.get("same_error"):
        return None, a["same_error"]
    if not a.get("same_amount") or not a.get("prev_same") or not a.get("same_as_of"):
        return None, "今天还没有两市都走完的 bar"
    hh, mm = (int(x) for x in a["same_as_of"].split(":"))
    lag = R.trade_lag_min(datetime.combine(data_d, datetime.min.time()).replace(hour=hh, minute=mm), market_dt)
    if lag is not None and lag > R.AMOUNT_MAX_LAG_MIN:
        return None, f"同期成交额截到 {a['same_as_of']}，比行情时间晚 {lag:.0f} 个交易分钟，过期了"
    return {"ratio": round(a["same_amount"] / a["prev_same"], 3), "amount": a["same_amount"], "prev_same": a["prev_same"],
            "prev_date": a.get("prev_date"), "same_as_of": a["same_as_of"], "source": a.get("source")}, None


def build_live(db: Session, *, sess: dict, cal: Sequence[date], market_dt: datetime, local_now: datetime,
               core_items: Sequence[dict], f_pools: Future, f_breadth: Future, f_amount: Future,
               idx_quotes: Optional[dict]) -> dict:
    """盘中买入识别。先判市场硬条件，再按结果决定还取不取成分股、分时。"""
    from . import market_recap_live_service as live
    data_d, ref, phase = sess["data_date"], sess["ref_date"], sess["phase"]
    base = {"rule_version": R.RULE_VERSION, "mode": "live", "trade_date": data_d.isoformat(),
            "ref_date": ref.isoformat(), "as_of": _iso(market_dt), "phase": phase, "thresholds": R.THRESHOLDS}
    clock = _Clock(BR_BUDGET_S)
    upto, now_hms = market_dt, market_dt.strftime("%H:%M:%S")
    notes: List[str] = []
    facts: Dict[str, dict] = {}

    # ── 第一波：跌停池、指数分钟（网络）；同时主线程查库 ──
    f_dt = _pool.submit(live._cached, ("br-dtpool", data_d), POOL_TTL_S, lambda: fetch_limit_down_pool(data_d, timeout=8))
    f_idx = {c: _pool.submit(live._cached, ("br-min", c, m, data_d), MINUTE_TTL_S,
                             lambda c=c, m=m: fetch_minute_days_tencent(c, m))
             for c, m, _ in live.INDEXES}
    hm_sample, prev_ml = None, {"state_date": None, "sectors": []}
    try:
        hm_sample = live._cached(("br-hm", ref), FACTS_TTL_S, lambda: highmark_sample(db, ref))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        notes.append(f"昨日高标样本读取失败（{type(e).__name__}）")
    try:
        prev_ml = live._cached(("br-ml", ref), FACTS_TTL_S, lambda: prev_mainlines(db, ref))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        notes.append(f"上一交易日主升雷达读取失败（{type(e).__name__}）")
    hm_codes = [x["code"] for x in hm_sample or []]

    # ── 市场硬条件：跌停家数、最大空头（跟买入检查共用） ──
    h = _market_hard(db, live, data_d=data_d, cal=cal, market_dt=market_dt, clock=clock, f_dt=f_dt,
                     f_breadth=f_breadth, facts=facts, extra_codes=hm_codes, notes=notes)
    ld, breadth = h["ld"], h["breadth"]

    stop_all = bool(ld.get("stop_all"))
    # 跌停可信下界 >20（停手 · 空仓）：不再取成分股、板块分时；否则**先把它们发出去**，跟下面等成交额 / 指数并行
    pools, pools_err, first_time, sealed_today, active_codes, names_today = None, None, {}, {}, set(), {}
    groups, members, ml_note, member_codes, need, m_info, f_mq, f_board, prev_snap = [], {}, None, [], [], {}, None, {}, {}
    if not stop_all:
        # 主线候选：今天涨停 + 触板 × 关注板块；加上 T-1 主升雷达的板块
        pools, pools_err = clock.wait(f_pools, "涨停 / 炸板池")
        first_time: Dict[str, str] = {}
        for x in (pools or {}).get("broken") or []:
            if x.get("first_time"):
                first_time[x["code"]] = x["first_time"]
        sealed_today = {x["code"]: x for x in (pools or {}).get("sealed") or []}
        for c, x in sealed_today.items():
            if x.get("first_time"):
                first_time[c] = x["first_time"]
        active_codes = set(first_time) | set(sealed_today)
        names_today = {x["code"]: x.get("name") for k in ("sealed", "broken") for x in (pools or {}).get(k) or []}
        groups, members, ml_note = [], {}, None
        ml_stale = prev_ml.get("state_date") and prev_ml["state_date"] != ref.isoformat()
        try:
            mem_of = watched_sectors_of(db, active_codes)
            prev_by_code = {s["code"]: s for s in prev_ml["sectors"]}
            sec_rows = {r.code: r for r in db.query(Sector).filter(Sector.code.in_(list(prev_by_code)))} if prev_by_code else {}
            cands: Dict[int, dict] = {}
            for c, secs in mem_of.items():
                for sid, scode, sname, stype, cnt in secs:
                    cands.setdefault(sid, {"sid": sid, "code": scode, "name": sname, "type": stype, "members_n": cnt,
                                           "active": set(), "origin": None})["active"].add(c)
            for scode, s in prev_by_code.items():
                row = sec_rows.get(scode)
                if row is None:
                    continue
                cd = cands.setdefault(row.id, {"sid": row.id, "code": scode, "name": s["name"], "type": row.sector_type,
                                               "members_n": row.stock_count, "active": set(), "origin": None})
                cd["origin"] = s["origin"]
                cd["prev_state"] = s["state_label"] + (f"（{prev_ml['state_date']} 的状态，{ref} 缺）" if ml_stale else "")
            pick = []
            for cd in cands.values():
                if cd["origin"] is None:
                    if len(cd["active"]) < R.NEW_MIN_ACTIVE:
                        continue
                    cd["origin"] = "new"
                pick.append(cd)
            groups = R.merge_groups(pick)[:MAX_EVAL_GROUPS]
            members = sector_members(db, [g["sid"] for g in groups])
            if not groups:
                ml_note = ("上一交易日没有处在主升 / 分歧的关注板块；今天的涨停 / 炸板池没拿到"
                           f"（{pools_err or '没有数据'}），新方向找不到——数据不足，不是没有主线"
                           if pools is None else
                           "上一交易日没有处在主升 / 分歧的关注板块，今天也没有关注板块集中涨停（≥3 只）")
        except Exception as e:  # noqa: BLE001
            db.rollback()
            ml_note = f"主线候选没算出来（{type(e).__name__}）"
            logger.warning("买入识别：主线候选失败: %r", e)
        if ml_stale:
            notes.append(f"{ref} 没有主升雷达状态，旧主线用的是 {prev_ml['state_date']} 的（回退）")

        # 第二波：成分股行情、板块分时（只发出去，后面再等）
        member_codes: List[str] = []
        for g in groups:
            ms = members.get(g["sid"], [])
            g["members_n"] = len(ms)
            if len(ms) > MAX_MEMBERS_PER_SECTOR:
                g["too_many"] = True
                continue
            if len(set(member_codes) | {m["code"] for m in ms}) > MAX_MEMBER_QUOTES:
                g["over_budget"] = True
                continue
            member_codes.extend(m["code"] for m in ms)
        member_codes = list(dict.fromkeys(member_codes))
        need = [c for c in member_codes if c not in facts]
        m_info = live._names(db, need) if need else {}
        f_mq = _pool.submit(live._quotes, [(c, m_info[c][2]) for c in need]) if need else None
        f_board = {g["sid"]: _pool.submit(live._cached, ("br-bd", g["code"], data_d), MINUTE_TTL_S,
                                          lambda code=g["code"]: fetch_board_minutes(code))
                   for g in groups if not g.get("too_many") and not g.get("over_budget")}
        prev_snap = live._prev_snaps(db, member_codes, ref)

    # ③ 大盘
    idx_items, idx_pts_of = [], {}
    for c, m, name in live.INDEXES:
        days, err = clock.wait(f_idx[c], f"{name} 分钟")
        prev_close, pts = _today_pts(days, data_d, upto)
        q = (idx_quotes or {}).get(c)
        price_now = q.price if q and q.trade_date == data_d and q.price else None
        stale = None if err else R.minute_staleness(pts, market_dt)
        j = (R.judge_index(pts, prev_close=prev_close or (q.prev_close if q else None), price_now=price_now)
             if not (err or stale) else {"ok": None, "evidence": err or stale})
        idx_items.append({"code": c, "name": name, "judge": j})
        idx_pts_of[c] = (prev_close, pts, err or stale)
    a, a_err = clock.wait(f_amount, "分钟成交额")
    vol, vol_err = _volume(a, a_err, data_d, market_dt)
    idx = R.index_item(idx_items, volume=vol, breadth=breadth, phase_ok=True, phase_note="", volume_error=vol_err)

    # ④ 高标反馈
    if hm_sample is None:
        hm = R.item("highmark", "高标负反馈", R.INSUFFICIENT, "样本不足", f"{ref} 没有生命周期快照，昨日高标样本拿不到")
    else:
        stats = R.highmark_stats([{**(facts.get(x["code"]) or {}), "code": x["code"],
                                   "name": x.get("name") or (facts.get(x["code"]) or {}).get("name")} for x in hm_sample])
        prev_obs, worst, hm_note = hm_observe(db, data_d, market_dt, stats)
        scope = f"{ref} 高标池（近 60 日最高连板 ≥4）里生命周期是连板中 / 刚断板 / 修复中的 {len(hm_sample)} 只"
        hm = R.highmark_item(stats, prev=prev_obs, worst_median=worst, phase_ok=True, phase_note="", scope=scope)
        hm["sample"] = [{"code": x["code"], "name": x.get("name"), "state_zh": HM_STATE_ZH[x["state"]],
                         "pct": (facts.get(x["code"]) or {}).get("pct"), "limit": (facts.get(x["code"]) or {}).get("limit"),
                         "status": (facts.get(x["code"]) or {}).get("status")}
                        for x in hm_sample]
        hm["memory_note"] = ("「上一次观测」存在当日状态里，按行情时间算间隔；同一行情时间重复刷新不算新观测，"
                             "两次比的是都拿到行情的同一批票") + (f"。{hm_note}" if hm_note else "")

    m_items = [ld, h["bear_3d"], h["bear_seal"], idx, hm]
    m_status, m_worst = R.combine(m_items)
    market = {"status": m_status, "status_zh": R.STATUS_ZH[m_status],
              "label": "市场过关" if m_status == R.PASS else (m_worst or {}).get("label"),
              "reason": "五项必需证据都满足" if m_status == R.PASS else f"{m_worst['title']}：{m_worst['reason']}",
              "items": m_items}
    hard_block = m_status == R.BLOCK
    out = {**base, "market": market, "prev_mainline_date": prev_ml.get("state_date"), "notes": notes,
           "stop_all": stop_all, "hard_block": hard_block,
           "memory_note": "当天要记住的（封单锚、高标观测）存在 AppConfig 当日状态里，刷新 / 重启 / 多个请求同一份"}
    if stop_all:
        entry, reason, _ = R.pick_entry(m_status, [], phase=phase)
        return {**out, "mainlines": [], "focus": None, "entry": entry, "entry_reason": reason,
                "mainline_note": f"跌停可信下界 {ld.get('count')} 家（>{R.LD_NO_NEW_MAX}）：停手 · 空仓，主线候选收起，不取成分股和分时"}

    mq, mq_err = clock.wait(f_mq, "成分股行情")
    if mq_err:
        notes.append(mq_err)
    for c in need:
        n, st, _ = m_info[c]
        facts[c] = _fact(live, c, (mq or {}).get(c), data_d, st, n or names_today.get(c), market_dt)

    # ── 主线：板块分时 × 上证分时 × 成分股广度（都按代表板块自己的成分） ──
    idx_prev, idx_pts, idx_bad = idx_pts_of[CORE_INDEX[0]]
    q_sh = (idx_quotes or {}).get(CORE_INDEX[0])
    idx_pct_now = round(q_sh.pct_change, 2) if q_sh and q_sh.trade_date == data_d and q_sh.pct_change is not None else None
    evals = []
    for g in groups:
        ms = members.get(g["sid"], [])
        own = {m["code"] for m in ms if not m["is_st"]} & active_codes
        g_out = {"sector": {"id": g["sid"], "code": g["code"], "name": g["name"], "type": g.get("type")},
                 "merged": g["merged"], "origin": g["origin"], "prev_state": g.get("prev_state"),
                 "active_today": len(own), "active_merged": len(g["active"]), "members": len(ms),
                 "meta": {"trade_date": data_d.isoformat(), "observed_at": now_hms,
                          "source": "东财板块分时（push2his）× 上证分时 × 成分股实时行情（等权，代表板块自己的成分）"}}
        if g.get("too_many") or g.get("over_budget"):
            g_out.update(status=R.INSUFFICIENT, label="数据不足",
                         reason=f"成分股 {len(ms)} 只，超过实时行情预算，没取" if g.get("too_many") else "本次行情预算用完，没取")
            evals.append((g, g_out))
            continue
        br = R.breadth_of([facts.get(m["code"]) or {} for m in ms if not m["is_st"]], sum(1 for m in ms if not m["is_st"]))
        b_res, b_err = clock.wait(f_board.get(g["sid"]), f"{g['name']} 分时")
        series = None
        if b_res and b_res[0] and idx_prev and idx_pts and not idx_bad:
            bpts = _bars_pts(b_res[1], data_d, upto)
            b_err = R.minute_staleness(bpts, market_dt)
            if not b_err:
                series = R.sector_series(bpts, b_res[0], idx_pts, idx_prev)
                if series is None:
                    b_err = "板块和上证分时对齐不到 10 分钟"
        elif not b_err:
            b_err = (f"上证分时不可用（{idx_bad}）" if idx_bad else
                     "板块分时还是空的" if b_res is not None else "板块分时没拿到")
        st, label, reason = R.mainline_judge(origin=g["origin"], br=br, series=series, series_error=b_err,
                                             idx_pct_now=idx_pct_now)
        if g["origin"] == "new" and st == R.CONFIRMED:
            label = "新方向确认走强"
        g_out.update(status=st, status_zh=R.MAINLINE_ZH[st], label=label, reason=reason, breadth=br, series=series,
                     series_error=b_err)
        evals.append((g, g_out))

    # ── 带队：只在代表板块的成分里找；市场硬拦截时不补分时 ──
    core_codes = {i["stock_code"] for i in core_items}
    lead_jobs: Dict[str, Future] = {}
    lead_groups = [(g, o) for g, o in evals if o["status"] in (R.CONFIRMED, R.PENDING, R.CANDIDATE)][:MAX_LEADER_GROUPS]
    for g, out_g in lead_groups:
        ms = {m["code"]: m for m in members.get(g["sid"], []) if not m["is_st"]}
        cands = []
        for c, m in ms.items():
            f, p = facts.get(c) or {}, prev_snap.get(c)
            in_core, lu20 = c in core_codes, (p.limit_up_days_20d if p is not None else 0) or 0
            if not (c in active_codes or in_core or lu20 >= 1):
                continue
            price = f.get("price")
            touch = (first_time.get(c) or "")[:5] or None
            cands.append({"code": c, "name": m["name"] or f.get("name"), "market": m["market"], "in_core": in_core,
                          "lu20": lu20, "board": (p.board_count if p is not None and p.is_limit_up else 0),
                          "price": price, "pct": f.get("pct"), "limit": f.get("limit"),
                          "float_mv": (m["float_shares"] * price) if m.get("float_shares") and price else None,
                          "touch_at": touch, "first_time": first_time.get(c), "pct5_at": None,
                          "broken_times": (sealed_today.get(c) or {}).get("broken_times"), "lead_at": touch})
        cands.sort(key=R.leader_sort_key)
        if not hard_block:
            # 补分时：没触板的拿 +5% 时刻；前排没封住的看承接；前排有没触板的，第一只触板的也补（启动事件要可比）
            no_touch = [c for c in cands if not c["touch_at"]][:2]
            top_touch = [c for c in cands if c["touch_at"]][:3]
            want = no_touch + [c for c in top_touch if c["limit"] != "sealed"][:2]
            if no_touch and top_touch:
                want.append(top_touch[0])
            for c in list({c["code"]: c for c in want}.values())[:MAX_MINUTE_PER_GROUP]:
                lead_jobs[c["code"]] = _pool.submit(live._cached, ("br-min", c["code"], c["market"], data_d), MINUTE_TTL_S,
                                                    lambda c=c: fetch_minute_days_tencent(c["code"], c["market"]))
        out_g["_cands"] = cands
    for g, out_g in evals:
        cands = out_g.pop("_cands", None)
        if cands is None:
            continue
        for c in cands:
            pts, prev_close, why = None, None, None
            if c["code"] in lead_jobs:
                days, why = clock.wait(lead_jobs[c["code"]], f"{c['name']} 分钟")
                prev_close, pts = _today_pts(days, data_d, upto)
                why = why or (R.minute_staleness(pts, market_dt) if pts else None)
                if why:
                    pts = None
            c["pct5_at"] = R.pct5_time(pts, prev_close)
            if not c["touch_at"]:
                c["lead_at"], c["lead_basis"] = c["pct5_at"], f"分时第一次 +{R.LEAD_PCT:g}%"
            else:
                c["lead_basis"] = "首封" if c["limit"] == "sealed" and not c.get("broken_times") else "首次触板"
            c["seal_txt"] = R.seal_state(c["limit"], c["broken_times"], bool(c["touch_at"]))
            c["support"], c["support_txt"] = R.support_of(limit=c["limit"], broken_times=c["broken_times"],
                                                          pts=pts, prev_close=prev_close)
            if c["support"] is None and hard_block and c["limit"] != "sealed":
                c["support_txt"] = "市场拦截中，没补分时，承接没核"
            elif c["support"] is None and why:
                c["support_txt"] = f"分时不可用（{why}），承接没法判断"
            c["weak"] = c["support"] is False or (c.get("pct") is not None and c["pct"] <= 0)
            c["weak_txt"] = (c["support_txt"] if c["support"] is False else f"此刻 {c['pct']:+.2f}%，翻绿了") if c["weak"] else None
        cands.sort(key=R.leader_sort_key)
        rep_codes = {m["code"] for m in members.get(g["sid"], []) if not m["is_st"]}
        peers = [{"code": c, "name": names_today.get(c) or (facts.get(c) or {}).get("name") or c, "at": first_time[c]}
                 for c in rep_codes if c in first_time]
        series = out_g.get("series") or {}
        lj = R.leader_judge(cands[:6], peers, round_start=series.get("round_start"))
        out_g["leader"] = lj
        out_g["leader_candidates"] = [{k: c.get(k) for k in ("code", "name", "lead_at", "lead_basis", "touch_at", "pct5_at",
                                                              "in_core", "lu20", "board", "pct", "limit", "float_mv", "price",
                                                              "seal_txt", "support", "support_txt", "weak", "weak_txt")}
                                      for c in cands[:4]]
        out_g["leader_note"] = ("带队和跟进只在这个板块自己的成分里找，跟进要在这一轮（板块分时起点之后）；"
                                "排序：先动时刻 → 昨日人气核心 → 连板高度 → 近 20 日涨停 → 流通市值小 → 价格低（不用龙头分）"
                                + ("；市场拦截中，没补分时" if hard_block else ""))
    order = {R.CONFIRMED: 0, R.PENDING: 1, R.CANDIDATE: 2, R.NOT_MET: 3, R.INSUFFICIENT: 4}
    shown = sorted((o for _, o in evals), key=lambda o: (order[o["status"]], -o["active_today"]))[:MAX_SHOWN]
    entry, entry_reason, focus = R.pick_entry(m_status, shown, phase=phase)
    if pools is None and pools_err:
        notes.append(f"涨停 / 炸板池没拿到（{pools_err}），新方向和首封时刻缺数据")
    return {**out, "mainlines": shown, "focus": focus, "mainline_note": ml_note, "entry": entry,
            "entry_reason": entry_reason}


# ── 盘后研究：只摆可证的收盘事实 ─────────────────────────────────────────────

NO_INTRADAY = "盘中无法复核"


def build_close(db: Session, d: date, cal: Sequence[date]) -> dict:
    """
    盘后研究：所选交易日的收盘事实。跌停家数、高标反馈、主升状态、首封先后是收盘后能证的；
    最大空头止跌、大盘止跌放量、主线分时证据、带队承接要盘中观测——没存盘中观测，一律「盘中无法复核」。
    不给结论、不给买入检查入口：历史日期不能串进实时结论。所选日期的主升状态是回退的，照实标出来。
    """
    from .strong_stock_service import _limit_moves_query
    cal = sorted(cal)
    prev = max((x for x in cal if x < d), default=None)
    base = {"rule_version": R.RULE_VERSION, "mode": "close", "trade_date": d.isoformat(),
            "ref_date": prev.isoformat() if prev else None, "as_of": None, "thresholds": R.THRESHOLDS,
            "entry": None, "entry_reason": "盘后研究只摆收盘事实，不提供买入检查入口", "focus": 0, "notes": []}
    # ① 跌停家数（收盘，非 ST 日快照）
    n_ld = _limit_moves_query(db, d, "limit_down").count()
    n_rows = db.query(func.count(StockDailySnapshot.id)).filter(StockDailySnapshot.date == d).scalar() or 0
    meta = {"trade_date": d.isoformat(), "source": "日快照（非 ST，收盘）", "observed_at": "收盘",
            "coverage": f"当天快照 {n_rows} 行"}
    if not n_rows:
        ld = R.item("limit_down", "跌停家数", R.INSUFFICIENT, "数据不足", f"{d} 没有日快照（还没同步或不是交易日）", meta=meta)
    else:
        st = R.BLOCK if n_ld > R.LD_PASS_MAX else R.PASS
        lab = "停手 · 空仓" if n_ld > R.LD_NO_NEW_MAX else "不开新仓" if n_ld > R.LD_PASS_MAX else "≤10 家"
        ld = R.item("limit_down", "跌停家数", st, lab, f"收盘跌停 {n_ld} 家（非 ST）", count=n_ld, meta=meta)
    # ② 最大空头：近 3 日领跌能锁，止没止跌要分时；封单最重要盘中跌停池，没存
    lead3 = three_day_leader(db, cal, d) if prev else None
    bear_3d = R.item("bear_3d", "最大空头·近 3 日领跌", R.INSUFFICIENT, NO_INTRADAY,
                     "止跌要看离开低点后的分时回踩，当天没有存下盘中观测" +
                     (f"；近 3 日领跌：{lead3['name']}（{lead3['basis']}）" if lead3 else ""),
                     targets=[{**lead3, "judge": {"state": R.UNKNOWN, "state_zh": NO_INTRADAY, "evidence": NO_INTRADAY}}]
                     if lead3 else [])
    bear_seal = R.item("bear_seal", "最大空头·封单最重", R.INSUFFICIENT, NO_INTRADAY,
                       "封单锚要盘中可信的跌停池快照，所选日期没有存", targets=[])
    # ③ 大盘：收盘涨跌、成交额比上一交易日（全天，不是同期）
    from ..models.market_index import IndexDailySnapshot, MarketBreadthDaily
    idx_rows = {r.index_code: r for r in db.query(IndexDailySnapshot)
                .filter(IndexDailySnapshot.date == d, IndexDailySnapshot.index_code.in_(["000001", "399001", "399006", "000688"]))}
    amt: Dict[date, Optional[float]] = {}
    try:
        amt = dict(db.query(MarketBreadthDaily.date, MarketBreadthDaily.deal_amount)
                   .filter(MarketBreadthDaily.date.in_([d] + ([prev] if prev else []))).all())
    except Exception:  # noqa: BLE001 —— 只是个对照值，缺了照样出别的
        db.rollback()
    idx = R.item("index", "大盘止跌放量", R.INSUFFICIENT, NO_INTRADAY,
                 "低点守没守住、同期比要分时，当天没有存下盘中观测",
                 closes=[{"code": c, "pct": (idx_rows[c].pct_change if c in idx_rows else None)} for c in ("000001", "399001", "399006", "000688")],
                 amount=amt.get(d), prev_amount=amt.get(prev) if prev else None)
    # ④ 高标反馈：昨日样本 × 当天收盘
    if prev is None:
        hm = R.item("highmark", "高标负反馈", R.INSUFFICIENT, "数据不足", "没有上一交易日")
    else:
        sample = highmark_sample(db, prev)
        if sample is None:
            hm = R.item("highmark", "高标负反馈", R.INSUFFICIENT, "样本不足", f"{prev} 没有生命周期快照")
        else:
            snaps = {s.code: x for s, x in db.query(Stock, StockDailySnapshot)
                     .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
                     .filter(StockDailySnapshot.date == d, Stock.code.in_([x["code"] for x in sample]))}
            facts = []
            for x in sample:
                s = snaps.get(x["code"])
                facts.append({"code": x["code"], "name": x["name"],
                              "status": "ok" if s is not None and s.pct_change is not None else "missing",
                              "pct": s.pct_change if s is not None else None,
                              "limit": ("limit_down" if s is not None and s.is_limit_down else None)})
            stats = R.highmark_stats(facts)
            scope = f"{prev} 高标池里连板中 / 刚断板 / 修复中的 {len(sample)} 只 × {d} 收盘"
            # 收盘就是最终结果，没有「再看一次」——按同一套口径把收盘当第二次观测
            hm = R.highmark_item(stats, prev={"at": "收盘", "median_pct": stats["median_pct"],
                                              "median_now": stats["median_pct"], "common": stats["quoted"]},
                                 worst_median=None, phase_ok=True, phase_note="", scope=scope)
            hm["reason"] = hm["reason"].split("；上一次")[0] + "（收盘口径）"
    items = [ld, bear_3d, bear_seal, idx, hm]
    market = {"status": R.INSUFFICIENT, "status_zh": "收盘事实", "label": "收盘事实 · 盘中无法复核",
              "reason": "最大空头止跌、大盘止跌放量要盘中观测，所选日期没有——不给「市场过关」结论", "items": items}
    # 主线：所选日收盘的主升状态 + 首封先后
    from ..models.limit_up_detail import LimitUpDailyDetail
    from .sector_mainline_service import MAINLINE_FAMILY, IGNITION, get_sector_mainline_state
    st = get_sector_mainline_state(db, as_of=d)
    sd = st.get("state_date")
    sd_s = sd.isoformat() if isinstance(sd, date) else sd
    fallback = f"（{sd_s} 的状态，{d} 没有）" if sd_s and sd_s != d.isoformat() else ""
    secs = [s for s in st.get("sectors") or [] if s["state"] in MAINLINE_FAMILY | {IGNITION}][:MAX_SHOWN]
    lu = {r.stock_code: r for r in db.query(LimitUpDailyDetail).filter(LimitUpDailyDetail.trade_date == d)}
    rows = {r.code: r for r in db.query(Sector).filter(Sector.code.in_([s["code"] for s in secs]))} if secs else {}
    mem = sector_members(db, [rows[s["code"]].id for s in secs if s["code"] in rows])
    mls = []
    for s in secs:
        row = rows.get(s["code"])
        ms = mem.get(row.id, []) if row else []
        firsts = sorted(((lu[m["code"]].first_limit_time, m) for m in ms if m["code"] in lu and lu[m["code"]].first_limit_time),
                        key=lambda x: x[0])
        leader = ({"status": "CLOSE", "label": "收盘口径：首封最早",
                   "reason": f"{firsts[0][1]['name']} {firsts[0][0]:%H:%M} 首封最早；承接要分时，{NO_INTRADAY}",
                   "leader": {"code": firsts[0][1]["code"], "name": firsts[0][1]["name"], "lead_at": f"{firsts[0][0]:%H:%M}"},
                   "followers": [{"code": m["code"], "name": m["name"], "at": f"{t:%H:%M:%S}"} for t, m in firsts[1:3]]}
                  if firsts else {"status": "NONE", "label": "当天没有成分股涨停", "reason": "", "leader": None, "followers": []})
        mls.append({"sector": {"id": row.id if row else None, "code": s["code"], "name": s["name"]},
                    "merged": [], "origin": None, "status": R.INSUFFICIENT, "status_zh": "收盘状态",
                    "label": f"收盘：{s['state_label']}{fallback}", "reason": f"{s['state_reason'] if 'state_reason' in s else ''}"
                    f"；先于指数走强、回踩后相对强度要分时，{NO_INTRADAY}".lstrip("；"),
                    "active_today": len(firsts), "members": len(ms), "leader": leader})
    if fallback:
        base["notes"].append(f"主升雷达在 {d} 没有状态，用的是 {sd_s} 的（回退）")
    return {**base, "market": market, "mainlines": mls,
            "mainline_note": None if mls else f"{d} 收盘没有处在点火 / 主升 / 加速 / 高潮 / 分歧的关注板块",
            "prev_mainline_date": sd_s}
