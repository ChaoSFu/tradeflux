"""
弱转强雷达状态机（2026-08-22 重构：结构判断与闸门判断解耦）。

**为什么拆开**：旧版本 compute_next_state 把"价格结构走到哪一步"和"当前是否被
大盘/板块/龙头闸门拦截"糅进同一次判断——一旦任何闸门不通过就整体判 BLOCK，
且 BLOCK 没有任何路径能跳出来（所有状态转移分支都不匹配 current_state=="BLOCK"，
即使下一次刷新闸门全部转好也永远停留在 BLOCK）。这是真实存在的死态 bug，不是
设计取舍。同时旧版"CONFIRMING"的判定是 `price > pullback_low`（pullback_low
只是进入 REPAIRING 以来的滚动最低价），意味着任意一次微小回调后的一个小反弹
就会触发 CONFIRMING——不是"突破第一次修复高点"，是"比刚才那一tick高"，噪音
级别的确认，不是真正的回踩确认结构。

**现在的设计**：
- structural_state：只看价格行为，完全不理会任何闸门，每次刷新都照常基于
  真实价格推进（H1/L1 两段式，见下）。即使展示态是 BLOCK，底层结构追踪也
  不会被打断——大盘/龙头这类环境条件会变化，候选价格结构本身没有失效，不该
  被环境的临时波动清零。
- current_state（展示态）= structural_state 叠加闸门覆盖后的最终值：
  硬性闸门（数据过期/大盘/板块/龙头非核心/监管/观察期）不通过 → 展示 BLOCK，
  但 structural_state 照常在底层推进，闸门一旦恢复，展示立刻反映真实进度，
  不需要从 WATCH 重新走一遍。
  板块"刚起步"（NEW_START）→ 软上限，展示态最高只到 READY，不跟已证明过自己
  强、现在出现分歧的核心弱转强模式享受同等的 REPAIRING/CONFIRMING/BUYABLE 待遇。
  龙头"未决"（不是 non_leader，是两个候选分不出谁是真龙头）→ 软上限，展示态
  最高只到 CONFIRMING，不隐藏正在发生的结构，但不能显示还没真正确立龙头就
  给的 BUYABLE。
  涨停空间不足 → 软上限，只在 structural_state 已经是 BUYABLE 时把展示态降级
  为 WAIT，底层 structural_state 保留 BUYABLE 不清空——空间会随价格逐分钟
  变化，候选本身没有失效，空间一旦重新充足展示立刻恢复，不用重新走一遍结构。

结构状态机（H1/L1 两段式，替代旧版"任意反弹即确认"的弱定义）：
  WATCH/READY/WAIT → [现价 > repair_anchor=max(昨收,VWAP或MA5)]  → REPAIRING
                                                                    （recovery_high=现价，开始建H1）
  REPAIRING        → [现价 ≤ repair_anchor]                      → WAIT（结构失效，清空H1/L1追踪）
                    → [现价创新高]                                → REPAIRING（recovery_high 上移，H1 未回踩前持续抬高）
                    → [现价回落超过 pullback_min_pct]              → CONFIRMING（冻结 recovery_high=H1，开始记 pullback_low=L1）
  CONFIRMING       → [现价 ≤ repair_anchor]                      → WAIT（跌穿回到起点，回踩确认失败）
                    → [现价 > 冻结的 recovery_high]                → BUYABLE（二次突破H1，回踩结构确认完成）
                    → [否则]                                      → CONFIRMING（继续记录 pullback_low）
  BUYABLE          → [现价 ≤ repair_anchor]                      → WAIT（信号失效，清空追踪）
                    → [否则]                                      → BUYABLE

Phase 1/2 明确不做 WARNING/EXIT（持仓监控态，本仓库无持仓/成交跟踪能力，硬做
就是编数据）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .w2s_risk_service import evaluate_space_gate

WATCH = "WATCH"
READY = "READY"
REPAIRING = "REPAIRING"
CONFIRMING = "CONFIRMING"
BUYABLE = "BUYABLE"
WAIT = "WAIT"
BLOCK = "BLOCK"

ALL_STATES = (WATCH, READY, REPAIRING, CONFIRMING, BUYABLE, WAIT, BLOCK)
STRUCTURAL_STATES = (WATCH, READY, REPAIRING, CONFIRMING, BUYABLE, WAIT)

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


def compute_structural_transition(
    *,
    structural_state: str,
    price: Optional[float],
    prev_close: Optional[float],
    vwap: Optional[float],
    ma5: Optional[float],
    recovery_high: Optional[float],
    pullback_low: Optional[float],
    pullback_started: bool,
    pullback_min_pct: float,
    auction_gap: Optional[float],
    auction_gap_min: float,
    is_after_auction: bool,
) -> dict:
    """
    纯函数：只看价格结构，完全不理会任何闸门。返回
    {new_structural_state, recovery_high, pullback_low, pullback_started, trigger_reasons}。
    repair_anchor 优先用真实 VWAP（当日成交额/成交量算出，比5日线更能代表"今天
    这批买盘的平均成本"），VWAP 缺失（比如刚开盘还没有成交）时退回 MA5。
    """
    if price is None or prev_close is None:
        return {
            "new_structural_state": structural_state,
            "recovery_high": recovery_high, "pullback_low": pullback_low,
            "pullback_started": pullback_started,
            "trigger_reasons": ["缺少现价数据，暂无法判断结构"],
        }

    anchor_ref = vwap if vwap is not None else ma5
    repair_anchor = max(prev_close, anchor_ref) if anchor_ref is not None else prev_close

    state = structural_state
    trigger_reasons: list[str] = []
    if is_after_auction and auction_gap is not None and auction_gap >= auction_gap_min and state == WATCH:
        trigger_reasons.append(f"竞价Gap {auction_gap:.1f}% 超预期（阈值 {auction_gap_min:.1f}%）")
        state = READY

    # 重新收复关键位：WATCH/READY/WAIT 都可能在价格重新站上 repair_anchor 时进入 REPAIRING
    # （WAIT 不是死态，价格随时可能再次收复，跟 WATCH/READY 走同一条重新进入的路）。
    if state in (WATCH, READY, WAIT) and price > repair_anchor:
        trigger_reasons.append(f"现价 {price:.2f} 收复 max(昨收,VWAP/MA5)={repair_anchor:.2f}")
        return {
            "new_structural_state": REPAIRING,
            "recovery_high": price, "pullback_low": None, "pullback_started": False,
            "trigger_reasons": trigger_reasons,
        }

    if state == REPAIRING:
        if price <= repair_anchor:
            return {
                "new_structural_state": WAIT,
                "recovery_high": None, "pullback_low": None, "pullback_started": False,
                "trigger_reasons": ["跌破修复关键位，结构失效，退回观察"],
            }
        rh = recovery_high if recovery_high is not None else price
        if price >= rh:
            # H1 还没形成有效回踩前持续抬高，还在建第一段修复高点
            return {
                "new_structural_state": REPAIRING,
                "recovery_high": price, "pullback_low": None, "pullback_started": False,
                "trigger_reasons": trigger_reasons,
            }
        if price < rh * (1 - pullback_min_pct / 100):
            # 回落幅度超过噪音阈值，判定为真正开始回踩：冻结 H1，开始记录 L1
            trigger_reasons.append(f"现价 {price:.2f} 较修复高点 {rh:.2f} 回踩超过 {pullback_min_pct:.1f}%，形成有效回踩")
            return {
                "new_structural_state": CONFIRMING,
                "recovery_high": rh, "pullback_low": price, "pullback_started": True,
                "trigger_reasons": trigger_reasons,
            }
        # 小幅回落但未达到有效回踩阈值：视为噪音，H1 暂不冻结，继续留在 REPAIRING
        return {
            "new_structural_state": REPAIRING,
            "recovery_high": rh, "pullback_low": None, "pullback_started": False,
            "trigger_reasons": trigger_reasons,
        }

    if state == CONFIRMING:
        if price <= repair_anchor:
            return {
                "new_structural_state": WAIT,
                "recovery_high": None, "pullback_low": None, "pullback_started": False,
                "trigger_reasons": ["跌破修复关键位，回踩确认失败"],
            }
        rh = recovery_high
        new_pullback_low = min(pullback_low, price) if pullback_low is not None else price
        if rh is not None and price > rh:
            trigger_reasons.append(f"现价 {price:.2f} 突破修复高点 {rh:.2f}，回踩结构确认完成")
            return {
                "new_structural_state": BUYABLE,
                "recovery_high": rh, "pullback_low": new_pullback_low, "pullback_started": True,
                "trigger_reasons": trigger_reasons,
            }
        return {
            "new_structural_state": CONFIRMING,
            "recovery_high": rh, "pullback_low": new_pullback_low, "pullback_started": True,
            "trigger_reasons": trigger_reasons,
        }

    if state == BUYABLE:
        if price <= repair_anchor:
            return {
                "new_structural_state": WAIT,
                "recovery_high": None, "pullback_low": None, "pullback_started": False,
                "trigger_reasons": ["跌破关键位，BUYABLE 信号失效"],
            }
        return {
            "new_structural_state": BUYABLE,
            "recovery_high": recovery_high, "pullback_low": pullback_low, "pullback_started": pullback_started,
            "trigger_reasons": trigger_reasons,
        }

    # 理论上不会到达（state 只会是 ALL_STATES 里的一个），兜底原样返回
    return {
        "new_structural_state": state,
        "recovery_high": recovery_high, "pullback_low": pullback_low, "pullback_started": pullback_started,
        "trigger_reasons": trigger_reasons,
    }


def compute_gate_blocks(
    *,
    signal_enabled: bool,
    market_state: str,
    market_gate_blocked: set[str],
    sector_category: str,
    sector_gate_allowed: set[str],
    leader_type: str,
    regulatory_risk: str,
    regulatory_risk_cap: set[str],
    is_observation_expired: bool,
) -> list[str]:
    """
    纯函数：硬性拦截原因列表，为空则不拦截。每次都重新算，从不因为"之前是不是
    BLOCK"而跳过判断——这是修复 BLOCK 死态 bug 的关键：状态应该是环境的输出，
    不应该反过来绑架后续计算。leader_type=="undetermined"（龙头未决）不在这里
    硬拦截，走 apply_leader_undetermined_cap 的软上限。
    """
    reasons: list[str] = []
    if not signal_enabled:
        reasons.append("数据过期，信号已禁用")
    if market_state in market_gate_blocked:
        reasons.append(f"大盘闸门状态 {market_state}，暂停新增买入类信号")
    if sector_category not in sector_gate_allowed:
        reasons.append(f"板块分类 {sector_category} 不在允许列表")
    if leader_type == "non_leader":
        reasons.append("非板块核心龙头")
    if regulatory_risk in regulatory_risk_cap:
        reasons.append(f"监管风险等级 {regulatory_risk}")
    if is_observation_expired:
        reasons.append("候选观察期已过，连续未上榜超过窗口")
    return reasons


def apply_new_start_cap(structural_state: str, sector_category: str) -> tuple[str, Optional[str]]:
    """
    纯函数：板块刚起步（NEW_START）时展示态最高只到 READY——新题材第一天爆发
    还没证明持续性/主线资金共识，不该跟"已经证明过自己强、现在出现分歧"的
    核心弱转强模式享受同等的 REPAIRING/CONFIRMING/BUYABLE 待遇。不是硬拦截
    （不放进 sector_gate_allowed 的排除列表），因为候选本身值得继续观察，
    只是还没到能给结构性信号的阶段。
    """
    if sector_category == "NEW_START" and structural_state in (REPAIRING, CONFIRMING, BUYABLE):
        return READY, "板块仍处早期(NEW_START)，暂不放行到修复/确认阶段"
    return structural_state, None


def apply_leader_undetermined_cap(structural_state: str, leader_type: str) -> tuple[str, Optional[str]]:
    """
    纯函数：龙头未决时展示态最高只到 CONFIRMING——早期龙头竞争阶段如果直接
    BLOCK 会错过"谁才是真龙头"这个市场自己筛选的过程；但没确立龙头之前也不能
    显示 BUYABLE。返回 (展示态, 附加说明或 None)。
    """
    if leader_type == "undetermined" and structural_state == BUYABLE:
        return CONFIRMING, "龙头未决，二次突破已出现但暂不升级为 BUYABLE"
    return structural_state, None


def apply_space_gate_cap(structural_state: str, limit_room: Optional[float], space_min_room_pct: float) -> tuple[str, Optional[str]]:
    """
    纯函数：涨停空间不足时展示态从 BUYABLE 降级为 WAIT，但只覆盖展示，不清空
    底层 structural_state——空间会随价格逐分钟变化，候选本身没有失效，空间一旦
    重新充足应该立刻恢复展示，不需要重新走一遍回踩结构。
    """
    if structural_state == BUYABLE:
        ok, reason = evaluate_space_gate(limit_room, space_min_room_pct)
        if not ok:
            return WAIT, f"结构已确认但{reason}，暂缓至 WAIT"
    return structural_state, None


def compute_next_state(
    *,
    structural_state: str,
    signal_enabled: bool,
    market_state: str,
    market_gate_blocked: set[str],
    sector_category: str,
    sector_gate_allowed: set[str],
    leader_type: str,
    regulatory_risk: str,
    regulatory_risk_cap: set[str],
    is_observation_expired: bool,
    price: Optional[float],
    prev_close: Optional[float],
    vwap: Optional[float],
    ma5: Optional[float],
    recovery_high: Optional[float],
    pullback_low: Optional[float],
    pullback_started: bool,
    pullback_min_pct: float,
    auction_gap: Optional[float],
    auction_gap_min: float,
    is_after_auction: bool,
    limit_room: Optional[float],
    space_min_room_pct: float,
) -> dict:
    """
    纯函数编排：结构判断（不受闸门影响，持续推进）→ 闸门判断（每次重新算，
    硬性不通过则展示 BLOCK）→ 龙头未决软上限 → 空间不足软上限。返回：
    {display_state, structural_state, recovery_high, pullback_low, pullback_started,
     trigger_reasons, block_reasons}
    """
    structural = compute_structural_transition(
        structural_state=structural_state, price=price, prev_close=prev_close,
        vwap=vwap, ma5=ma5, recovery_high=recovery_high, pullback_low=pullback_low,
        pullback_started=pullback_started, pullback_min_pct=pullback_min_pct,
        auction_gap=auction_gap, auction_gap_min=auction_gap_min, is_after_auction=is_after_auction,
    )
    block_reasons = compute_gate_blocks(
        signal_enabled=signal_enabled, market_state=market_state, market_gate_blocked=market_gate_blocked,
        sector_category=sector_category, sector_gate_allowed=sector_gate_allowed,
        leader_type=leader_type, regulatory_risk=regulatory_risk, regulatory_risk_cap=regulatory_risk_cap,
        is_observation_expired=is_observation_expired,
    )

    if block_reasons:
        display_state = BLOCK
        trigger_reasons: list[str] = []
    else:
        capped, new_start_note = apply_new_start_cap(structural["new_structural_state"], sector_category)
        capped, leader_note = apply_leader_undetermined_cap(capped, leader_type)
        display_state, space_note = apply_space_gate_cap(capped, limit_room, space_min_room_pct)
        trigger_reasons = list(structural["trigger_reasons"])
        for note in (new_start_note, leader_note, space_note):
            if note:
                trigger_reasons.append(note)

    return {
        "display_state": display_state,
        "structural_state": structural["new_structural_state"],
        "recovery_high": structural["recovery_high"],
        "pullback_low": structural["pullback_low"],
        "pullback_started": structural["pullback_started"],
        "trigger_reasons": trigger_reasons,
        "block_reasons": block_reasons,
    }
