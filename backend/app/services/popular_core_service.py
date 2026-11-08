"""
市场人气核心池 + 人气核心活跃事件（每日复盘 V2）。

## 人气核心是什么

一组**行情和交易行为**上的高辨识度候选，不是社交热度、不是机构持仓、不是上涨概率。
多入口召回、按代码取并集；**不合成任何「人气分」**，每只票只挂它实际满足的客观标签。

| 来源 | 规则（RULES） | 统计范围 |
|---|---|---|
| gain_5/10/20/60 | 区间涨幅前 20 | 全市场（大市值同步的全市场收盘快照，东财前复权区间涨幅）；当天没有完整快照就是 missing |
| lu_10/20/60 | 涨停次数前 10 | 已跟踪股票（股票日快照只覆盖进过候选池的票） |
| board | 当前连板前 10（≥2 板） | 已跟踪股票 |
| high_board | 近 60 日最高连板前 10（≥3 板） | 已跟踪股票 |
| turnover / turnover_repeat | 当日成交额前 20；近 5 个交易日 ≥3 次进前 20 | 成交额前列名单 |
| lifecycle_restart | 断板→修复、修复→二波确认等转移 | 生命周期快照（强势池） |

排名一律是**竞争排名**（并列同名次，下一个跳号），只在对应来源完整时给出。

## 两条轴不互相覆盖

「辨识度依据」（凭什么进池）和「当前结构状态」（生命周期）分开存、分开显示：60 日涨幅
第一但已经走弱的票，历史辨识度照样在，只是当前状态是走弱。

## 冻结

`freeze()` 把某天的池子和事件写进 market_popular_core_* / leader_activation_events，
之后读那一天只读冻结的那份。**没冻结过的历史日期现算**，用的也全是那一天的数据；
只有「涨停次日均」是股票表上的当前值，所以现算历史日期时不回填它。
"""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

RULE_VERSION = "core_v1"

RULES = {
    "gain_top": 20, "lu_top": 10, "board_top": 10, "board_min": 2,
    "high_top": 10, "high_min": 3, "turnover_top": 20,
    "turnover_repeat_days": 5, "turnover_repeat_min": 3,
    "volume_surge_ratio": 2.0,       # 放量异动：量比 ≥ 2（东财量比，全市场快照）
    "response_pct": 5.0,             # 关联股「满足上涨响应条件」：当日涨幅 ≥ 5%
    "leader_weak_pct": -3.0,         # 高标转弱：当日跌幅 ≤ -3% 或断板
}

GAIN_WINDOWS = (5, 10, 20, 60)
LU_WINDOWS = (10, 20, 60)
SCOPE_FULL = "全市场（沪深主板+创业板+科创板，不含北交所）"
SCOPE_TRACKED = "已跟踪股票（进过候选池的），不是全 A 股"
SOURCE_LABEL = {
    **{f"gain_{w}": f"{w}日涨幅" for w in GAIN_WINDOWS},
    **{f"lu_{w}": f"{w}日涨停" for w in LU_WINDOWS},
    "board": "连板高度", "high_board": "60日高板", "turnover": "成交额",
    "turnover_repeat": "成交额反复上榜", "lifecycle_restart": "生命周期重启",
}
# 「历史人气」视角看这些依据：中期记忆、反复活跃、容量
HISTORICAL_SOURCES = {"gain_60", "lu_60", "lu_20", "high_board", "turnover_repeat"}
RESTART_TRANSITIONS = {
    ("BROKEN", "REPAIRING"): "repair_start", ("CROSS_FAILED", "REPAIRING"): "repair_start",
    ("REPAIRING", "CROSS_SUCCESS"): "repair_confirm", ("CROSS_WEAKENING", "CROSS_SUCCESS"): "repair_confirm",
}

EVENT_LABEL = {
    "limit_up": "今日涨停", "relimit": "再次涨停", "streak_continue": "连板延续",
    "streak_break": "断板", "repair_start": "修复启动", "repair_confirm": "修复确认",
    "repair_fail": "修复失败", "volume_surge": "放量异动",
}
RESPONSE_LABEL = {
    "limit_up": "已涨停", "up_response": "满足上涨响应条件", "independent_strong": "高标转弱时独立走强",
    "not_met": "尚未满足响应条件", "insufficient": "响应数据不足",
}


