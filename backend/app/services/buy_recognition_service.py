"""
每日复盘 · 买入识别 · 事实层（buy_recog_v1，2026-11-23）。规则在 buy_recognition_rules.py（纯函数）。

盘中挂在 `GET /market-recap/live` 的 `buy_recognition`；盘后研究走
`GET /market-recap/{date}/buy-recognition`（只摆可证的收盘事实，盘中那几条标「盘中无法复核」）。

## 数据从哪来、怎么核

| 事实                 | 来源                                              | 核实 / 兜底                                    |
|----------------------|---------------------------------------------------|------------------------------------------------|
| 跌停家数             | 东财跌停池（非 ST、沪深，跟系统跌停名单同范围）   | **逐只拿实时行情核「此刻仍封跌停」**：开盘前接口给的是上一交易日名单、qdate 却写今天 |
| 对照口径             | 东财涨跌分布（含 ST、北交所）                     | 只对照；它都 ≤10 时兜底                        |
| 最大空头             | 库里近 3 个交易日日快照 + 跌停池封单额            | 分钟价（腾讯 5 日分钟）判止跌                  |
| 大盘                 | 四大指数分钟价 + 同期成交额（新浪 / 东财分钟）    | 只用已走完的分钟                               |
| 高标反馈             | 上一交易日的高标池（生命周期：连板中 / 刚断板 / 修复中）| 实时行情                                 |
| 主线                 | T-1 主升雷达状态 + 今天涨停 / 触板 × 关注板块成分 | 板块分时（东财 push2his）、成分股实时行情      |
| 带队                 | 涨停 / 炸板池首封时刻、分钟价                     |                                                |

## 两处要「记住今天」的，放在服务进程内存里

盘中跟踪按设计**一行不写库**。「当日首个可信跌停池快照里封单最大的票」和「高标反馈上一次观测」
都只记在进程内存：服务重启从重启后第一次重新算；早于第一次打开页面就开板离池的票看不到——卡片上照实写。

## 请求预算

沿用盘中跟踪的刷新节奏（交易时段 30 秒），不加轮询。全市场两路 30 秒缓存、分钟 60 秒缓存、
行情按只 15 秒缓存（跟盘中跟踪共用）。先用库里的数挑板块（≤5 组）和少量个股（每组 ≤3 只补分时），再补分钟。
整体最多等 BR_BUDGET_S 秒，没回来的那项这次标数据不足（晚到的进缓存，下次刷新就有）。
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutTimeout
from datetime import date, datetime, timedelta
from time import monotonic
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models.sector import Sector, StockSectorRelation
from ..models.stock import Stock, StockDailySnapshot
from . import buy_recognition_rules as R
from .eastmoney_fetcher import (
    MinuteBar, _is_bj_code, exact_limit_price, fetch_board_minutes, fetch_minute_days_tencent,
    get_actual_limit_pct, market_int,
)
from .limit_up_detail_fetcher import fetch_limit_down_pool

logger = logging.getLogger(__name__)

BR_BUDGET_S = 9.0
POOL_TTL_S = 30
MINUTE_TTL_S = 60
FACTS_TTL_S = 600
MAX_EVAL_GROUPS = 5
MAX_SHOWN = 3
MAX_MEMBERS_PER_SECTOR = 800     # 半导体概念在库里 457 只（2026-11-23 实测），400 会把大概念全挡成数据不足
MAX_MEMBER_QUOTES = 1000         # 一次刷新最多给成分股取这么多只行情；按活跃只数排在前面的组先用
MAX_MINUTE_PER_GROUP = 3
LIVE_OK_PHASES = ("trading", "lunch", "closing_auction", "closed")
HM_STATES = ("STREAKING", "BROKEN", "REPAIRING")
HM_STATE_ZH = {"STREAKING": "连板中", "BROKEN": "刚断板", "REPAIRING": "修复中"}
CORE_INDEX = ("000001", 1, "上证指数")

PHASE_NOTE = {
    "pre_open": "还没开盘：跌停池、指数分时都是上一交易日的，开盘后再判",
    "auction": "集合竞价中：竞价价格不是成交结果，开盘后再判",
    "auction_done": "竞价结束、等待开盘，开盘后再判",
    "non_trading": "今天休市，没有盘中观测",
    "stale": "行情时间没更新，没有此刻的盘中观测",
    "unknown": "行情时间未知，没有此刻的盘中观测",
}

_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="buy-recog")


# ── 进程内存：当天要记住的两件事 ─────────────────────────────────────────────

class _DayState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.day: Optional[date] = None
        self.seal_locks: List[dict] = []
        self.hm: List[dict] = []
        self.first_seen: Optional[str] = None

    def _roll(self, d: date) -> None:
        if self.day != d:
            self.day, self.seal_locks, self.hm, self.first_seen = d, [], [], None

    def reset(self) -> None:
        with self.lock:
            self.day, self.seal_locks, self.hm, self.first_seen = None, [], [], None

    def seal_targets(self, d: date, sealed: Sequence[dict], at: str, trusted: bool) -> List[dict]:
        """
        sealed = 此刻行情核过仍封跌停的跌停池行（trusted=False 时只读已锁的）。第一次可信快照锁封单
        最大的那只；之后出现封单比已锁的都重的票，加进来重新核对（最多 3 只）。开板离池的照样跟踪。
        """
        with self.lock:
            self._roll(d)
            if not trusted:
                return [dict(x) for x in self.seal_locks]
            self.first_seen = self.first_seen or at
            rows = [r for r in sealed if r.get("seal_amount")]
            if rows:
                top = max(rows, key=lambda r: r["seal_amount"])
                locked = {x["code"] for x in self.seal_locks}
                if not self.seal_locks:
                    self.seal_locks.append({**_seal_view(top), "locked_at": at,
                                            "basis": f"{at} 首个可信跌停池快照里封单最大"})
                elif top["code"] not in locked and len(self.seal_locks) < 3 and \
                        top["seal_amount"] > max(x["seal_amount"] for x in self.seal_locks):
                    self.seal_locks.append({**_seal_view(top), "locked_at": at,
                                            "basis": f"{at} 出现更重的封单，重新核对"})
            return [dict(x) for x in self.seal_locks]

    def hm_observe(self, d: date, now: datetime, median: Optional[float]) -> Tuple[Optional[dict], Optional[float]]:
        """记一次高标反馈；返回 (≥HM_RECHECK_S 秒前最近的一次, 今天最差的中位涨幅)。"""
        with self.lock:
            self._roll(d)
            prev = next((x for x in reversed(self.hm)
                         if (now - x["ts"]).total_seconds() >= R.HM_RECHECK_S), None)
            worst = min((x["median_pct"] for x in self.hm), default=None)
            if median is not None:
                self.hm.append({"ts": now, "at": now.strftime("%H:%M:%S"), "median_pct": median})
                self.hm = self.hm[-240:]
            return ({"at": prev["at"], "median_pct": prev["median_pct"]} if prev else None), worst


_DAY = _DayState()


def _seal_view(r: dict) -> dict:
    return {"code": r["code"], "name": r.get("name"), "market": r.get("market"),
            "seal_amount": r.get("seal_amount"), "down_days": r.get("down_days")}


# ── 小工具 ──────────────────────────────────────────────────────────────────

def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


def _today_pts(days: Optional[dict], d: date, upto: Optional[datetime]) -> Tuple[Optional[float], List[Tuple[datetime, float]]]:
    """腾讯 5 日分钟 → (昨收, 今天已走完的 (分钟结束时刻, 价))。"""
    if not days or d not in days:
        return None, []
    prev, bars = days[d]
    return prev, [(b.dt, b.close) for b in bars if upto is None or b.dt <= upto]


def _bars_pts(bars: Sequence[MinuteBar], d: date, upto: Optional[datetime]) -> List[Tuple[datetime, float]]:
    return [(b.dt, b.close) for b in bars if b.dt.date() == d and (upto is None or b.dt <= upto)]


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
            "basis": f"近 3 个交易日（{days[0]:%m-%d}~{days[-1]:%m-%d}）累计 {w['cum_pct']:+.1f}% 最深"}


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
    """上一交易日（收盘）主升雷达里，处在点火 / 主升 / 加速 / 高潮 / 分歧的关注板块。"""
    from .sector_mainline_service import DIVERGENCE, IGNITION, MAINLINE_FAMILY, get_sector_mainline_state
    st = get_sector_mainline_state(db, as_of=ref)
    keep = [s for s in st.get("sectors") or [] if s["state"] in MAINLINE_FAMILY | {IGNITION}]
    return {"state_date": st.get("state_date"),
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


# ── 盘中 ────────────────────────────────────────────────────────────────────

def _skeleton(base: dict, note: str) -> dict:
    items = [R.item(k, t, R.INSUFFICIENT, "未开盘", note) for k, t in
             (("limit_down", "跌停家数"), ("bear", "最大空头"), ("index", "大盘止跌放量"), ("highmark", "高标负反馈"))]
    return {**base, "market": {"status": R.INSUFFICIENT, "status_zh": R.STATUS_ZH[R.INSUFFICIENT], "label": "数据不足",
                               "reason": note, "items": items},
            "mainlines": [], "mainline_note": note, "entry": None, "notes": [note]}


def build_live(db: Session, *, sess: dict, cal: Sequence[date], market_dt: Optional[datetime], local_now: datetime,
               facts: Dict[str, dict], core_items: Sequence[dict], pools: Optional[dict], pools_err: Optional[str],
               breadth: Optional[dict], breadth_err: Optional[str], amount: Optional[dict], amount_err: Optional[str],
               idx_quotes: Optional[dict]) -> dict:
    """盘中买入识别。facts = 盘中跟踪已取好的个股事实（会被补进新取的票）。"""
    from . import market_recap_live_service as live
    data_d, ref, phase = sess["data_date"], sess["ref_date"], sess["phase"]
    base = {"rule_version": R.RULE_VERSION, "mode": "live", "trade_date": data_d.isoformat() if data_d else None,
            "ref_date": ref.isoformat() if ref else None, "as_of": _iso(market_dt), "phase": phase,
            "thresholds": R.THRESHOLDS}
    if phase not in LIVE_OK_PHASES or data_d is None or ref is None:
        return _skeleton(base, PHASE_NOTE.get(phase, "没有此刻的盘中观测"))
    clock = _Clock(BR_BUDGET_S)
    upto = market_dt
    now_hms = market_dt.strftime("%H:%M:%S") if market_dt else local_now.strftime("%H:%M:%S")
    notes: List[str] = []

    # ── 第一波：跌停池、指数分钟（网络）；同时主线程查库 ──
    f_dt = _pool.submit(live._cached, ("br-dtpool", data_d), POOL_TTL_S, lambda: fetch_limit_down_pool(data_d, timeout=8))
    f_idx = {c: _pool.submit(live._cached, ("br-min", c, m, data_d), MINUTE_TTL_S,
                             lambda c=c, m=m: fetch_minute_days_tencent(c, m))
             for c, m, _ in live.INDEXES}
    lead3 = None
    try:
        lead3 = live._cached(("br-3d", ref), FACTS_TTL_S, lambda: three_day_leader(db, cal, data_d))
    except Exception as e:  # noqa: BLE001
        db.rollback()
        notes.append(f"近 3 日领跌股没算出来（{type(e).__name__}）")
    f_lead3 = (_pool.submit(live._cached, ("br-min", lead3["code"], lead3["market"], data_d), MINUTE_TTL_S,
                            lambda: fetch_minute_days_tencent(lead3["code"], lead3["market"])) if lead3 else None)
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

    # 主线候选：今天涨停 + 触板 × 关注板块；加上 T-1 主升雷达的板块
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
            cd["origin"], cd["prev_state"] = s["origin"], s["state_label"]
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
    hm_codes = [x["code"] for x in hm_sample or []]
    core_codes = {i["stock_code"] for i in core_items}
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
    prev_snap = live._prev_snaps(db, list(set(member_codes) | set(hm_codes)), ref)

    # ── 第二波：跌停池成员、高标样本、板块成分的行情；锁定标的、板块的分钟 ──
    dt_res, dt_err = clock.wait(f_dt, "跌停池")
    dt_rows = [{"code": r.code, "name": r.name, "market": r.market, "seal_amount": r.seal_amount,
                "board_amount": r.board_amount, "down_days": r.down_days, "open_times": r.open_times,
                "last_seal_time": r.last_seal_time.strftime("%H:%M:%S") if r.last_seal_time else None,
                "pct": r.pct_change, "industry": r.em_industry} for r in (dt_res[0] if dt_res else [])]
    dt_tc = dt_res[1] if dt_res else None
    earlier_locks = [x["code"] for x in _DAY.seal_targets(data_d, [], now_hms, trusted=False)]
    need = [c for c in dict.fromkeys([r["code"] for r in dt_rows] + hm_codes + member_codes + earlier_locks
                                     + ([lead3["code"]] if lead3 else [])) if c not in facts]
    info = live._names(db, need) if need else {}
    for r in dt_rows:
        if r["code"] in info and r.get("market") is not None:
            n, st, _ = info[r["code"]]
            info[r["code"]] = (n, st, int(r["market"]))
    f_q = _pool.submit(live._quotes, [(c, info[c][2]) for c in need]) if need else None
    f_board = {g["sid"]: _pool.submit(live._cached, ("br-bd", g["code"], data_d), MINUTE_TTL_S,
                                      lambda code=g["code"]: fetch_board_minutes(code))
               for g in groups[:MAX_EVAL_GROUPS] if not g.get("too_many") and not g.get("over_budget")}
    quotes, q_err = clock.wait(f_q, "行情")
    for c in need:
        n, st, _ = info[c]
        facts[c] = live.live_fact(c, (quotes or {}).get(c), data_d, st, n or names_today.get(c))

    # ① 跌停家数
    verify = {}
    for r in dt_rows:
        f = facts.get(r["code"]) or {}
        verify[r["code"]] = ("sealed" if f.get("status") == "ok" and f.get("limit") == "limit_down" else
                             "left" if f.get("status") == "ok" else
                             "stale" if f.get("status") == "stale" else "missing")
    ld_meta = {"trade_date": data_d.isoformat(), "observed_at": now_hms, "source": "东财跌停池（非 ST、沪深）+ 实时行情逐只核",
               "coverage": f"跌停池 {len(dt_rows)} 行（接口自报 {dt_tc if dt_tc is not None else '—'}），行情核上 "
                           f"{sum(1 for v in verify.values() if v in ('sealed', 'left'))} 只",
               "compare_source": "东财涨跌分布（含 ST、沪深北）", "compare_error": breadth_err}
    ld = R.limit_down_item(phase_ok=True, phase_note="", pool_ok=dt_res is not None, pool_error=dt_err, pool_tc=dt_tc,
                           rows=dt_rows, verify=verify, updown_ld=(breadth or {}).get("limit_down"),
                           updown_ok=bool(breadth), market_time=market_dt, source_meta=ld_meta)

    # ② 最大空头：近 3 日领跌 + 跌停池封单最大（进程内锁定）
    pool_trusted = ld["status"] != R.INSUFFICIENT and dt_res is not None
    seal_locks = _DAY.seal_targets(data_d, [r for r in dt_rows if verify.get(r["code"]) == "sealed"] if pool_trusted else [],
                                   now_hms, trusted=pool_trusted)
    targets = []
    if lead3:
        targets.append({**lead3, "lock": "3d"})
    for s in seal_locks:
        if lead3 and s["code"] == lead3["code"]:
            targets[0]["basis"] += f"；也是{s['basis']}（{(s['seal_amount'] or 0) / 1e8:.2f} 亿）"
            continue
        targets.append({**s, "lock": "seal",
                        "basis": f"{s['basis']}（{(s['seal_amount'] or 0) / 1e8:.2f} 亿）"})
    f_tmin = {t["code"]: (f_lead3 if t.get("lock") == "3d" else
                          _pool.submit(live._cached, ("br-min", t["code"], t.get("market") or 0, data_d), MINUTE_TTL_S,
                                       lambda t=t: fetch_minute_days_tencent(t["code"], t.get("market") or 0)))
              for t in targets}
    t_info = live._names(db, [t["code"] for t in targets]) if targets else {}
    for t in targets:
        days, err = clock.wait(f_tmin[t["code"]], f"{t['name'] or t['code']} 分钟")
        prev_close, pts = _today_pts(days, data_d, upto)
        f = facts.get(t["code"]) or {}
        price_now = f.get("price") if f.get("status") == "ok" else None
        prev_close = prev_close or f.get("prev_close")
        is_st = t_info.get(t["code"], (None, False, 0))[1]
        ldp = exact_limit_price(prev_close, get_actual_limit_pct(t["code"], is_st), False) if prev_close else None
        t["judge"] = (R.judge_bear(pts, prev_close=prev_close, ld_price=ldp, price_now=price_now)
                      if not err else {"state": R.UNKNOWN, "evidence": err})
        t["judge"]["state_zh"] = R.BEAR_ZH[t["judge"]["state"]]
        t["price"], t["pct"] = price_now, (round((price_now / prev_close - 1) * 100, 2) if price_now and prev_close else None)
    bear_notes = [(f"封单锁定记在服务进程内存里，今天从 {_DAY.first_seen} 第一次可信快照起算；"
                   "更早就开板离开跌停池的看不到，服务重启会重新锁定") if _DAY.first_seen else
                  "今天还没有可信的跌停池快照，封单最大的那只还没锁定（只核近 3 日领跌股）"]
    if lead3:
        bear_notes.append(f"近 3 日领跌的范围：库里有日快照的非 ST 沪深票 {lead3['scope_n']} 只（跟踪范围，不是全市场）")
    bear = R.bear_item(targets, phase_ok=True, phase_note="", notes=bear_notes)

    # ③ 大盘
    idx_items = []
    for c, m, name in live.INDEXES:
        days, err = clock.wait(f_idx[c], f"{name} 分钟")
        prev_close, pts = _today_pts(days, data_d, upto)
        q = (idx_quotes or {}).get(c)
        price_now = q.price if q and q.trade_date == data_d and q.price else None
        j = (R.judge_index(pts, prev_close=prev_close or (q.prev_close if q else None), price_now=price_now)
             if not err else {"ok": None, "evidence": err})
        idx_items.append({"code": c, "name": name, "judge": j})
    vol = None
    if amount and amount.get("date") == data_d.isoformat() and amount.get("prev_same") and amount.get("same_amount"):
        vol = {"ratio": round(amount["same_amount"] / amount["prev_same"], 3), "amount": amount["same_amount"],
               "prev_same": amount["prev_same"], "prev_date": amount.get("prev_date"),
               "same_as_of": amount.get("same_as_of"), "source": amount.get("source")}
    idx = R.index_item(idx_items, volume=vol, breadth=breadth, phase_ok=True, phase_note="")
    if vol is None:
        idx["volume_error"] = amount_err or "分钟成交额还不是今天的"

    # ④ 高标反馈
    if hm_sample is None:
        hm = R.item("highmark", "高标负反馈", R.INSUFFICIENT, "样本不足", f"{ref} 没有生命周期快照，昨日高标样本拿不到")
    else:
        stats = R.highmark_stats([{**(facts.get(x["code"]) or {}), "code": x["code"],
                                   "name": x.get("name") or (facts.get(x["code"]) or {}).get("name")} for x in hm_sample])
        prev_obs, worst = _DAY.hm_observe(data_d, market_dt or local_now, stats["median_pct"])
        scope = f"{ref} 高标池（近 60 日最高连板 ≥4）里生命周期是连板中 / 刚断板 / 修复中的 {len(hm_sample)} 只"
        hm = R.highmark_item(stats, prev=prev_obs, worst_median=worst, phase_ok=True, phase_note="", scope=scope)
        hm["sample"] = [{"code": x["code"], "name": x.get("name"), "state_zh": HM_STATE_ZH[x["state"]],
                         "pct": (facts.get(x["code"]) or {}).get("pct"), "limit": (facts.get(x["code"]) or {}).get("limit")}
                        for x in hm_sample]
        hm["memory_note"] = "「上一次观测」记在服务进程内存里，第一次打开页面时还没有，要隔 ≥60 秒再看一次"

    m_items = [ld, bear, idx, hm]
    m_status, m_worst = R.combine(m_items)
    market = {"status": m_status, "status_zh": R.STATUS_ZH[m_status],
              "label": "市场过关" if m_status == R.PASS else (m_worst or {}).get("label"),
              "reason": "四项必需证据都满足" if m_status == R.PASS else f"{m_worst['title']}：{m_worst['reason']}",
              "items": m_items}

    # ── 主线：板块分时 × 上证分时 × 成分股广度 ──
    idx_days, _ = clock.wait(f_idx[CORE_INDEX[0]], "上证分钟")
    idx_prev, idx_pts = _today_pts(idx_days, data_d, upto)
    q_sh = (idx_quotes or {}).get(CORE_INDEX[0])
    idx_pct_now = round(q_sh.pct_change, 2) if q_sh and q_sh.trade_date == data_d and q_sh.pct_change is not None else None
    evals = []
    for g in groups:
        ms = members.get(g["sid"], [])
        g_out = {"sector": {"id": g["sid"], "code": g["code"], "name": g["name"], "type": g.get("type")},
                 "merged": g["merged"], "origin": g["origin"], "prev_state": g.get("prev_state"),
                 "active_today": len(g["active"]), "members": len(ms),
                 "meta": {"trade_date": data_d.isoformat(), "observed_at": now_hms,
                          "source": "东财板块分时（push2his）× 上证分时 × 成分股实时行情（等权）"}}
        if g.get("too_many") or g.get("over_budget"):
            g_out.update(status=R.INSUFFICIENT, label="数据不足",
                         reason=f"成分股 {len(ms)} 只，超过实时行情预算，没取" if g.get("too_many") else "本次行情预算用完，没取")
            evals.append((g, g_out))
            continue
        br = R.breadth_of([facts.get(m["code"]) or {} for m in ms if not m["is_st"]], sum(1 for m in ms if not m["is_st"]))
        b_res, b_err = clock.wait(f_board.get(g["sid"]), f"{g['name']} 分时")
        series = None
        if b_res and b_res[0] and idx_prev and idx_pts:
            series = R.sector_series(_bars_pts(b_res[1], data_d, upto), b_res[0], idx_pts, idx_prev)
        elif not b_err:
            b_err = "板块分时还是空的" if b_res is not None else b_err
        st, label, reason = R.mainline_judge(origin=g["origin"], br=br, series=series, series_error=b_err,
                                             idx_pct_now=idx_pct_now)
        if g["origin"] == "new" and st == R.CONFIRMED:
            label = "新方向确认走强"
        g_out.update(status=st, status_zh=R.MAINLINE_ZH[st], label=label, reason=reason, breadth=br, series=series,
                     series_error=b_err)
        evals.append((g, g_out))

    # ── 带队：先动时刻、承接、跟进 ──
    lead_jobs = {}
    for g, out in evals:
        if out["status"] not in (R.CONFIRMED, R.PENDING, R.CANDIDATE):
            continue
        ms = {m["code"]: m for m in members.get(g["sid"], []) if not m["is_st"]}
        cands = []
        for c, m in ms.items():
            f, p = facts.get(c) or {}, prev_snap.get(c)
            in_core, lu20 = c in core_codes, (p.limit_up_days_20d if p is not None else 0) or 0
            if not (c in active_codes or in_core or lu20 >= 1):
                continue
            price = f.get("price")
            cands.append({"code": c, "name": m["name"] or f.get("name"), "market": m["market"], "in_core": in_core,
                          "lu20": lu20, "board": (p.board_count if p is not None and p.is_limit_up else 0),
                          "price": price, "pct": f.get("pct"), "limit": f.get("limit"),
                          "float_mv": (m["float_shares"] * price) if m.get("float_shares") and price else None,
                          "first_time": first_time.get(c),
                          "broken_times": (sealed_today.get(c) or {}).get("broken_times"),
                          "lead_at": first_time.get(c, "")[:5] or None})
        cands.sort(key=R.leader_sort_key)
        # 补分时：没有首封时刻的（拿 +5% 时刻）、没封住的头一只（看承接）
        want = [c for c in cands if not c["first_time"]][:2] + [c for c in cands[:1] if c["limit"] != "sealed"]
        for c in list({c["code"]: c for c in want}.values())[:MAX_MINUTE_PER_GROUP]:
            lead_jobs[c["code"]] = _pool.submit(live._cached, ("br-min", c["code"], c["market"], data_d), MINUTE_TTL_S,
                                                lambda c=c: fetch_minute_days_tencent(c["code"], c["market"]))
        out["_cands"] = cands
    for g, out in evals:
        cands = out.pop("_cands", None)
        if cands is None:
            continue
        for c in cands:
            pts, prev_close = None, None
            if c["code"] in lead_jobs:
                days, _err = clock.wait(lead_jobs[c["code"]], f"{c['name']} 分钟")
                prev_close, pts = _today_pts(days, data_d, upto)
            if not c["lead_at"]:
                c["lead_at"], c["lead_basis"] = R.lead_time(first_time=None, pts=pts, prev_close=prev_close)
            else:
                c["lead_basis"] = "首封" if c["limit"] == "sealed" else "首次触板"
            c["support"], c["support_txt"] = R.support_of(limit=c["limit"], broken_times=c["broken_times"],
                                                          pts=pts, prev_close=prev_close)
        cands.sort(key=R.leader_sort_key)
        peers = [{"code": c, "name": names_today.get(c) or (facts.get(c) or {}).get("name") or c, "at": first_time[c]}
                 for c in g["active"] if c in first_time]
        lj = R.leader_judge(cands[:4], peers)
        out["leader"] = {k: v for k, v in lj.items()}
        out["leader_candidates"] = [{k: c.get(k) for k in ("code", "name", "lead_at", "lead_basis", "in_core", "lu20",
                                                            "board", "pct", "limit", "float_mv", "price", "support_txt")}
                                    for c in cands[:4]]
        out["leader_note"] = "排序：先动时刻 → 昨日人气核心 → 连板高度 → 近 20 日涨停 → 流通市值小 → 价格低（不用龙头分）"
    order = {R.CONFIRMED: 0, R.PENDING: 1, R.CANDIDATE: 2, R.NOT_MET: 3, R.INSUFFICIENT: 4}
    shown = sorted((o for _, o in evals), key=lambda o: (order[o["status"]], -o["active_today"]))[:MAX_SHOWN]

    entry, entry_reason = R.pick_entry(m_status, shown)
    if pools is None and pools_err:
        notes.append(f"涨停 / 炸板池没拿到（{pools_err}），新方向和首封时刻缺数据")
    if q_err:
        notes.append(q_err)
    return {**base, "market": market, "mainlines": shown, "mainline_note": ml_note,
            "prev_mainline_date": prev_ml.get("state_date"), "entry": entry,
            "entry_reason": entry_reason, "notes": notes,
            "memory_note": "当天要记住的两件事（封单锁定、高标上一次观测）在服务进程内存里，不写库"}


# ── 盘后研究：只摆可证的收盘事实 ─────────────────────────────────────────────

NO_INTRADAY = "盘中无法复核"


def build_close(db: Session, d: date, cal: Sequence[date]) -> dict:
    """
    盘后研究：所选交易日的收盘事实。跌停家数、高标反馈、主升状态、首封先后是收盘后能证的；
    最大空头止跌、大盘止跌放量、主线分时证据、带队承接要盘中观测——没存盘中观测，一律「盘中无法复核」。
    不给结论、不给买入检查入口：历史日期不能串进实时结论。
    """
    from .strong_stock_service import _limit_moves_query
    cal = sorted(cal)
    prev = max((x for x in cal if x < d), default=None)
    base = {"rule_version": R.RULE_VERSION, "mode": "close", "trade_date": d.isoformat(),
            "ref_date": prev.isoformat() if prev else None, "as_of": None, "thresholds": R.THRESHOLDS,
            "entry": None, "entry_reason": "盘后研究只摆收盘事实，不提供买入检查入口", "notes": []}
    # ① 跌停家数（收盘，非 ST 日快照）
    n_ld = _limit_moves_query(db, d, "limit_down").count()
    n_rows = db.query(func.count(StockDailySnapshot.id)).filter(StockDailySnapshot.date == d).scalar() or 0
    meta = {"trade_date": d.isoformat(), "source": "日快照（非 ST，收盘）", "observed_at": "收盘",
            "coverage": f"当天快照 {n_rows} 行"}
    if not n_rows:
        ld = R.item("limit_down", "跌停家数", R.INSUFFICIENT, "数据不足", f"{d} 没有日快照", meta=meta)
    else:
        st = R.BLOCK if n_ld > R.LD_PASS_MAX else R.PASS
        lab = "停手 · 空仓" if n_ld > R.LD_NO_NEW_MAX else "不开新仓" if n_ld > R.LD_PASS_MAX else "≤10 家"
        ld = R.item("limit_down", "跌停家数", st, lab, f"收盘跌停 {n_ld} 家（非 ST）", count=n_ld, meta=meta)
    # ② 最大空头：标的能锁，止没止跌要分时
    lead3 = three_day_leader(db, cal, d) if prev else None
    bear = R.item("bear", "最大空头", R.INSUFFICIENT, NO_INTRADAY,
                  "止跌要看离开跌停后的分时回踩，当天没有存下盘中观测" +
                  (f"；近 3 日领跌：{lead3['name']}（{lead3['basis']}）" if lead3 else ""),
                  targets=[{**lead3, "judge": {"state": R.UNKNOWN, "state_zh": NO_INTRADAY, "evidence": NO_INTRADAY}}]
                  if lead3 else [])
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
            hm = R.highmark_item(stats, prev={"at": "收盘", "median_pct": stats["median_pct"]},
                                 worst_median=None, phase_ok=True, phase_note="", scope=scope)
            hm["reason"] = hm["reason"].split("；上一次")[0] + "（收盘口径）"
    items = [ld, bear, idx, hm]
    market = {"status": R.INSUFFICIENT, "status_zh": "收盘事实", "label": "收盘事实 · 盘中无法复核",
              "reason": "最大空头止跌、大盘止跌放量要盘中观测，所选日期没有——不给「市场过关」结论", "items": items}
    # 主线：所选日收盘的主升状态 + 首封先后
    from ..models.limit_up_detail import LimitUpDailyDetail
    from .sector_mainline_service import MAINLINE_FAMILY, IGNITION, get_sector_mainline_state
    st = get_sector_mainline_state(db, as_of=d)
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
                    "label": f"收盘：{s['state_label']}", "reason": f"{s['state_reason'] if 'state_reason' in s else ''}"
                    f"；先于指数走强、回踩后相对强度要分时，{NO_INTRADAY}".lstrip("；"),
                    "active_today": len(firsts), "members": len(ms), "leader": leader})
    return {**base, "market": market, "mainlines": mls,
            "mainline_note": None if mls else f"{d} 收盘没有处在点火 / 主升 / 加速 / 高潮 / 分歧的关注板块",
            "prev_mainline_date": st.get("state_date")}
