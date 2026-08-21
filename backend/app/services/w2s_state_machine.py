"""
弱转强雷达状态机：WATCH → READY → REPAIRING → CONFIRMING → BUYABLE，
两个随时可能触发的侧出口 WAIT / BLOCK。

Phase 1 明确不做 WARNING/EXIT（那是持仓监控状态，本仓库没有持仓/成交跟踪能力，
硬做就是编数据——治理性决策 5）。

核心函数 compute_next_state 是纯函数：不开 DB session，只吃已经查好/算好的
标量输入，返回 (new_state, trigger_reasons, block_reasons)。数据新鲜度判断
和监管风险分级也是纯函数，独立拆出来方便单测和被 refresh_service 复用。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

WATCH = "WATCH"
READY = "READY"
REPAIRING = "REPAIRING"
CONFIRMING = "CONFIRMING"
BUYABLE = "BUYABLE"
WAIT = "WAIT"
BLOCK = "BLOCK"

ALL_STATES = (WATCH, READY, REPAIRING, CONFIRMING, BUYABLE, WAIT, BLOCK)

LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
EXTREME = "EXTREME"


def check_data_freshness(last_refreshed_at: Optional[datetime], now: datetime, max_staleness_seconds: float = 600.0) -> bool:
    """纯函数：数据是否新鲜到可以支撑 BUYABLE 信号。过期 → signal_enabled=False。"""
    if last_refreshed_at is None:
        return False
    age = (now - last_refreshed_at).total_seconds()
    return 0 <= age <= max_staleness_seconds


def classify_regulatory_risk(is_under_regulation: bool, days_since_regulation_lifted: Optional[int]) -> str:
    """
    纯函数：粗粒度监管风险分级（Phase 1 只做 LOW/MEDIUM/HIGH/EXTREME 四档，
    不做完整 0-100 分——规格要求禁止编造"距离停牌还有X%"之类无依据的精确说法）。
    """
    if is_under_regulation:
        return EXTREME
    if days_since_regulation_lifted is not None and days_since_regulation_lifted <= 3:
        return HIGH
    if days_since_regulation_lifted is not None and days_since_regulation_lifted <= 10:
        return MEDIUM
    return LOW


def compute_next_state(
    *,
    current_state: str,
    signal_enabled: bool,
    sector_category: str,
    sector_gate_allowed: set[str],
    leader_type: str,
    regulatory_risk: str,
    regulatory_risk_cap: set[str],
    is_observation_expired: bool,
    price: Optional[float],
    prev_close: Optional[float],
    ma5: Optional[float],
    pullback_low: Optional[float],
    auction_gap: Optional[float],
    auction_gap_min: float,
    is_after_auction: bool,
) -> tuple[str, list[str], list[str]]:
    """
    纯函数：给定当前态 + 一批已算好的闸门输入，算出下一态。
    硬性 BLOCK 判断优先级从高到低：数据过期 → 板块分类不允许 → 龙头非核心/未决
    → 监管风险达到 cap → 候选观察期已过。全部通过后才走结构判断。
    """
    block_reasons: list[str] = []
    trigger_reasons: list[str] = []

    if not signal_enabled:
        block_reasons.append("数据过期，信号已禁用")
    if sector_category not in sector_gate_allowed:
        block_reasons.append(f"板块分类 {sector_category} 不在允许列表")
    if leader_type in ("non_leader", "undetermined"):
        block_reasons.append("龙头未决或非核心龙头" if leader_type == "undetermined" else "非板块核心龙头")
    if regulatory_risk in regulatory_risk_cap:
        block_reasons.append(f"监管风险等级 {regulatory_risk}")
    if is_observation_expired:
        block_reasons.append("候选观察期已过，连续未上榜超过窗口")

    if block_reasons:
        return BLOCK, trigger_reasons, block_reasons

    # 结构判断需要价格数据，缺失则原地 WAIT（不是 BLOCK——只是暂时算不出来）
    if price is None or prev_close is None:
        return WAIT, trigger_reasons, ["缺少现价数据，暂无法判断结构"]

    repair_anchor = max(prev_close, ma5) if ma5 is not None else prev_close

    if is_after_auction and auction_gap is not None and auction_gap >= auction_gap_min and current_state == WATCH:
        trigger_reasons.append(f"竞价Gap {auction_gap:.1f}% 超预期（阈值 {auction_gap_min:.1f}%）")
        current_state = READY

    if current_state in (WATCH, READY) and price > repair_anchor:
        trigger_reasons.append(f"现价 {price:.2f} 收复 max(昨收,MA5)={repair_anchor:.2f}")
        return REPAIRING, trigger_reasons, block_reasons

    if current_state == REPAIRING:
        if price <= repair_anchor:
            return WAIT, ["跌破修复关键位，退回观察"], block_reasons
        if pullback_low is not None and price > pullback_low:
            trigger_reasons.append(f"现价 {price:.2f} 突破回踩低点 {pullback_low:.2f}")
            return CONFIRMING, trigger_reasons, block_reasons
        return REPAIRING, trigger_reasons, block_reasons

    if current_state == CONFIRMING:
        if pullback_low is not None and price <= pullback_low:
            return WAIT, ["跌破回踩低点，确认失败"], block_reasons
        trigger_reasons.append("结构确认完成：板块/龙头/回踩突破三项闸门全部通过")
        return BUYABLE, trigger_reasons, block_reasons

    if current_state == BUYABLE:
        if price <= repair_anchor:
            return WAIT, ["跌破关键位，BUYABLE 信号失效"], block_reasons
        return BUYABLE, trigger_reasons, block_reasons

    return current_state, trigger_reasons, block_reasons