def board_of(code: str) -> str:
    if code.startswith(("688", "689")):
        return "科创板"
    if code.startswith(("300", "301")):
        return "创业板"
    return "主板"


def competition_rank(values: Dict[str, Optional[float]], top: int) -> Dict[str, int]:
    """{code: 名次}，只留名次 ≤ top 的。并列同名次（1,2,2,4）；None 和 ≤0 不参与。"""
    asc = sorted(v for v in values.values() if v is not None and v > 0)
    out = {}
    for c, v in values.items():
        if v is None or v <= 0:
            continue
        r = 1 + len(asc) - bisect_right(asc, v)      # 比它大的有几个（全市场五千多只，不能两两比）
        if r <= top:
            out[c] = r
    return out


# ── 读数（全是指定交易日的数据）────────────────────────────────────────────

def _full_market(db: Session, d: date) -> Optional[Dict[str, Any]]:
    """当天有东财完整收盘快照才算数（腾讯备用没有区间涨幅）。"""
    from ..models.large_cap import LargeCapMarketDaily, LargeCapSyncBatch
    b = (db.query(LargeCapSyncBatch.id).filter(LargeCapSyncBatch.trade_date == d,
                                               LargeCapSyncBatch.applied.is_(True),
                                               LargeCapSyncBatch.source == "eastmoney_clist").first())
    if b is None:
        return None
    return {r.stock_code: r for r in db.query(
        LargeCapMarketDaily.stock_code, LargeCapMarketDaily.stock_name, LargeCapMarketDaily.is_st,
        LargeCapMarketDaily.pct_change, LargeCapMarketDaily.pct_change_5d, LargeCapMarketDaily.pct_change_10d,
        LargeCapMarketDaily.pct_change_20d, LargeCapMarketDaily.pct_change_60d, LargeCapMarketDaily.amount,
        LargeCapMarketDaily.volume_ratio, LargeCapMarketDaily.industry,
    ).filter(LargeCapMarketDaily.trade_date == d)}


def _tracked(db: Session, d: Optional[date]) -> Dict[str, tuple]:
    from ..models.stock import Stock, StockDailySnapshot
    if d is None:
        return {}
    return {s.code: (s, x) for s, x in db.query(Stock, StockDailySnapshot)
            .join(StockDailySnapshot, StockDailySnapshot.stock_id == Stock.id)
            .filter(StockDailySnapshot.date == d)}


def _lifecycle(db: Session, d: date) -> Dict[str, Any]:
    from ..routers.leader_cycle import get_leader_cycle
    resp = get_leader_cycle(trade_date=d, db=db)
    if resp.trade_date != d:
        return {}
    return {it.code: it for it in [*resp.running, *resp.broken]}


# ── 核心池 ──────────────────────────────────────────────────────────────────

