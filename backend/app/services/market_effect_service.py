"""
市场效应引擎 — 每日赚钱效应 / 亏钱效应（精简 MVP，formula_version=market_effect_v0.1.0）。

核心原则（对应 docs/MARKET_EFFECT_SOLUTION.md）：
  - 冻结群体只用 T-1 日已经写入的 StockDailySnapshot 事实，T 日只做「评价」不做
    「重新筛选成员」，避免幸存者偏差和未来数据泄漏；
  - 全市场广度优先用 MarketBreadthDaily（真实全市场，来自东财 windvane 同步）；
    该表历史缺失时，退化为对 stock_daily_snapshots 当日全部行的统计，并显式标注
    breadth_source="tracked_pool"（跟踪股票池近似，不是全市场），不得混淆展示；
  - 本版本是简化版：固定阈值评分，非文档里的滚动120日分位标准化；简化5态生命
    周期，非文档完整9态滞回状态机。这些留给后续 Phase 2 增强。
"""
import statistics
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from ..models.stock import StockDailySnapshot
from ..models.market_index import MarketBreadthDaily
from ..models.market_effect import MarketEffectDaily
from .snapshot_settlement import settled_by_date

FORMULA_VERSION = "market_effect_v0.1.0"
LARGE_LOSS_THRESHOLD = -7.0  # % ，超过这个跌幅算「大亏」

COHORT_LABELS = {
    "limit_up":     "昨日涨停",
    "first_board":  "昨日首板",
    "multi_board":  "昨日连板",
    "limit_down":   "昨日跌停",
    "broken_board": "昨日炸板",
    "strong_proxy": "强势股池（近似）",
}

QUADRANT_LABELS = {
    "benign_spread":     "良性扩散",
    "strong_divergence": "强分歧",
    "quiet_chaos":       "缩量混沌",
    "loss_spread":       "负反馈扩散",
}

LIFECYCLE_LABELS = {
    "loss_spreading":  "亏钱扩散",
    "recovering":      "修复低迷",
    "profit_confirmed":"赚钱确认",
    "profit_spreading":"赚钱扩散",
    "loss_warning":     "负反馈预警",
}


# ─── 交易日辅助（直接用已有快照日期推导，天然跳过周末/节假日）──────────────────

def _prev_trading_date(db: Session, d: date) -> Optional[date]:
    row = (
        db.query(StockDailySnapshot.date)
        .filter(StockDailySnapshot.date < d)
        .order_by(StockDailySnapshot.date.desc())
        .first()
    )
    return row[0] if row else None


def _next_trading_date(db: Session, d: date) -> Optional[date]:
    row = (
        db.query(StockDailySnapshot.date)
        .filter(StockDailySnapshot.date > d)
        .order_by(StockDailySnapshot.date.asc())
        .first()
    )
    return row[0] if row else None


# ─── 全市场广度 ────────────────────────────────────────────────────────────────

def compute_market_breadth(db: Session, trade_date: date) -> dict:
    breadth_row = db.query(MarketBreadthDaily).filter(MarketBreadthDaily.date == trade_date).first()
    if breadth_row is not None and breadth_row.up_count is not None:
        return {
            "source": "full_market",
            "up_count": breadth_row.up_count,
            "down_count": breadth_row.down_count,
            "flat_count": breadth_row.flat_count,
            "limit_up_count": breadth_row.limit_up_count,
            "limit_down_count": breadth_row.limit_down_count,
            "median_pct_change": None,  # 全市场逐股涨跌幅未落库，暂无法算中位数
            "sample_size": (breadth_row.up_count or 0) + (breadth_row.down_count or 0) + (breadth_row.flat_count or 0),
        }

    # 退化口径：用当日全部已跟踪股票快照统计（不是全市场）
    snaps = (
        db.query(StockDailySnapshot)
        .filter(StockDailySnapshot.date == trade_date, StockDailySnapshot.pct_change.isnot(None))
        .all()
    )
    pct_changes = [s.pct_change for s in snaps]
    up = sum(1 for p in pct_changes if p > 0)
    down = sum(1 for p in pct_changes if p < 0)
    flat = sum(1 for p in pct_changes if p == 0)
    limit_up = sum(1 for s in snaps if s.is_limit_up)
    limit_down = sum(1 for s in snaps if s.is_limit_down)
    return {
        "source": "tracked_pool",
        "up_count": up,
        "down_count": down,
        "flat_count": flat,
        "limit_up_count": limit_up,
        "limit_down_count": limit_down,
        "median_pct_change": statistics.median(pct_changes) if pct_changes else None,
        "sample_size": len(pct_changes),
    }


