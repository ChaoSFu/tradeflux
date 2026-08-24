"""
弱转强雷达 Market Gate（Phase 2，2026-08-22 二次修订）：Market Trend Score
（核心指数趋势）+ Risk Appetite Score（涨跌家数/涨跌停比/T-1冻结群体次日反馈）
→ GREEN/YELLOW/ORANGE/RED。

不新起数据管道——三部分数据都基于已有的、daily_update 每天同步的数据：
指数趋势复用 index_trend_service.get_market_trend()（读库，不额外打接口），
涨跌家数/涨跌停比复用 MarketBreadthDaily（大盘趋势页同一份数据源），T-1冻结
群体反馈复用 market_effect_service（赚钱/亏钱效应引擎，"昨日涨停/首板/连板/
炸板/跌停"这批股票今天的真实表现）。

二次修订换掉了两融余额5日变化率这个分量：两融更接近中短期资金背景，跟
"今天9:35能不能做弱转强"这种日内决策相关性不够强；换成冻结群体次日反馈后，
Risk Appetite 才是真正在回答"市场今天有没有承接弱转强的情绪环境"。

核心打分/分类函数是纯函数，DB 相关的取数逻辑单独放 get_market_gate()。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from ..models.market_index import MarketBreadthDaily
from .index_trend_service import get_market_trend
from . import market_effect_service

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
    market_effect_profit_strength: Optional[float],
    market_effect_loss_strength: Optional[float],
    market_effect_confidence: str = "NORMAL",
) -> Optional[float]:
    """
    纯函数：风险偏好分（0-100）= 涨跌家数比(0-35) + 涨跌停比(0-25) + 冻结群体
    净反馈(0-40)。涨跌家数是硬性输入，缺失直接返回 None（不能编造中性值掩盖
    数据缺失）；涨跌停/冻结群体反馈任一缺失时该子项给中性分（容忍度更高，
    不是唯一判断依据）。market_effect_confidence="LOW"（对应 market_effect
    的 breadth_source=tracked_pool，全市场广度退化为跟踪池近似）时冻结群体
    分量权重减半，不能让近似数据跟真实全市场数据享受同等话语权。
    """
    if up_count is None or down_count is None:
        return None
    total = up_count + down_count
    updown_score = _clamp((up_count / total) * 35, 0, 35) if total > 0 else 17.5

    if limit_up_count is not None and limit_down_count is not None and (limit_up_count + limit_down_count) > 0:
        lt = limit_up_count + limit_down_count
        limit_score = _clamp((limit_up_count / lt) * 25, 0, 25)
    else:
        limit_score = 12.5

    if market_effect_profit_strength is not None and market_effect_loss_strength is not None:
        # profit/loss_strength 各自 0-100，独立评分不是互补关系；两者之差代表净偏向，
        # /2 后 ±50 夹到 0-40 区间、20 为中性中点。
        net = market_effect_profit_strength - market_effect_loss_strength
        weight = 0.5 if market_effect_confidence == "LOW" else 1.0
        effect_score = _clamp(20 + net / 2 * weight, 0, 40)
    else:
        effect_score = 20.0

    return round(_clamp(updown_score + limit_score + effect_score), 1)


def classify_market_negative_feedback(loss_strength: Optional[float]) -> str:
    """
    纯函数：把 T-1 冻结群体的 loss_strength（0-100）单独分级成 LOW/MEDIUM/HIGH，
    不再只让它作为 Risk Appetite Score 里被抵消掉的一个分量存在。round3 审阅
    指出这类"市场负反馈"信号被埋没在一个复合分数里，用户在界面上看不出"今天
    风险偏好分低，到底是因为普涨不够，还是因为昨天龙头今天集体大面"——两者
    应对策略完全不同，所以单独暴露。缺数据时返回 UNKNOWN，不假装是 LOW。
    """
    if loss_strength is None:
        return "UNKNOWN"
    if loss_strength >= 60:
        return "HIGH"
    if loss_strength >= 35:
        return "MEDIUM"
    return "LOW"


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
    {trend_score, risk_score, market_state, index_scores,
     market_effect_date, market_effect_confidence, as_of_date,
     trend_as_of, breadth_as_of}。
    全部读库，不额外发起外部请求。

    market_effect 部分用"最近一个已收盘、有完整数据的交易日"而不是"今天"——
    market_effect_service 的冻结群体反馈需要 T 日的 StockDailySnapshot 才能
    算出"T-1群体在T日的真实表现"，盘中今天的快照还没写入，直接传"今天"进去
    要么算不出来要么算出空结果。用最近收盘日的结果，代表"最近一次已知的市场
    承接环境"，是盘中能拿到的最新真实信号。

    2026-08-24新增 trend_as_of/breadth_as_of（外部评审指出的真实问题）：
    Market Gate 实际由三段不同刷新节奏的数据拼成（指数趋势/涨跌家数广度/T-1
    市场效应），此前只把 as_of_date（=breadth的日期）当成整个 Market Gate的
    新鲜度代表——三段各自都有独立的刷新链路，任何一段掉线时都不会体现在唯一
    的 as_of_date 上（正是 windvane 涨跌统计连续7天静默失败这次事故暴露出来
    的）。trend_as_of 直接来自 get_market_trend() 早就在算的 updated_at，之前
    只是没有透出来，不是新查询。三段状态是否新鲜、如何在展示态上处理（要不要
    降级/拦截），留给调用方（router/前端）按各自阈值判断，这里只如实透出三个
    原始时间戳，不在这一层就下"新鲜/过期"的结论。
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

    effect_profit: Optional[float] = None
    effect_loss: Optional[float] = None
    effect_date: Optional[str] = None
    effect_confidence = "NORMAL"
    latest_trade_date = market_effect_service.get_latest_trade_date(db)
    if latest_trade_date is not None:
        try:
            effect_row = market_effect_service.get_or_compute(db, latest_trade_date)
            effect_profit = effect_row.profit_strength
            effect_loss = effect_row.loss_strength
            effect_date = str(effect_row.trade_date)
            effect_confidence = "LOW" if effect_row.breadth_source == "tracked_pool" else "NORMAL"
        except Exception:  # noqa: BLE001
            pass  # 市场效应算不出来时该分量退化为中性分，不阻断 Market Gate 其余部分

    risk_score = compute_risk_appetite_score(
        up_count=latest.up_count if latest else None,
        down_count=latest.down_count if latest else None,
        limit_up_count=latest.limit_up_count if latest else None,
        limit_down_count=latest.limit_down_count if latest else None,
        market_effect_profit_strength=effect_profit,
        market_effect_loss_strength=effect_loss,
        market_effect_confidence=effect_confidence,
    )
    market_state = classify_market_state(trend_score, risk_score)

    breadth_as_of = str(latest.date) if latest else None
    return {
        "trend_score": trend_score,
        "risk_score": risk_score,
        "market_state": market_state,
        "index_scores": index_scores,
        "market_effect_date": effect_date,
        "market_effect_confidence": effect_confidence,
        "market_effect_profit_strength": effect_profit,
        "market_effect_loss_strength": effect_loss,
        "market_negative_feedback": classify_market_negative_feedback(effect_loss),
        "as_of_date": breadth_as_of,  # 沿用旧字段名，语义不变（=breadth_as_of），避免破坏现有调用方
        "trend_as_of": (trend_resp.updated_at or "")[:10] or None,
        "breadth_as_of": breadth_as_of,
    }