def compute(db: Session, d: date, cal: Sequence[date], latest_snapshot_date: Optional[date] = None) -> dict:
    """现算 d 这天的人气核心池和活跃事件。只读 d（和 d 往前的序列），不读之后的。"""
    from ..models.limit_up_detail import LimitUpDailyDetail
    from ..models.turnover_pool import TurnoverPoolDaily
    i = list(cal).index(d) if d in set(cal) else None
    prev_d = cal[i - 1] if i else None
    fm = _full_market(db, d)
    tr, tr_prev = _tracked(db, d), _tracked(db, prev_d)
    life = _lifecycle(db, d)
    detail = {r.stock_code: r for r in db.query(LimitUpDailyDetail).filter(LimitUpDailyDetail.trade_date == d)}
    turnover = {r.stock_code: r for r in db.query(TurnoverPoolDaily).filter(TurnoverPoolDaily.date == d)}

    sources: Dict[str, dict] = {}
    picks: Dict[str, List[dict]] = defaultdict(list)

    def add(src: str, ranks: Dict[str, int], values: Dict[str, Any], scope: str):
        for c, r in ranks.items():
            picks[c].append({"source": src, "rank": r, "value": values.get(c), "scope": scope})
        sources[src]["n"] = len(ranks)

    # 区间涨幅：全市场
    gain_ranks: Dict[int, Dict[str, int]] = {}
    for w in GAIN_WINDOWS:
        src = f"gain_{w}"
        if fm is None:
            sources[src] = {"status": "missing", "scope": SCOPE_FULL, "n": 0,
                            "note": f"{d} 没有完整的全市场收盘快照（2026-11-06 起才有），不排全市场涨幅"}
            gain_ranks[w] = {}
            continue
        vals = {c: getattr(r, f"pct_change_{w}d") for c, r in fm.items()}
        sources[src] = {"status": "ok", "scope": SCOPE_FULL, "n": 0, "note": None}
        gain_ranks[w] = competition_rank(vals, RULES["gain_top"])
        add(src, gain_ranks[w], vals, SCOPE_FULL)

    # 涨停次数 / 连板 / 高板：已跟踪股票
    tracked_ok = bool(tr)
    unsettled = sum(1 for _s, x in tr.values() if not x.is_settled)
    t_note = None if not unsettled else f"{unsettled}/{len(tr)} 行快照不是收盘终值"
    for w in LU_WINDOWS:
        src = f"lu_{w}"
        vals = {c: getattr(x, f"limit_up_days_{w}d") for c, (_s, x) in tr.items()}
        sources[src] = {"status": "ok" if tracked_ok and not unsettled else ("partial" if tracked_ok else "missing"),
                        "scope": SCOPE_TRACKED, "n": 0, "note": t_note if tracked_ok else f"{d} 没有股票日快照"}
        add(src, competition_rank(vals, RULES["lu_top"]), vals, SCOPE_TRACKED)
    board_vals = {c: x.board_count for c, (_s, x) in tr.items()
                  if x.is_limit_up and (x.board_count or 0) >= RULES["board_min"]}
    sources["board"] = {"status": sources["lu_10"]["status"], "scope": SCOPE_TRACKED, "n": 0, "note": t_note}
    add("board", competition_rank(board_vals, RULES["board_top"]), board_vals, SCOPE_TRACKED)
    high_vals = {c: x.board_count_60d for c, (_s, x) in tr.items() if (x.board_count_60d or 0) >= RULES["high_min"]}
    sources["high_board"] = {"status": sources["lu_10"]["status"], "scope": SCOPE_TRACKED, "n": 0, "note": t_note}
    add("high_board", competition_rank(high_vals, RULES["high_top"]), high_vals, SCOPE_TRACKED)

    # 成交额：当天前 20 + 近 5 个交易日反复上榜
    tv_ranks = {c: r.rank for c, r in turnover.items() if r.rank <= RULES["turnover_top"]}
    sources["turnover"] = {"status": "ok" if turnover else "missing", "scope": "成交额前列名单", "n": 0,
                           "note": None if turnover else f"{d} 没有成交额前列名单"}
    add("turnover", tv_ranks, {c: round(turnover[c].amount / 1e8, 2) for c in tv_ranks}, "成交额前列名单")
    window = [x for x in cal if x <= d][-RULES["turnover_repeat_days"]:]
    if len(window) == RULES["turnover_repeat_days"]:
        have_days = {x for (x,) in db.query(TurnoverPoolDaily.date).filter(TurnoverPoolDaily.date.in_(window)).distinct()}
        cnt = Counter(c for (c,) in db.query(TurnoverPoolDaily.stock_code)
                      .filter(TurnoverPoolDaily.date.in_(window), TurnoverPoolDaily.rank <= RULES["turnover_top"]))
        rep = {c: n for c, n in cnt.items() if n >= RULES["turnover_repeat_min"]}
        complete = len(have_days) == len(window)
        sources["turnover_repeat"] = {
            "status": "ok" if complete else "partial", "scope": "成交额前列名单", "n": 0,
            "note": None if complete else f"近 {len(window)} 个交易日里只有 {len(have_days)} 天有名单，次数可能偏少"}
        for c, n in rep.items():
            picks[c].append({"source": "turnover_repeat", "rank": None, "value": n, "scope": "成交额前列名单"})
        sources["turnover_repeat"]["n"] = len(rep)
    else:
        sources["turnover_repeat"] = {"status": "missing", "scope": "成交额前列名单", "n": 0, "note": "交易日历不够 5 天"}

    # 生命周期重启
    restart = {c: RESTART_TRANSITIONS[(it.previous_lifecycle_state, it.lifecycle_state)]
               for c, it in life.items()
               if it.transitioned_today and (it.previous_lifecycle_state, it.lifecycle_state) in RESTART_TRANSITIONS}
    sources["lifecycle_restart"] = {"status": "ok" if life else "missing", "scope": "生命周期快照（强势池）", "n": len(restart),
                                    "note": None if life else f"{d} 没有生命周期快照"}
    for c, ev in restart.items():
        picks[c].append({"source": "lifecycle_restart", "rank": None, "value": ev, "scope": "生命周期快照（强势池）"})

    # 「涨停次日均」只有股票表上的当前值：只有 d 就是最新一天时才用，历史日期不回填
    use_premium = latest_snapshot_date is not None and d == latest_snapshot_date
    items = {c: _row(c, srcs, fm, tr.get(c), detail.get(c), turnover.get(c), life.get(c), gain_ranks, use_premium)
             for c, srcs in picks.items()}
    events = compute_events(d, list(items), fm, tr, tr_prev, detail, life)
    return {"date": d, "prev_date": prev_d, "rule_version": RULE_VERSION, "rules": RULES, "sources": sources,
            "items": items, "events": events, "premium_is_current": use_premium}