# ─── 冻结群体定义（全部来自 T-1 日已写入的 StockDailySnapshot 字段）────────────

def _cohort_snapshots(db: Session, cohort_date: date, cohort_type: str) -> list[StockDailySnapshot]:
    q = db.query(StockDailySnapshot).filter(StockDailySnapshot.date == cohort_date)
    if cohort_type == "limit_up":
        q = q.filter(StockDailySnapshot.is_limit_up.is_(True))
    elif cohort_type == "first_board":
        q = q.filter(StockDailySnapshot.is_limit_up.is_(True), StockDailySnapshot.board_count == 1)
    elif cohort_type == "multi_board":
        q = q.filter(StockDailySnapshot.is_limit_up.is_(True), StockDailySnapshot.board_count >= 2)
    elif cohort_type == "limit_down":
        q = q.filter(StockDailySnapshot.is_limit_down.is_(True))
    elif cohort_type == "broken_board":
        q = q.filter(StockDailySnapshot.is_broken_board.is_(True))
    elif cohort_type == "strong_proxy":
        q = q.filter(StockDailySnapshot.top_10_pct_change_20d.is_(True))
    else:
        raise ValueError(f"unknown cohort_type: {cohort_type}")
    return q.all()


def compute_cohort_outcome(db: Session, cohort_date: date, outcome_date: date, cohort_type: str) -> dict:
    members = _cohort_snapshots(db, cohort_date, cohort_type)
    member_count = len(members)
    result = {
        "cohort_type": cohort_type,
        "label": COHORT_LABELS[cohort_type],
        "member_count": member_count,
        "valid_count": 0,
        "median_pct_change": None,
        "red_ratio": None,
        "large_loss_ratio": None,
        "advance_ratio": None,  # 仅连板/涨停类：晋级（板数提高）比例
        "broken_ratio": None,   # 仅连板/涨停类：断板（次日不再涨停）比例
    }
    if member_count == 0:
        return result

    member_ids = [m.stock_id for m in members]
    board_before = {m.stock_id: m.board_count for m in members}
    outcome_snaps = (
        db.query(StockDailySnapshot)
        .filter(StockDailySnapshot.date == outcome_date, StockDailySnapshot.stock_id.in_(member_ids))
        .all()
    )
    valid = [s for s in outcome_snaps if s.pct_change is not None]
    result["valid_count"] = len(valid)
    if not valid:
        return result

    pct_changes = [s.pct_change for s in valid]
    result["median_pct_change"] = round(statistics.median(pct_changes), 2)
    result["red_ratio"] = round(sum(1 for p in pct_changes if p > 0) / len(pct_changes), 3)
    result["large_loss_ratio"] = round(sum(1 for p in pct_changes if p < LARGE_LOSS_THRESHOLD) / len(pct_changes), 3)

    if cohort_type in ("limit_up", "first_board", "multi_board"):
        advanced = sum(1 for s in valid if s.board_count > board_before.get(s.stock_id, 0))
        broken = sum(1 for s in valid if not s.is_limit_up)
        result["advance_ratio"] = round(advanced / len(valid), 3)
        result["broken_ratio"] = round(broken / len(valid), 3)

    return result


def list_cohort_members(db: Session, cohort_date: date, outcome_date: date, cohort_type: str) -> list[dict]:
    """下钻：某个冻结群体的具体成员及次日表现，供证据审计（不作为选股列表）。"""
    from ..models.stock import Stock

    members = _cohort_snapshots(db, cohort_date, cohort_type)
    if not members:
        return []
    member_ids = [m.stock_id for m in members]
    board_before = {m.stock_id: m.board_count for m in members}

    outcome_by_id = {
        s.stock_id: s
        for s in db.query(StockDailySnapshot).filter(
            StockDailySnapshot.date == outcome_date, StockDailySnapshot.stock_id.in_(member_ids)
        )
    }
    stocks_by_id = {s.id: s for s in db.query(Stock).filter(Stock.id.in_(member_ids))}

    rows = []
    for m in members:
        stock = stocks_by_id.get(m.stock_id)
        outcome = outcome_by_id.get(m.stock_id)
        rows.append({
            "code": stock.code if stock else None,
            "name": stock.name if stock else None,
            "board_count_before": board_before.get(m.stock_id),
            "outcome_pct_change": outcome.pct_change if outcome else None,
            "outcome_board_count": outcome.board_count if outcome else None,
            "has_outcome": outcome is not None and outcome.pct_change is not None,
        })
    rows.sort(key=lambda r: (r["outcome_pct_change"] is None, -(r["outcome_pct_change"] or 0)))
    return rows


