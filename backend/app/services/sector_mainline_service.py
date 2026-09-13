"""
板块趋势 · 主升板块雷达（sector_mainline_v1）

回答一个问题：**现在哪几个板块在主升**。在「大盘 → 板块主线 → 个股」这条链上管中间那一环：
大盘趋势页看环境，这里看主线在哪几个板块，再往下才轮到个股。**主升状态 ≠ 买入信号**——
这里只描述板块处在什么状态，不给买卖建议。

事实 → 闸门 → 状态 → 证据。不打分：每个关注板块过四道闸（趋势 / 相对强度 / 生态 / 风险），
每道闸给 PASS / WARN / FAIL / UNKNOWN 和它依据的事实，状态由闸门组合决定。页面上从状态
能一路追到「为什么是这个状态」。

**第一版是规则型状态机，阈值是工程初始值**：下面的分位数只用来定量级（板块指数不是个股），
不代表已经拿历史回测优化过。上线后用 scripts/sector_mainline.py --days 在生产上看分布再调。

## 数据从哪来（2026-09-13 量过生产后定的）

- 趋势、相对强度：板块指数日线 SectorIndexDaily（生产上 302 个关注板块历史齐全，约 300 根）
  和上证 000001（IndexDailySnapshot）。
- 生态（涨停序列、最高板、炸板、跌停）：**成分股日快照**（StockDailySnapshot ×
  StockSectorRelation），跟买入检查的「板块涨停数」同一个口径，历史跟快照一样长。
  **不用 SectorDailySnapshot**，因为生产上：
    · 只有 2026-08-21 以来 16 天；
    · board_height 几乎恒为 0（只看强势池成员——元件 09-07 涨停 13 只，高度记 0）；
    · strong_stock_count 是当时强势池成员数的拷贝，16 天里基本不动；
    · limit_up_count 跟成分股口径会「7 对 2」（数据新鲜度契约第 6 条）。
- **不用** emotion_score / leader_score / risk_score / phase：自造的黑箱分数。
- **不用** Sector.rank_*：只给前 5 名打标，其余是 NULL。相对强度排名在这里按全体关注板块现算。

## 口径纪律

- **状态基准日**：最近一个「关注板块大半有指数收盘 bar、成分股快照大半已结算、且已收盘」
  的交易日。板块指数只在收盘后写（sync_boards 盘中不写），盘中那一跑的快照也不会让基准日前移。
  不用 settled_by_date 的「一票否决」：生产上 09-04 ~ 09-10 每天都有少数行没结算，严格口径下
  过去的交易日全是 False，生态就全成了 UNKNOWN。那几只照实写进证据。
- **不知道就是 UNKNOWN，不当 0**：那天库里没快照 → 生态 UNKNOWN；上证缺那天 → 相对强度 UNKNOWN；
  成交额缺 → 放量那条事实跳过，写进证据。
- **阈值是板块指数量级的**。板块指数的波动远小于个股：2026-09-13 用 282 个板块 × 20 天实测，
  「均线多头 + RS 双正」的样本里偏离 MA20 p90 = 8.9%、p99 = 12.9%，5 日涨幅 p90 = 6.8%、
  p99 = 12.6%。个股那套「偏离 20% 算过热」搬过来一次都触发不了。
- **滞回**：三道闸（趋势 / 相对强度 / 生态）近 3 天里有 2 天全过才进主升；已经在主升里，
  哪天没全过先记「分歧」，不直接掉回「无」。
- 成分用的是**现在的**成分股关系，历史上调进调出的不回溯。

规则说明见 docs/SECTOR_MAINLINE.md。
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..models.market_index import IndexDailySnapshot, SectorIndexDaily
from ..models.sector import Sector, StockSectorRelation
from ..models.stock import StockDailySnapshot
from .eastmoney_fetcher import SH_TZ, bar_is_settled
from .relative_strength_service import rs_vs_benchmark
from .snapshot_settlement import settlement_counts

VERSION = "sector_mainline_v1"
BENCHMARK_CODE = "000001"
BENCHMARK_NAME = "上证指数"

# ── 状态 ──────────────────────────────────────────────────────────────────────
NONE = "NONE"
IGNITION = "IGNITION"
MAIN_RISE = "MAIN_RISE"
ACCELERATION = "ACCELERATION"
CLIMAX = "CLIMAX"
DIVERGENCE = "DIVERGENCE"
WEAKENING = "WEAKENING"
UNKNOWN = "UNKNOWN"

# 默认排序的状态优先级
STATE_ORDER = (ACCELERATION, MAIN_RISE, IGNITION, CLIMAX, DIVERGENCE, WEAKENING, NONE, UNKNOWN)
STATE_LABELS = {
    ACCELERATION: "加速", MAIN_RISE: "主升", IGNITION: "点火", CLIMAX: "高潮",
    DIVERGENCE: "分歧", WEAKENING: "转弱", NONE: "无", UNKNOWN: "未知",
}
# 「在主线里」的几种状态：从这里掉出来先是分歧 / 转弱，不直接回到「无」
MAINLINE_FAMILY = frozenset({MAIN_RISE, ACCELERATION, CLIMAX, DIVERGENCE})
# 真正「主升过」的状态。分歧不算：分歧只是主升之后的缓冲，不能自己给自己续命
CORE_STATES = frozenset({MAIN_RISE, ACCELERATION, CLIMAX})

# ── 闸门结果 ──────────────────────────────────────────────────────────────────
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
GATES = ("trend", "rs", "ecology", "risk")
CORE_GATES = ("trend", "rs", "ecology")

# ── 涨停趋势 ──────────────────────────────────────────────────────────────────
EXPANDING = "EXPANDING"
STABLE = "STABLE"
CONTRACTING = "CONTRACTING"
SPIKE = "SPIKE"
LU_TREND_LABELS = {EXPANDING: "扩散", STABLE: "平稳", CONTRACTING: "收缩",
                   SPIKE: "单日爆发", UNKNOWN: "未知"}

# ── 阈值（工程初始值；量级出处见模块说明）────────────────────────────────────
MIN_BARS = 26            # MA20 + 它 5 天前的值（算走向）+ 1
LOAD_DAYS = 80           # 读多少个交易日的板块指数（10 天轨迹 + 60 根图 + 均线预热）
CHART_DAYS = 60          # 详情里的指数日线
ECO_CHART_DAYS = 30      # 详情里的生态序列
TRAIL_DAYS = 10          # 状态按天往前推几天（滞回要用前一天的状态）
TRAIL_SHOWN = 5          # 列表里带几天的状态轨迹
RECENT_FAMILY_DAYS = 5   # 最近几天真的主升 / 加速 / 高潮过，掉下来才叫「分歧」「转弱」，否则就是「无」
ECO_DAYS = 6             # 生态看 D-5 ~ D：近 3 日 vs 前 3 日
RS_WINDOWS = (5, 10, 20)
RS_TOP_SHARE = 0.2       # RS10 在关注板块里排前 20%
HOT_DEV20 = 8.0          # 偏离 MA20 ≈ p90：偏热 / 加速
EXTREME_DEV20 = 12.0     # ≈ p99：极端乖离
EXTREME_R5 = 12.0        # 5 日涨幅 ≈ p99
ACCEL_R5 = 6.0           # 5 日涨幅 ≈ p90
ECO_MIN_LU_3D = 3        # 近 3 日涨停合计
ECO_MIN_ACTIVE_DAYS = 2  # 近 3 日里至少 2 天有涨停——成序列，不是一天的事
ECO_MIN_HEIGHT = 2       # 近 3 日出过 2 板
SPIKE_MIN_LU = 4         # 当天 ≥4 只、前两天合计 ≤1 只 = 单日爆发
CONTRACT_RATIO = 0.6     # 近 3 日涨停 ≤ 前 3 日的 60% = 收缩
HEIGHT_DROP = 2          # 高度从 ≥3 板掉了 2 板以上 = 高度在降
SEAL_RATE_LOW = 0.5      # 封板率低于一半（样本 ≥4 只）
SEAL_MIN_SAMPLE = 4
AMOUNT_SURGE = 1.4       # 5 日 / 20 日成交额 ≈ p99：放量
EUPHORIA_LU_3D = 10      # 近 3 日涨停 ≥10 只：涨停板块雷达 08-25 以来有涨停的板块-日里约前 5~10%
CRASH_MIN_LD = 3         # 跌停 ≥3 只且不少于涨停：亏钱效应压过赚钱效应
SETTLED_SHARE_MIN = 0.5  # 那天成分股快照过半已结算，才算收盘后的数据

THRESHOLDS = {
    "min_bars": MIN_BARS, "rs_top_share": RS_TOP_SHARE, "hot_dev20": HOT_DEV20,
    "extreme_dev20": EXTREME_DEV20, "extreme_r5": EXTREME_R5, "accel_r5": ACCEL_R5,
    "eco_min_lu_3d": ECO_MIN_LU_3D, "eco_min_active_days": ECO_MIN_ACTIVE_DAYS,
    "eco_min_height": ECO_MIN_HEIGHT, "spike_min_lu": SPIKE_MIN_LU,
    "contract_ratio": CONTRACT_RATIO, "seal_rate_low": SEAL_RATE_LOW,
    "amount_surge": AMOUNT_SURGE, "euphoria_lu_3d": EUPHORIA_LU_3D,
}


# ═══ 交易日与状态基准日 ══════════════════════════════════════════════════════

def trading_calendar(db: Session, codes: Sequence[str], upto: date) -> List[date]:
    """
    板块指数自己的交易日序列：关注板块里一半以上有 bar 的日子。

    不用上证的日期当日历——上证缺一天时要能说「相对强度不知道」，而不是连那天的
    趋势和生态一起丢掉。
    """
    rows = (db.query(SectorIndexDaily.date, func.count(SectorIndexDaily.id))
            .filter(SectorIndexDaily.sector_code.in_(list(codes)),
                    SectorIndexDaily.date <= upto,
                    SectorIndexDaily.date >= upto - timedelta(days=LOAD_DAYS * 2))
            .group_by(SectorIndexDaily.date).all())
    if not rows:
        return []
    top = max(n for _, n in rows)
    return sorted(d for d, n in rows if n * 2 >= top)


def resolve_state_date(cal: Sequence[date], counts: Dict[date, Tuple[int, int]],
                       now: datetime) -> Tuple[Optional[date], List[str]]:
    """最近一个能当状态基准的交易日，以及为什么跳过了更近的日子。"""
    notes: List[str] = []
    for d in reversed(list(cal)[-5:]):
        if not bar_is_settled(d, now):
            notes.append(f"{d} 还没收盘，状态不用盘中数据")
            continue
        settled, total = counts.get(d, (0, 0))
        if total == 0:
            notes.append(f"{d} 库里还没有成分股日快照，状态基准退到前一交易日")
            continue
        if settled < total * SETTLED_SHARE_MIN:
            notes.append(f"{d} 成分股快照只有 {settled}/{total} 行是收盘终值，"
                         "状态基准退到前一交易日")
            continue
        return d, notes
    return None, notes


# ═══ 读数（全部批量，跟板块数无关的固定几条查询）══════════════════════════════

Bar = Tuple[date, float, Optional[float], Optional[float]]   # 日期、收盘、涨跌幅、成交额（元）


def _load_bars(db: Session, codes: Sequence[str], start: date, end: date) -> Dict[str, List[Bar]]:
    out: Dict[str, List[Bar]] = defaultdict(list)
    for code, d, close, pct, amount in (
            db.query(SectorIndexDaily.sector_code, SectorIndexDaily.date, SectorIndexDaily.close,
                     SectorIndexDaily.pct_change, SectorIndexDaily.amount)
            .filter(SectorIndexDaily.sector_code.in_(list(codes)),
                    SectorIndexDaily.date >= start, SectorIndexDaily.date <= end)
            .order_by(SectorIndexDaily.sector_code, SectorIndexDaily.date)):
        if close and close > 0:
            out[code].append((d, close, pct, amount if amount and amount > 0 else None))
    return out


def member_ecology(db: Session, sector_ids: Sequence[int],
                   days: Sequence[date]) -> Dict[Tuple[int, date], Dict[str, int]]:
    """
    {(板块id, 日期): 成分股当天的涨停/炸板/跌停只数、最高连板、上涨家数、行数、已结算行数}。

    **一条分组查询**，不按板块循环。涨停数跟买入检查同一个口径（成分股日快照里
    is_limit_up 的只数），最高板 = 成分股 board_count 的最大值（也跟买入检查一样）。
    某个板块某天一行都没有 → 不在结果里，调用方当「不知道」，不当 0。
    """
    if not sector_ids or not days:
        return {}
    S = StockDailySnapshot
    flag = lambda col: func.sum(case((col.is_(True), 1), else_=0))  # noqa: E731
    rows = (db.query(StockSectorRelation.sector_id, S.date, func.count(S.id),
                     flag(S.is_settled), flag(S.is_limit_up), flag(S.is_broken_board),
                     flag(S.is_limit_down), func.max(S.board_count),
                     func.sum(case((S.pct_change > 0, 1), else_=0)))
            .join(S, S.stock_id == StockSectorRelation.stock_id)
            .filter(StockSectorRelation.sector_id.in_(list(sector_ids)), S.date.in_(list(days)))
            .group_by(StockSectorRelation.sector_id, S.date).all())
    return {(sid, d): {"rows": int(n), "settled": int(st or 0), "lu": int(lu or 0),
                       "broken": int(bb or 0), "ld": int(ld or 0), "height": int(h or 0),
                       "up": int(up or 0)}
            for sid, d, n, st, lu, bb, ld, h, up in rows}


# ═══ 事实 ════════════════════════════════════════════════════════════════════

def trend_facts(series: Sequence[Bar], pos: int) -> Dict[str, Any]:
    """板块指数截至 series[pos] 的趋势事实。根数不够只回 {"bars": n}。"""
    closes = [b[1] for b in series[:pos + 1]]
    n = len(closes)
    if n < MIN_BARS:
        return {"bars": n}

    def ma(k: int, lag: int = 0) -> float:
        return sum(closes[n - k - lag:n - lag]) / k

    ma5, ma10, ma20, ma20_prev = ma(5), ma(10), ma(20), ma(20, 5)
    close = closes[-1]
    pct = series[pos][2]
    if pct is None:
        pct = (close / closes[-2] - 1) * 100
    amounts = [b[3] for b in series[max(0, pos - 19):pos + 1]]
    missing = sum(1 for a in amounts if a is None) + (20 - len(amounts))
    ratio = None
    if missing == 0:
        ratio = (sum(amounts[-5:]) / 5) / (sum(amounts) / 20)
    return {
        "bars": n, "close": round(close, 2), "pct": round(pct, 2),
        "ma5": round(ma5, 2), "ma10": round(ma10, 2), "ma20": round(ma20, 2),
        "ma20_slope": round((ma20 / ma20_prev - 1) * 100, 2),
        "dev20": round((close / ma20 - 1) * 100, 2),
        "r5": round((close / closes[-6] - 1) * 100, 2),
        "high20": close >= max(closes[-21:-1]),
        "amount_ratio": round(ratio, 2) if ratio is not None else None,
        "amount_missing": missing,
    }


def classify_lu_trend(lu: Sequence[Optional[int]]) -> str:
    """涨停序列 D-5 ~ D 的形状。任何一天不知道 → UNKNOWN。"""
    if len(lu) < ECO_DAYS or any(v is None for v in lu[-ECO_DAYS:]):
        return UNKNOWN
    prev3, last3 = sum(lu[-6:-3]), sum(lu[-3:])
    if lu[-1] >= SPIKE_MIN_LU and lu[-2] + lu[-3] <= 1:
        return SPIKE
    if prev3 >= ECO_MIN_LU_3D and last3 <= prev3 * CONTRACT_RATIO:
        return CONTRACTING
    if last3 >= ECO_MIN_LU_3D and last3 > prev3 and sum(1 for v in lu[-3:] if v) >= 2:
        return EXPANDING
    return STABLE


def ecology_facts(aggs: Sequence[Optional[Dict[str, int]]], members: int) -> Dict[str, Any]:
    """aggs：D-5 ~ D 每天的成分股汇总（member_ecology 的值，不知道的天是 None）。"""
    lu = [a["lu"] if a else None for a in aggs]
    h = [a["height"] if a else None for a in aggs]
    last = aggs[-1] if aggs else None

    def total(xs):
        return sum(xs) if len(xs) == 3 and None not in xs else None

    def top(xs):
        return max(xs) if len(xs) == 3 and None not in xs else None

    f: Dict[str, Any] = {
        "members": members, "lu_series": lu, "height_series": h,
        "lu": last["lu"] if last else None, "broken": last["broken"] if last else None,
        "ld": last["ld"] if last else None, "height": last["height"] if last else None,
        "up_ratio": round(last["up"] / last["rows"], 3) if last and last["rows"] else None,
        "unsettled": (last["rows"] - last["settled"]) if last else None,
        "lu_3d": total(lu[-3:]), "lu_prev3d": total(lu[-6:-3]),
        "height_3d": top(h[-3:]), "height_prev3d": top(h[-6:-3]),
        "lu_trend": classify_lu_trend(lu),
    }
    f["active_days"] = sum(1 for v in lu[-3:] if v) if f["lu_3d"] is not None else None
    f["seal_rate"] = (round(f["lu"] / (f["lu"] + f["broken"]), 3)
                      if last and (f["lu"] + f["broken"]) else None)
    f["height_falling"] = bool(f["height_prev3d"] is not None and f["height_3d"] is not None
                               and f["height_prev3d"] >= 3
                               and f["height_3d"] <= f["height_prev3d"] - HEIGHT_DROP)
    # 「生态在退」：之前确实有过像样的涨停（前 3 日 ≥3 只），现在涨停收缩或高度掉下来
    f["fading"] = bool((f["lu_trend"] == CONTRACTING or f["height_falling"])
                       and (f["lu_prev3d"] or 0) >= ECO_MIN_LU_3D)
    return f


# ═══ 闸门 ════════════════════════════════════════════════════════════════════

def _gate(status: str, reason: str, **extra) -> Dict[str, Any]:
    return {"status": status, "reason": reason, **extra}


def _top_n(n: Optional[int]) -> int:
    return max(1, math.ceil((n or 0) * RS_TOP_SHARE))


def trend_gate(tf: Optional[Dict[str, Any]], day: date) -> Dict[str, Any]:
    if tf is None:
        return _gate(UNKNOWN, f"{day} 没有板块指数收盘")
    if "close" not in tf:
        return _gate(UNKNOWN, f"板块指数只有 {tf['bars']} 根，至少要 {MIN_BARS} 根才算得出 MA20 和它的走向")
    if tf["close"] < tf["ma20"]:
        return _gate(FAIL, f"收盘跌破 MA20（偏离 {tf['dev20']:+.1f}%）")
    stacked = tf["ma5"] > tf["ma10"] > tf["ma20"]
    rising = tf["ma20_slope"] > 0
    above10 = tf["close"] >= tf["ma10"]
    if stacked and rising and above10:
        return _gate(PASS, f"均线多头排列，MA20 五日 {tf['ma20_slope']:+.2f}%，收盘守在 MA10 上方")
    miss = []
    if not stacked:
        miss.append("均线还没多头排列")
    if not rising:
        miss.append(f"MA20 没向上（五日 {tf['ma20_slope']:+.2f}%）")
    if not above10:
        miss.append("收盘跌破 MA10")
    return _gate(WARN, "站在 MA20 上方，但" + "、".join(miss))


def rs_gate(rf: Dict[str, Any]) -> Dict[str, Any]:
    rs10, rs20 = rf.get("rs10"), rf.get("rs20")
    if rs10 is None:
        return _gate(UNKNOWN, rf.get("missing") or "近10日相对强度算不出来")
    if rs10 <= 0:
        return _gate(FAIL, f"近10日跑输上证 {rs10:+.2f} 个百分点")
    rank, n = rf.get("rs10_rank"), rf.get("rs10_n")
    top = rank is not None and rank <= _top_n(n)
    if rs20 is not None and rs20 > 0 and top:
        return _gate(PASS, f"近10日跑赢上证 {rs10:+.2f}、近20日 {rs20:+.2f} 个百分点，"
                           f"RS10 排 {rank}/{n}")
    miss = []
    if rs20 is None:
        miss.append("近20日比不了")
    elif rs20 <= 0:
        miss.append(f"近20日仍跑输（{rs20:+.2f}）")
    if not top:
        miss.append(f"RS10 排 {rank}/{n}，不在前 {int(RS_TOP_SHARE * 100)}%")
    return _gate(WARN, f"近10日跑赢上证 {rs10:+.2f} 个百分点，但" + "、".join(miss))


def ecology_gate(ef: Dict[str, Any]) -> Dict[str, Any]:
    if not ef["members"]:
        return _gate(UNKNOWN, "没有成分股关系，数不了涨停")
    if ef["lu_3d"] is None:
        return _gate(UNKNOWN, "近3个交易日里有的日子没有成分股日快照")
    lu3 = ef["lu_3d"]
    seq = "/".join(str(v) for v in ef["lu_series"][-3:])
    if lu3 <= 1:
        return _gate(FAIL, f"近3日涨停 {seq}，合计 {lu3} 只")
    problems = []
    if ef["lu_trend"] == SPIKE:
        problems.append("单日爆发，前两天几乎没有涨停")
    elif ef["active_days"] < ECO_MIN_ACTIVE_DAYS:
        problems.append("只有 1 天有涨停")
    if lu3 < ECO_MIN_LU_3D:
        problems.append(f"合计只有 {lu3} 只")
    if (ef["height_3d"] or 0) < ECO_MIN_HEIGHT:
        problems.append("没出过 2 板")
    if ef["lu_trend"] == CONTRACTING:
        problems.append(f"涨停在收缩（前3日 {ef['lu_prev3d']} → 近3日 {lu3}）")
    if ef["height_falling"]:
        problems.append(f"高度在降（{ef['height_prev3d']} 板 → {ef['height_3d']} 板）")
    if not problems:
        return _gate(PASS, f"近3日涨停 {seq}，最高 {ef['height_3d']} 板，"
                           f"涨停{LU_TREND_LABELS[ef['lu_trend']]}")
    return _gate(WARN, f"近3日涨停 {seq}：" + "、".join(problems))


def risk_gate(tf: Optional[Dict[str, Any]], ef: Dict[str, Any]) -> Dict[str, Any]:
    """
    过热和见顶迹象。只用看得见的事实：乖离、5 日涨幅、涨停数、封板率、跌停、成交额。
    kind：climax（极端乖离 + 见顶迹象，或涨停、成交也同时极端）/ crash（跌停潮）/ extreme / hot / None。
    """
    if not tf or "close" not in tf:
        return _gate(UNKNOWN, "趋势事实缺失，算不了乖离", kind=None)
    dev, r5, pct, ratio = tf["dev20"], tf["r5"], tf["pct"], tf["amount_ratio"]
    lu, broken, ld = ef.get("lu"), ef.get("broken"), ef.get("ld")
    signals = []
    if ef.get("lu_trend") == CONTRACTING:
        signals.append("涨停收缩")
    if lu is not None and broken is not None and lu + broken >= SEAL_MIN_SAMPLE \
            and lu / (lu + broken) < SEAL_RATE_LOW:
        signals.append(f"封板率 {lu / (lu + broken):.0%}（炸板 {broken} 只）")
    if pct is not None and pct <= -2:
        signals.append(f"指数当日 {pct:+.2f}%")
    if ld is not None and ld >= 2:
        signals.append(f"跌停 {ld} 只")
    if ratio is not None and ratio >= AMOUNT_SURGE and pct is not None and pct <= 0.5:
        signals.append(f"放量滞涨（5日/20日成交额 {ratio:.2f} 倍）")
    if ld is not None and ld >= CRASH_MIN_LD and ld >= (lu or 0):
        return _gate(FAIL, f"跌停 {ld} 只、涨停 {lu} 只，亏钱效应压过赚钱效应", kind="crash")
    ext = f"偏离 MA20 {dev:+.1f}%、5日 {r5:+.1f}%"
    if dev >= EXTREME_DEV20 or r5 >= EXTREME_R5:
        if signals:
            return _gate(FAIL, f"极端乖离（{ext}），且出现" + "、".join(signals), kind="climax")
        lu3 = ef.get("lu_3d")
        if ratio is not None and ratio >= AMOUNT_SURGE and lu3 is not None and lu3 >= EUPHORIA_LU_3D:
            return _gate(FAIL, f"极端乖离（{ext}），涨停（近3日 {lu3} 只）和成交（5日/20日 {ratio:.2f} 倍）"
                               "也同时到了极端——情绪高潮", kind="climax")
        return _gate(WARN, f"极端乖离（{ext}），还没有见顶迹象", kind="extreme")
    if dev >= HOT_DEV20 or signals:
        parts = ([f"偏离 MA20 {dev:+.1f}%，偏热"] if dev >= HOT_DEV20 else []) + signals
        return _gate(WARN, "、".join(parts), kind="hot")
    return _gate(PASS, f"没有过热或见顶迹象（{ext}）", kind=None)


# ═══ 状态 ════════════════════════════════════════════════════════════════════

def classify_state(gates: Dict[str, Dict[str, Any]], tf: Optional[Dict[str, Any]],
                   rf: Dict[str, Any], ef: Dict[str, Any], core_hist: Sequence[bool],
                   trail: Sequence[str]) -> Tuple[str, str]:
    """
    闸门组合 → 状态。core_hist 是截至今天（含）每天三道核心闸是否全过，
    trail 是截至昨天的状态。返回 (状态, 一句话理由)。
    """
    t, r, e, k = (gates[g]["status"] for g in GATES)
    prev = trail[-1] if trail else None
    # 最近 5 天真的主升过（主升 / 加速 / 高潮），掉下来才叫分歧、转弱。
    # 第一版拿「在主线里」（含分歧）判，主升过一次的板块就一直挂在分歧上、跌破 MA20 又变转弱：
    # 用生产同源数据回放，09-11 有 46 个转弱、每天三四十个分歧，真正的主线反而被淹没
    recent = any(s in CORE_STATES for s in trail[-RECENT_FAMILY_DAYS:])

    if t == UNKNOWN:
        return UNKNOWN, gates["trend"]["reason"]
    if t == FAIL:
        if recent:
            return WEAKENING, "刚从主线退下来：" + gates["trend"]["reason"]
        return NONE, gates["trend"]["reason"]
    if r == UNKNOWN or e == UNKNOWN:
        return UNKNOWN, "；".join(gates[g]["reason"] for g in ("rs", "ecology")
                                 if gates[g]["status"] == UNKNOWN)
    if k == FAIL and gates["risk"].get("kind") == "crash":
        return (WEAKENING if recent else NONE), gates["risk"]["reason"]
    if r == FAIL:
        if recent:
            return WEAKENING, "刚从主线退下来：" + gates["rs"]["reason"]
        return NONE, gates["rs"]["reason"]
    if k == FAIL:
        return CLIMAX, gates["risk"]["reason"]
    # 主升之后价格、相对强度、生态三样同时变差（跌破 MA10、5 日跑输上证、涨停收缩或断档）：
    # 这已经不是某一道闸的分歧，是转弱——哪怕还没跌破 MA20
    rs5 = rf.get("rs5")
    if recent and tf["close"] < tf["ma10"] and rs5 is not None and rs5 < 0 \
            and (ef["lu_trend"] == CONTRACTING or e == FAIL):
        return WEAKENING, (f"刚从主线退下来：收盘跌破 MA10、近5日跑输上证 {rs5:+.2f} 个百分点、"
                           + ("涨停在收缩" if ef["lu_trend"] == CONTRACTING else "涨停断档"))

    if core_hist and core_hist[-1]:
        confirmed = sum(core_hist[-3:]) >= 2
        if recent or confirmed:
            if tf["dev20"] >= HOT_DEV20 and tf["r5"] >= ACCEL_R5:
                return ACCELERATION, (f"主升中加速：偏离 MA20 {tf['dev20']:+.1f}%、"
                                      f"5日 {tf['r5']:+.1f}%")
            if recent:
                return MAIN_RISE, "趋势、相对强度、生态三道闸都通过，主升延续"
            return MAIN_RISE, "趋势、相对强度、生态三道闸近3天里有2天全部通过"
        return IGNITION, "三道闸今天第一次全部通过——近3天里要有2天全过才算主升"

    weak = "；".join(gates[g]["reason"] for g in CORE_GATES if gates[g]["status"] != PASS)
    if prev in MAINLINE_FAMILY and recent:
        return DIVERGENCE, "主升中出现分歧：" + weak
    if t == PASS and r == PASS and ef.get("fading"):
        return DIVERGENCE, "K 线还强，但生态在退：" + gates["ecology"]["reason"]
    # 点火只有上面「三道闸第一次全过」那一种。第一版「两道闸过了」「单日涨停爆发」也记点火，
    # 2026-09-13 生产上一天出 41 个点火。用生产同源数据回放 10 天：这两类次日进主升只有
    # 2%（3/156）和 0%（0/18），一半以上次日回到「无」；第一次全过的次日进主升 50%（19/38）。
    # 用户定：点火只留第一次全过，其余记「无」，理由里写清差哪一道
    if sum(1 for g in CORE_GATES if gates[g]["status"] == PASS) == 2:
        return NONE, "三道闸过了两道，还差：" + weak
    return NONE, weak


# ═══ 计算 ════════════════════════════════════════════════════════════════════

def _compute(db: Session, as_of: Optional[date] = None,
             now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(SH_TZ)
    out: Dict[str, Any] = {
        "version": VERSION, "state_date": None, "notes": [],
        "benchmark": {"code": BENCHMARK_CODE, "name": BENCHMARK_NAME},
        "thresholds": THRESHOLDS, "_results": {}, "_bars": {}, "_cal": [], "_sid": {},
    }
    sectors = (db.query(Sector.id, Sector.code, Sector.name, Sector.stock_count)
               .filter(Sector.is_watched.is_(True)).order_by(Sector.id).all())
    if not sectors:
        out["notes"].append("没有关注板块")
        return out
    codes = [s.code for s in sectors]
    cal_all = trading_calendar(db, codes, as_of or now.date())
    if not cal_all:
        out["notes"].append("板块指数日线是空的")
        return out
    counts = settlement_counts(db, cal_all[-(TRAIL_DAYS + ECO_DAYS + 5):])
    state_date, notes = resolve_state_date(cal_all, counts, now)
    out["notes"] = notes
    if state_date is None:
        out["notes"].append("最近几个交易日都没有收盘后的数据，没有可用的状态基准日")
        return out
    cal = [d for d in cal_all if d <= state_date]
    trail_days = cal[-TRAIL_DAYS:]
    eco_days = cal[-(TRAIL_DAYS + ECO_DAYS - 1):]
    start = cal[-LOAD_DAYS] if len(cal) >= LOAD_DAYS else cal[0]

    bars = _load_bars(db, codes, start, state_date)
    bench = {d: c for d, c in db.query(IndexDailySnapshot.date, IndexDailySnapshot.close)
             .filter(IndexDailySnapshot.index_code == BENCHMARK_CODE,
                     IndexDailySnapshot.date >= start - timedelta(days=45),
                     IndexDailySnapshot.date <= state_date)
             if c and c > 0}
    members = dict(db.query(StockSectorRelation.sector_id, func.count(StockSectorRelation.id))
                   .filter(StockSectorRelation.sector_id.in_([s.id for s in sectors]))
                   .group_by(StockSectorRelation.sector_id).all())
    eco = member_ecology(db, [s.id for s in sectors], eco_days)

    if state_date not in bench:
        out["notes"].append(f"上证 {state_date} 没有收盘，相对强度这道闸全是「不知道」")

    # 相对强度：先把每天全体板块的 RS 算出来，才排得出名次
    bdates = sorted(bench)
    bidx = {d: i for i, d in enumerate(bdates)}
    closes = {c: {b[0]: b[1] for b in bars.get(c, [])} for c in codes}
    rs_by_day: Dict[date, Dict[str, Dict[str, Any]]] = {}
    for day in trail_days:
        i = bidx.get(day)
        per: Dict[str, Dict[str, Any]] = {}
        for c in codes:
            rf: Dict[str, Any] = {f"rs{w}": None for w in RS_WINDOWS}
            if i is None:
                rf["missing"] = f"上证 {day} 没有收盘，比不了相对强度"
            elif day not in closes[c]:
                rf["missing"] = f"{day} 没有板块指数收盘"
            else:
                for w in RS_WINDOWS:
                    if i - w >= 0:
                        rf[f"rs{w}"] = rs_vs_benchmark(closes[c], bench, bdates[i - w], day)
                if rf["rs10"] is None:
                    rf["missing"] = "10 个交易日前那天板块指数缺数据，近10日相对强度算不出来"
            per[c] = rf
        ranked = sorted((c for c in codes if per[c]["rs10"] is not None),
                        key=lambda c: -per[c]["rs10"])
        for k, c in enumerate(ranked, 1):
            per[c]["rs10_rank"], per[c]["rs10_n"] = k, len(ranked)
        rs_by_day[day] = per

    cal_pos = {d: i for i, d in enumerate(cal)}
    results: Dict[str, Dict[str, Any]] = {}
    for s in sectors:
        series = bars.get(s.code, [])
        pos = {b[0]: i for i, b in enumerate(series)}
        trail: List[str] = []
        core_hist: List[bool] = []
        days_out: List[Dict[str, Any]] = []
        for day in trail_days:
            tf = trend_facts(series, pos[day]) if day in pos else None
            rf = rs_by_day[day][s.code]
            j = cal_pos[day]
            window = cal[max(0, j - ECO_DAYS + 1):j + 1]
            aggs = [None] * (ECO_DAYS - len(window)) + [eco.get((s.id, d)) for d in window]
            ef = ecology_facts(aggs, members.get(s.id, 0))
            gates = {"trend": trend_gate(tf, day), "rs": rs_gate(rf),
                     "ecology": ecology_gate(ef), "risk": risk_gate(tf, ef)}
            core_hist.append(all(gates[g]["status"] == PASS for g in CORE_GATES))
            state, why = classify_state(gates, tf, rf, ef, core_hist, trail)
            trail.append(state)
            days_out.append({"date": day, "state": state, "reason": why, "gates": gates,
                             "tf": tf, "rf": rf, "ef": ef})
        results[s.code] = {"sector": s, "days": days_out, "members": members.get(s.id, 0),
                           "series": series, "pos": pos}

    # 5 日涨幅在关注板块里的名次（只给排序用；rank_5d 只有前 5 名，这里现算全体）
    last = {c: r["days"][-1] for c, r in results.items()}
    ranked5 = sorted((c for c in codes if last[c]["tf"] and "r5" in last[c]["tf"]),
                     key=lambda c: -last[c]["tf"]["r5"])
    r5_rank = {c: k for k, c in enumerate(ranked5, 1)}
    for c, r in results.items():
        r["r5_rank"] = r5_rank.get(c)

    out.update(state_date=state_date, _results=results, _bars=bars, _cal=cal,
               _sid={s.code: s.id for s in sectors},
               eco_dates=[d.isoformat() for d in cal[-ECO_DAYS:]])
    return out


def _evidence(r: Dict[str, Any], cal: Sequence[date]) -> List[str]:
    """当天的数据质量说明——哪些事实缺了、缺了影响什么。"""
    d = r["days"][-1]
    tf, ef = d["tf"] or {}, d["ef"]
    ev = []
    missing_bars = sum(1 for x in cal[-MIN_BARS:] if x not in r["pos"])
    if missing_bars and r["pos"]:
        ev.append(f"板块指数近 {MIN_BARS} 个交易日缺 {missing_bars} 根，均线跨度会变长")
    if tf.get("amount_missing"):
        ev.append(f"近20日有 {tf['amount_missing']} 天缺板块成交额，「放量」两条（放量滞涨、情绪高潮）没法判断")
    if ef.get("unsettled"):
        ev.append(f"{ef['unsettled']} 只成分股当天的快照不是收盘终值，涨停数可能不全")
    return ev


def _item(r: Dict[str, Any], cal: Sequence[date]) -> Dict[str, Any]:
    s, d = r["sector"], r["days"][-1]
    tf, rf, ef = d["tf"] or {}, d["rf"], d["ef"]
    return {
        "code": s.code, "name": s.name, "stock_count": s.stock_count, "members": r["members"],
        "state": d["state"], "state_label": STATE_LABELS[d["state"]], "state_reason": d["reason"],
        "trail": [{"date": x["date"].isoformat(), "state": x["state"]}
                  for x in r["days"][-TRAIL_SHOWN:]],
        "gates": {g: {"status": d["gates"][g]["status"], "reason": d["gates"][g]["reason"]}
                  for g in GATES},
        "facts": {
            "close": tf.get("close"), "pct": tf.get("pct"), "r5": tf.get("r5"),
            "r5_rank": r["r5_rank"], "dev20": tf.get("dev20"), "ma20_slope": tf.get("ma20_slope"),
            "high20": tf.get("high20"), "amount_ratio": tf.get("amount_ratio"),
            "rs5": rf.get("rs5"), "rs10": rf.get("rs10"), "rs20": rf.get("rs20"),
            "rs10_rank": rf.get("rs10_rank"), "rs10_n": rf.get("rs10_n"),
            "lu": ef["lu"], "lu_series": ef["lu_series"], "lu_3d": ef["lu_3d"],
            "lu_prev3d": ef["lu_prev3d"], "lu_trend": ef["lu_trend"],
            "height": ef["height"], "height_3d": ef["height_3d"], "broken": ef["broken"],
            "seal_rate": ef["seal_rate"], "ld": ef["ld"], "up_ratio": ef["up_ratio"],
        },
        "evidence": _evidence(r, cal),
    }


def _sort_key(item: Dict[str, Any]):
    f = item["facts"]
    return (STATE_ORDER.index(item["state"]),
            -f["rs10"] if f["rs10"] is not None else math.inf,
            f["r5_rank"] if f["r5_rank"] is not None else math.inf,
            -(f["lu_3d"] or 0))


def _public(ctx: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in ctx.items() if not k.startswith("_")}
    if out["state_date"] is not None:
        out["state_date"] = out["state_date"].isoformat()
    return out


# ═══ 对外 ════════════════════════════════════════════════════════════════════

def get_sector_mainline_state(db: Session, as_of: Optional[date] = None,
                              now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    全体关注板块的当前状态（列表接口用）。

    轻量：每个板块只带状态基准日当天的事实、四道闸、最近 5 天的状态轨迹，**不带历史序列**。
    查询条数固定（关注板块、交易日、结算、指数、上证、成分数、生态各一条），跟板块数无关。
    """
    ctx = _compute(db, as_of=as_of, now=now)
    items = sorted((_item(r, ctx["_cal"]) for r in ctx["_results"].values()), key=_sort_key)
    counts = {s: 0 for s in STATE_ORDER}
    for it in items:
        counts[it["state"]] += 1
    return {**_public(ctx), "counts": counts, "sectors": items}