def _row(code, srcs, fm, t, det, tv, lc, gain_ranks, use_premium) -> dict:
    f = fm.get(code) if fm else None
    s, x = t if t else (None, None)
    missing = []
    if f is None:
        missing.append("区间涨幅（这天没有全市场快照或这只不在里面）")
    if x is None:
        missing.append("涨停次数 / 连板（不在已跟踪股票里）")
    if det is not None and det.em_industry:
        sector, basis = det.em_industry, "当日涨停明细"
    elif f is not None and f.industry:
        sector, basis = f.industry, "当日全市场快照"
    elif s is not None and s.primary_sector_name:
        sector, basis = s.primary_sector_name, "当前主板块（非当日口径）"
    else:
        sector, basis = None, None
    name = (f.stock_name if f else None) or (s.name if s else None) or (det.stock_name if det else None) \
        or (tv.stock_name if tv else None) or (lc.name if lc else None)
    return {
        "stock_code": code, "stock_name": name, "board": board_of(code),
        "is_st": (f.is_st if f else (s.is_st if s else None)),
        "selection_sources": sorted(srcs, key=lambda z: (z["rank"] is None, z["rank"] or 0)),
        "pct_change": (f.pct_change if f and f.pct_change is not None
                       else (x.pct_change if x else (det.pct_change if det else None))),
        **{f"rank_{w}d": gain_ranks[w].get(code) for w in GAIN_WINDOWS},
        **{f"pct_change_{w}d": getattr(f, f"pct_change_{w}d") if f else None for w in GAIN_WINDOWS},
        **{f"limit_up_days_{w}d": getattr(x, f"limit_up_days_{w}d") if x else None for w in LU_WINDOWS},
        "current_board": (x.board_count if x and x.is_limit_up else (0 if x else None)),
        "historical_max_board": x.board_count_60d if x else None,
        "turnover_rank": tv.rank if tv else None,
        "amount": (f.amount if f and f.amount is not None else (round(tv.amount / 1e8, 2) if tv else None)),
        "limit_up_next_avg_pct": s.limit_up_next_avg_pct if (use_premium and s) else None,
        "limit_up_next_samples": s.limit_up_next_samples if (use_premium and s) else None,
        "lifecycle_state": lc.lifecycle_state if lc else None,
        "sector": sector, "sector_basis": basis,
        "data_status": {"missing": missing} if missing else None,
    }