# ─── 评分与状态（简化版：固定权重加权，非滚动分位标准化）───────────────────────

def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _score_from_breadth(breadth: dict, positive: bool) -> float:
    total = breadth["sample_size"]
    if not total:
        return 50.0
    up, down = breadth["up_count"] or 0, breadth["down_count"] or 0
    ratio = (up / total) if positive else (down / total)
    base = ratio * 100
    if breadth["median_pct_change"] is not None:
        adj = breadth["median_pct_change"] * 8 * (1 if positive else -1)
        base = (base + _clamp(50 + adj)) / 2
    return _clamp(base)


def _score_from_cohorts(cohorts: dict, cohort_types: list[str], positive: bool) -> Optional[float]:
    parts = []
    for ct in cohort_types:
        c = cohorts.get(ct)
        if not c or not c["valid_count"]:
            continue
        red = c["red_ratio"] or 0
        med = c["median_pct_change"] or 0
        ratio_component = red * 100 if positive else (1 - red) * 100
        med_component = _clamp(50 + med * 6) if positive else _clamp(50 - med * 6)
        parts.append((ratio_component + med_component) / 2)
    if not parts:
        return None
    return _clamp(sum(parts) / len(parts))


def compute_daily_effect(db: Session, trade_date: date) -> dict:
    breadth = compute_market_breadth(db, trade_date)
    prev_date = _prev_trading_date(db, trade_date)

    cohorts = {}
    if prev_date is not None:
        for ct in COHORT_LABELS:
            cohorts[ct] = compute_cohort_outcome(db, prev_date, trade_date, ct)

    profit_breadth_score = _score_from_breadth(breadth, positive=True)
    loss_breadth_score = _score_from_breadth(breadth, positive=False)
    profit_cohort_score = _score_from_cohorts(cohorts, ["limit_up", "multi_board", "strong_proxy"], positive=True)
    loss_cohort_score = _score_from_cohorts(cohorts, ["limit_down", "broken_board"], positive=False)
    # 涨停/连板群体的「大亏」比例同样计入亏钱效应（热门群体负反馈是最强证据）
    hot_large_loss = [
        cohorts[ct]["large_loss_ratio"] for ct in ("limit_up", "multi_board")
        if cohorts.get(ct) and cohorts[ct]["large_loss_ratio"] is not None
    ]
    if hot_large_loss:
        hot_loss_score = _clamp(sum(hot_large_loss) / len(hot_large_loss) * 100)
        loss_cohort_score = hot_loss_score if loss_cohort_score is None else (loss_cohort_score + hot_loss_score) / 2

    profit_strength = round(
        0.4 * profit_breadth_score + 0.6 * profit_cohort_score
        if profit_cohort_score is not None else profit_breadth_score, 1
    )
    loss_strength = round(
        0.4 * loss_breadth_score + 0.6 * loss_cohort_score
        if loss_cohort_score is not None else loss_breadth_score, 1
    )

    profit_high, loss_high = profit_strength >= 55, loss_strength >= 55
    if profit_high and not loss_high:
        quadrant = "benign_spread"
    elif profit_high and loss_high:
        quadrant = "strong_divergence"
    elif not profit_high and not loss_high:
        quadrant = "quiet_chaos"
    else:
        quadrant = "loss_spread"

    # 简化5态：仅用当日两分数判断，不做跨日滞回（v0.1，后续可加趋势确认）
    if quadrant == "loss_spread":
        lifecycle_state = "loss_spreading"
    elif quadrant == "quiet_chaos":
        lifecycle_state = "recovering"
    elif quadrant == "strong_divergence":
        lifecycle_state = "loss_warning"
    else:
        lifecycle_state = "profit_spreading" if profit_strength >= 70 else "profit_confirmed"

    evidence = [{
        "metric": "market_breadth",
        "raw_value": breadth,
        "sample_size": breadth["sample_size"],
        "direction": "positive" if (breadth["up_count"] or 0) >= (breadth["down_count"] or 0) else "negative",
    }]
    for ct, c in cohorts.items():
        if c["valid_count"]:
            evidence.append({
                "metric": f"cohort_{ct}_next_day_return",
                "raw_value": c,
                "sample_size": c["valid_count"],
                "direction": "positive" if (c["median_pct_change"] or 0) >= 0 else "negative",
            })

    breadth_desc = (
        f"{'全市场' if breadth['source'] == 'full_market' else '跟踪股票池'}"
        f"上涨{breadth['up_count']}只、下跌{breadth['down_count']}只"
    )
    hot = cohorts.get("limit_up")
    hot_desc = ""
    if hot and hot["valid_count"]:
        hot_desc = f"；昨日涨停股次日收益中位数{hot['median_pct_change']}%，红盘率{round(hot['red_ratio'] * 100)}%"
    summary = f"今日处于{QUADRANT_LABELS[quadrant]}状态。{breadth_desc}{hot_desc}。"

    return {
        "trade_date": trade_date,
        "profit_strength": profit_strength,
        "loss_strength": loss_strength,
        "quadrant": quadrant,
        "lifecycle_state": lifecycle_state,
        "breadth_source": breadth["source"],
        "coverage_ratio": 1.0 if breadth["source"] == "full_market" else min(1.0, breadth["sample_size"] / 500),
        "cohorts": cohorts,
        "evidence": evidence,
        "summary": summary,
    }


