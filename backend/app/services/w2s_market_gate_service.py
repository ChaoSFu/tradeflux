"""
弱转强雷达 Market Gate（Phase 2）：Market Trend Score（核心指数趋势）+
Risk Appetite Score（涨跌家数/涨跌停比/两融余额变化）→ GREEN/YELLOW/ORANGE/RED。

不新起数据管道——两个分数都基于已有的、daily_update 每天同步的数据：
指数趋势复用 index_trend_service.get_market_trend()（读库，不额外打接口），
风险偏好复用 MarketBreadthDaily（两融/涨跌统计，大盘趋势页同一份数据源）。

核心打分/分类函数是纯函数，DB 相关的取数逻辑单独放 get_market_gate()。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ..models.market_index import MarketBreadthDaily
from .index_trend_service import get_market_trend

GREEN = "GREEN"
YELLOW = "YELLOW"
ORANGE = "ORANGE"
RED = "RED"

# 核心指数加权（上证40%+深证35%+创业板25%）——排除科创50/北证50，
# 这两个盘子小、噪声大，跟 Sector Gate 排除动态噪声板块是同一个考量。
CORE_INDEX_WEIGHTS: dict[str, float] = {"000001": 0.40, "399001": 0.35, "399006": 0.25}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def compute_market_trend_score(index_scores: dict[str, float]) -> Optional[float]:
    """纯函数：核心指数趋势分加权平均。三个核心指数任一缺失 → None（不能悄悄拿两个当三个用）。"""
    if not all(code in index_scores for code in CORE_INDEX_WEIGHTS):
        return None
    return round(sum(index_scores[code] * w for code, w in CORE_INDEX_WEIGHTS.items()), 1)


def compute_risk_appetite_score(
    *,
    up_count: Optional[int],
    down_count: Optional[int],
    limit_up_count: Optional[int],
    limit_down_count: Optional[int],
    margin_balance_chg_pct: Optional[float],
) -> Optional[float]:
    """
    纯函数：风险偏好分（0-100）= 涨跌家数比(0-40) + 涨跌停比(0-30) + 两融余额5日变化率(0-30)。
    涨跌家数是硬性输入，缺失直接返回 None（不能编造中性值掩盖数据缺失）；
    涨跌停/两融任一缺失时该子项给中性一半分（不是主判断依据，容忍度更高）。
    """
    if up_count is None or down_count is None:
        return None
    total = up_count + down_count
    updown_score = _clamp((up_count / total) * 40, 0, 40) if total > 0 else 20.0

    if limit_up_count is not None and limit_down_count is not None and (limit_up_count + limit_down_count) > 0:
        lt = limit_up_count + limit_down_count
        limit_score = _clamp((limit_up_count / lt) * 30, 0, 30)
    else:
        limit_score = 15.0

    if margin_balance_chg_pct is not None:
        # ±5% 5日变化映射到 0-30，线性夹住（两融余额扩张=加杠杆意愿上升=风险偏好高）
        margin_score = _clamp(15 + margin_balance_chg_pct * 3, 0, 30)
    else:
        margin_score = 15.0

    return round(_clamp(updown_score + limit_score + margin_score), 1)


def classify_market_state(trend_score: Optional[float], risk_score: Optional[float]) -> str:
    """
    纯函数：双分数 → 四色。取"较弱的一侧"决定档位（跟 Sector/Leader Gate 一样，
    闸门看短板不看平均分），任一分数缺失时保守判 ORANGE（不敢判 GREEN，也不夸大到 RED）。
    """
    if trend_score is None or risk_score is None:
        return ORANGE
    if trend_score >= 55 and risk_score >= 55:
        return GREEN
    worst = min(trend_score, risk_score)
    if worst >= 35:
        return YELLOW
    if worst >= 20:
        return ORANGE
    return RED


def get_market_gate(db: Session) -> dict:
    """
    薄封装：查现成数据、算两个分数、分类。返回
    {trend_score, risk_score, market_state, index_scores, as_of_date}。
    全部读库，不额外发起外部请求（指数趋势/两融数据均由 daily_update 每日同步）。
    """
    trend_resp = get_market_trend(db)
    index_scores = {ix.code: float(ix.score) for ix in trend_resp.indices}
    trend_score = compute_market_trend_score(index_scores)

    latest = (
        db.query(MarketBreadthDaily)
        .filter(MarketBreadthDaily.up_count.isnot(None))
        .order_by(MarketBreadthDaily.date.desc())
        .first()
    )
    margin_chg: Optional[float] = None
    if latest and latest.margin_balance:
        prior = (
            db.query(MarketBreadthDaily)
            .filter(MarketBreadthDaily.date < latest.date, MarketBreadthDaily.margin_balance.isnot(None))
            .order_by(MarketBreadthDaily.date.desc())
            .offset(4)
            .first()
        )
        if prior and prior.margin_balance:
            margin_chg = (latest.margin_balance - prior.margin_balance) / prior.margin_balance * 100

    risk_score = compute_risk_appetite_score(
        up_count=latest.up_count if latest else None,
        down_count=latest.down_count if latest else None,
        limit_up_count=latest.limit_up_count if latest else None,
        limit_down_count=latest.limit_down_count if latest else None,
        margin_balance_chg_pct=margin_chg,
    )
    market_state = classify_market_state(trend_score, risk_score)

    return {
        "trend_score": trend_score,
        "risk_score": risk_score,
        "market_state": market_state,
        "index_scores": index_scores,
        "margin_balance_chg_pct": round(margin_chg, 2) if margin_chg is not None else None,
        "as_of_date": str(latest.date) if latest else None,
    }