# ── 活跃事件 ────────────────────────────────────────────────────────────────

def compute_events(d: date, codes: Iterable[str], fm, tr, tr_prev, detail, life) -> List[dict]:
    """
    只用当天已结算的数据。**数据缺了就不出事件**：今天没有快照就不判断板（没有假断板），
    昨天没有快照就不判「再次涨停」和「连板延续」。
    """
    out = []
    for c in codes:
        s, x = tr.get(c, (None, None))
        _sp, xp = tr_prev.get(c, (None, None))
        det = detail.get(c)
        name = (s.name if s else None) or (det.stock_name if det else None)
        limit_up = det is not None or (x is not None and x.is_limit_up)

        def ev(t, evidence, source, time=None):
            out.append({"leader_code": c, "leader_name": name, "event_type": t, "event_time": time,
                        "evidence": evidence, "source": source, "data_status": "ok"})
        t = det.first_limit_time if det is not None else None
        if limit_up:
            ev("limit_up", f"{d} 涨停" + (f"，首封 {t.strftime('%H:%M:%S')}" if t else ""),
               "涨停明细" if det is not None else "股票日快照", t)
            if xp is not None and not xp.is_limit_up and (xp.board_count_60d or 0) >= 2:
                ev("relimit", f"前一交易日没涨停，之前 60 日最高 {xp.board_count_60d} 板，今天重新涨停", "股票日快照", t)
        if x is not None and xp is not None and x.is_limit_up and (x.board_count or 0) >= 2 \
                and xp.board_count == x.board_count - 1:
            ev("streak_continue", f"{xp.board_count} → {x.board_count} 板", "股票日快照", t)
        if x is not None and xp is not None and xp.is_limit_up and (xp.board_count or 0) >= 2 \
                and not x.is_limit_up and det is None:
            ev("streak_break", f"昨天 {xp.board_count} 板，今天没涨停（{x.pct_change}%）", "股票日快照")
        lc = life.get(c)
        if lc is not None and lc.transitioned_today:
            pair = (lc.previous_lifecycle_state, lc.lifecycle_state)
            if pair in RESTART_TRANSITIONS:
                ev(RESTART_TRANSITIONS[pair], f"{pair[0]} → {pair[1]}", "生命周期")
            elif lc.lifecycle_state == "CROSS_FAILED":
                ev("repair_fail", f"{pair[0]} → CROSS_FAILED", "生命周期")
        f = fm.get(c) if fm else None
        if f is not None and f.volume_ratio is not None and f.volume_ratio >= RULES["volume_surge_ratio"]:
            ev("volume_surge", f"量比 {f.volume_ratio}（≥{RULES['volume_surge_ratio']}），当日 {f.pct_change}%", "全市场快照")
    return out


# ── 关联股的系统响应分类（事实，不是判断）────────────────────────────────────

def classify_response(rel: Optional[dict], leader: Optional[dict], leader_events: Iterable[str] = ()) -> str:
    """
    关联股当天的市场响应，只按预先写死的条件分：涨停 / 涨幅 ≥ RULES['response_pct'] /
    高标转弱（跌幅 ≤ -3% 或断板）时独立走强 / 没满足 / 数据不足。

    **「没满足」不等于「没响应」**，更不说明谁带动了谁——那要分时数据。
    """
    if rel is None or rel.get("status") == "missing" or rel.get("pct") is None:
        return "insufficient"
    if rel.get("is_limit_up"):
        return "limit_up"
    if rel["pct"] >= RULES["response_pct"]:
        lp = (leader or {}).get("pct")
        weak = (lp is not None and lp <= RULES["leader_weak_pct"]) or "streak_break" in set(leader_events)
        return "independent_strong" if weak else "up_response"
    return "not_met"


# ── 冻结与读取 ──────────────────────────────────────────────────────────────