def compute_and_cache(db: Session, trade_date: date) -> MarketEffectDaily:
    result = compute_daily_effect(db, trade_date)
    row = db.query(MarketEffectDaily).filter(MarketEffectDaily.trade_date == trade_date).first()
    if row is None:
        row = MarketEffectDaily(trade_date=trade_date)
        db.add(row)
    row.profit_strength = result["profit_strength"]
    row.loss_strength = result["loss_strength"]
    row.quadrant = result["quadrant"]
    row.lifecycle_state = result["lifecycle_state"]
    row.breadth_source = result["breadth_source"]
    row.coverage_ratio = result["coverage_ratio"]
    row.cohorts_json = result["cohorts"]
    row.evidence_json = result["evidence"]
    row.summary = result["summary"]
    row.formula_version = FORMULA_VERSION
    db.commit()
    db.refresh(row)
    return row


def get_or_compute(db: Session, trade_date: date) -> MarketEffectDaily:
    row = db.query(MarketEffectDaily).filter(MarketEffectDaily.trade_date == trade_date).first()
    if row is not None and row.formula_version == FORMULA_VERSION:
        return row
    return compute_and_cache(db, trade_date)


def get_latest_trade_date(db: Session) -> Optional[date]:
    row = db.query(StockDailySnapshot.date).order_by(StockDailySnapshot.date.desc()).first()
    return row[0] if row else None


def recent_trade_dates(db: Session, days: int) -> list[date]:
    """最近 days 个有快照的交易日，升序。"""
    latest = get_latest_trade_date(db)
    if latest is None:
        return []
    all_dates = [
        d for (d,) in (
            db.query(StockDailySnapshot.date)
            .filter(StockDailySnapshot.date <= latest)
            .distinct()
            .order_by(StockDailySnapshot.date.desc())
            .limit(days)
            .all()
        )
    ]
    all_dates.reverse()
    return all_dates


def get_history(db: Session, days: int) -> list[MarketEffectDaily]:
    """
    近 days 个交易日的效应行，升序。缺失的现算并落库（语义跟 get_or_compute 一致）。

    **命中的行一次查出来**，不要按天循环调 get_or_compute——那是 60 次
    `WHERE trade_date = ?`。这条路现在有两个页面在走（市场效应页的趋势图、
    涨跌停分析页的逐日曲线），而这台机器只有 1.8G。
    """
    all_dates = recent_trade_dates(db, days)
    if not all_dates:
        return []
    cached = {
        r.trade_date: r
        for r in db.query(MarketEffectDaily).filter(
            MarketEffectDaily.trade_date.in_(all_dates),
            # 版本对不上的行等于没有：跟 get_or_compute 一样重算
            MarketEffectDaily.formula_version == FORMULA_VERSION,
        )
    }
    return [cached.get(d) or compute_and_cache(db, d) for d in all_dates]