def get_sector_mainline_detail(db: Session, code: str, as_of: Optional[date] = None,
                               now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """
    单个板块：60 根指数日线 + MA5/10/20、30 天成分股生态、近 10 天每天的状态和四道闸。
    相对强度的名次要跟全体比，所以还是整批算一遍——跟列表同一个函数，不会算出两个结果。
    """
    ctx = _compute(db, as_of=as_of, now=now)
    r = ctx["_results"].get(code)
    if r is None:
        return None
    series, cal = r["series"], ctx["_cal"]
    closes = [b[1] for b in series]

    def ma(i: int, k: int) -> Optional[float]:
        return round(sum(closes[i - k + 1:i + 1]) / k, 2) if i + 1 >= k else None

    chart = [{"date": series[i][0].isoformat(), "close": round(series[i][1], 2),
              "pct": series[i][2], "amount": series[i][3],
              "ma5": ma(i, 5), "ma10": ma(i, 10), "ma20": ma(i, 20)}
             for i in range(max(0, len(series) - CHART_DAYS), len(series))]
    eco_days = cal[-ECO_CHART_DAYS:]
    eco = member_ecology(db, [ctx["_sid"][code]], eco_days)
    ecology = []
    for d in eco_days:
        a = eco.get((ctx["_sid"][code], d))
        ecology.append({"date": d.isoformat(),
                        **({k: a[k] for k in ("lu", "height", "broken", "ld")} if a else
                           {"lu": None, "height": None, "broken": None, "ld": None})})
    history = [{"date": x["date"].isoformat(), "state": x["state"],
                "state_label": STATE_LABELS[x["state"]], "reason": x["reason"],
                "gates": {g: {"status": x["gates"][g]["status"], "reason": x["gates"][g]["reason"]}
                          for g in GATES}}
               for x in r["days"]]
    return {**_public(ctx), "sector": _item(r, cal), "history": history,
            "bars": chart, "ecology": ecology}