def freeze(db: Session, d: date, cal: Sequence[date], latest_snapshot_date: Optional[date] = None) -> dict:
    """把 d 这天的池子和事件冻结下来（同一天同一规则版本是覆盖，不叠加）。"""
    from ..models.market_recap import LeaderActivationEvent, MarketPopularCoreDaily, MarketPopularCoreRun
    res = compute(db, d, cal, latest_snapshot_date)
    for M in (MarketPopularCoreDaily, LeaderActivationEvent):
        db.query(M).filter(M.trade_date == d, M.rule_version == RULE_VERSION).delete(synchronize_session=False)
    db.query(MarketPopularCoreRun).filter(MarketPopularCoreRun.trade_date == d,
                                          MarketPopularCoreRun.rule_version == RULE_VERSION).delete(synchronize_session=False)
    db.add(MarketPopularCoreRun(trade_date=d, rule_version=RULE_VERSION, sources=res["sources"], built_at=datetime.now()))
    for row in res["items"].values():
        db.add(MarketPopularCoreDaily(trade_date=d, rule_version=RULE_VERSION, **row))
    seen = set()
    for e in res["events"]:
        k = (e["leader_code"], e["event_type"])
        if k in seen:
            continue
        seen.add(k)
        db.add(LeaderActivationEvent(trade_date=d, rule_version=RULE_VERSION, **e))
    db.commit()
    return {"cores": len(res["items"]), "events": len(seen), "sources": res["sources"]}


_ROW_FIELDS = ("stock_code", "stock_name", "board", "is_st", "selection_sources", "pct_change",
               *[f"rank_{w}d" for w in GAIN_WINDOWS], *[f"pct_change_{w}d" for w in GAIN_WINDOWS],
               *[f"limit_up_days_{w}d" for w in LU_WINDOWS], "current_board", "historical_max_board",
               "turnover_rank", "amount", "limit_up_next_avg_pct", "limit_up_next_samples",
               "lifecycle_state", "sector", "sector_basis", "data_status")


def load(db: Session, d: date, cal: Sequence[date], latest_snapshot_date: Optional[date] = None) -> dict:
    """冻结过就读冻结的那份；没有就现算（frozen=False）。"""
    from ..models.market_recap import LeaderActivationEvent, MarketPopularCoreDaily, MarketPopularCoreRun
    run = (db.query(MarketPopularCoreRun)
           .filter(MarketPopularCoreRun.trade_date == d, MarketPopularCoreRun.rule_version == RULE_VERSION).first())
    if run is None:
        res = compute(db, d, cal, latest_snapshot_date)
        return {**res, "items": list(res["items"].values()), "frozen": False, "built_at": None}
    items = [{k: getattr(r, k) for k in _ROW_FIELDS} for r in db.query(MarketPopularCoreDaily)
             .filter(MarketPopularCoreDaily.trade_date == d, MarketPopularCoreDaily.rule_version == RULE_VERSION)]
    events = [{"leader_code": e.leader_code, "leader_name": e.leader_name, "event_type": e.event_type,
               "event_time": e.event_time, "evidence": e.evidence, "source": e.source, "data_status": e.data_status}
              for e in db.query(LeaderActivationEvent)
              .filter(LeaderActivationEvent.trade_date == d, LeaderActivationEvent.rule_version == RULE_VERSION)]
    return {"date": d, "rule_version": RULE_VERSION, "rules": RULES, "sources": run.sources, "items": items,
            "events": events, "frozen": True, "built_at": run.built_at,
            "premium_is_current": any(i["limit_up_next_samples"] is not None for i in items)}


def view_of(item: dict, event_codes: set, watched: set) -> List[str]:
    """一只票落在哪几个观察视角（不互斥，不是收益等级）。"""
    srcs = {s["source"] for s in item["selection_sources"]}
    v = ["all"]
    if item["stock_code"] in event_codes:
        v.append("active")
    if srcs & HISTORICAL_SOURCES:
        v.append("historical")
    if "lifecycle_restart" in srcs or item.get("lifecycle_state") in ("REPAIRING", "CROSS_SUCCESS"):
        v.append("repair")
    if item["stock_code"] in watched:
        v.append("mine")
    return v