# ─── 缓存重算 ──────────────────────────────────────────────────────────────────
#
# get_or_compute 只看 formula_version：一行一旦算出就不再更新。可它依赖的快照在
# 算完之后还会变，而且变的方向是**系统性**的：
#   · 当天：第一次被请求时是几点，缓存就停在几点。生产上 09-10 那行一直停在
#     盘中 12 点的 28/48，收盘后的日更也没动它
#   · 次日：昨日群体里掉出候选池的票，当天没有快照行，第二天日更的 10 日 dump
#     补历史才补上。于是缓存只算了"还留在池子里的"——也就是走得好的那些
# 2026-09-11 对生产核对：08-20 昨日涨停缓存 +4.25%（13/36），全部 38 只算是 -3.26%；
# 09-10 缓存 +3.83%（28/48），全部 48 只是 -1.04%。**符号都是反的**。

def refresh_effects(db: Session, dates) -> int:
    """按当前快照重算这些交易日的效应行，覆盖缓存。返回重算的天数。"""
    n = 0
    for d in dates:
        compute_and_cache(db, d)
        n += 1
    return n


def refresh_recent(db: Session, days: int = 12) -> int:
    """
    重算最近 days 个交易日（含今天）。日更写完快照、补完历史、对完涨跌停之后调用。

    12 = 10 日 dump 的覆盖（它补的历史行会改动这些天的次日结果）+ 余量。
    """
    return refresh_effects(db, recent_trade_dates(db, days))


# ─── 冻结群体的逐日曲线 ────────────────────────────────────────────────────────
#
# 「昨日群体·今日反馈」那张表是一天的截面，这里是同一批数字沿日期铺开。
# **两者必须是同一个字段**（cohorts_json 里的 median_pct_change），否则曲线
# 最后一点和它下面那张表会对不上——那就是同一个事实两套口径。

def build_cohort_series(
    rows: list[tuple[date, Optional[dict]]],
    settled: dict[date, Optional[bool]],
) -> dict:
    """
    纯函数：(交易日, cohorts_json) 列表 → 曲线载荷。

    **median 缺失就是缺失**（valid_count 不足 / 当天这个群体一个成员都没有），
    不填 0——0% 是"这群票不涨不跌"，跟"没有这群票"是两回事。member_count 和
    valid_count 一起带出去，让前端能在 tooltip 里说清是哪一种。
    """
    points = []
    seen: list[str] = []
    for d, cohorts in rows:
        values = {}
        for ct in COHORT_LABELS:
            c = (cohorts or {}).get(ct)
            if not c:
                continue
            if ct not in seen:
                seen.append(ct)
            values[ct] = {
                "median_pct_change": c.get("median_pct_change"),
                "member_count": c.get("member_count") or 0,
                "valid_count": c.get("valid_count") or 0,
            }
        points.append({
            "trade_date": d,
            # True=收盘终值 / False=还有盘中行 / None=不知道
            "is_settled": settled.get(d),
            "values": values,
        })

    as_of = points[-1]["trade_date"] if points else None
    notes = [
        "每条线 = 前一交易日收盘时冻结的那批票，在这一天的**中位涨跌幅**——"
        "跟下面「昨日群体 · 今日反馈」表里的「今日中位收益」是同一个字段。",
        "有效样本不足或当天这个群体没有成员时，接口给的是 null；曲线为了不碎成"
        "一段一段按 0 接过去，**悬停仍然显示「—」和样本数**，0 不代表「不涨不跌」。",
    ]
    if as_of is not None and points[-1]["is_settled"] is False:
        v = points[-1]["values"]
        cov = ", ".join(
            f"{COHORT_LABELS[ct]} {x['valid_count']}/{x['member_count']}"
            for ct, x in v.items() if x["member_count"]
        )
        notes.append(
            f"最后一点（{as_of}）**还没收盘**：用的是盘中价，而且只覆盖了部分成员"
            + (f"（{cov}）" if cov else "")
            + "，收盘后会变。")

    return {
        "as_of": as_of,
        "formula_version": FORMULA_VERSION,
        "cohorts": [{"cohort_type": ct, "label": COHORT_LABELS[ct]} for ct in seen],
        "points": points,
        "notes": notes,
    }


def get_cohort_series(db: Session, days: int) -> dict:
    rows = get_history(db, days)
    settled = settled_by_date(db, [r.trade_date for r in rows])
    return build_cohort_series([(r.trade_date, r.cohorts_json) for r in rows], settled)
