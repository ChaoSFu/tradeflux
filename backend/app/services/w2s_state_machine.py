"""
弱转强雷达状态机（2026-08-22 二次重构：结构事实与交易决策彻底分离）。

**为什么再拆一层**：第一次重构（展示态/结构态解耦）修复了 BLOCK 死态，但
`structural_state` 里仍然混了两种不同性质的东西——`REPAIRING`/`CONFIRMING`
描述的是"市场客观发生了什么"，`BUYABLE` 描述的却是"我是否应该交易"，这是
两层完全不同的语义。同理，结构失效时旧版直接把 structural_state 设成 WAIT，
但"跌破关键位"是一个客观事实（这次修复尝试失败了），"WAIT"是一个交易决策
（先别管它），两者也不该用同一个值表示——不然以后区分不了"结构很好只是空间
不够"的WAIT和"结构已经失败"的WAIT，这个区别对未来回测非常重要。

**现在彻底分成两层**：

结构事实层 STRUCTURE（`structural_state` 字段，只看价格，完全不理会任何
闸门，每次刷新持续推进，不受展示态影响）：
  WATCH → READY → REPAIRING → PULLBACK → CONFIRMED
  侧出口：FAILED（随时可能发生，随时可能从 FAILED 重新进入 REPAIRING）
  CONFIRMED 只代表"H1/L1回踩结构已经确认"这一个客观事实，不代表"可以买"。

交易决策层 DECISION（`display_state`/`current_state` 字段，= f(结构事实,
大盘, 板块, 龙头, 空间, 监管, 数据质量)，每次都从结构事实重新推导，不是
存储的独立状态机）：
  WATCH / READY / REPAIRING / CONFIRMING / BUYABLE / WAIT / BLOCK
  这一层的取值特意跟结构层的展示标签保持视觉上的一一对应（用户不需要关心
  内部两层分离这件事），但背后的计算方式完全不同：
    BUYABLE = 结构已 CONFIRMED  且  没有硬性闸门拦截  且  没有软上限拦截
    展示上的 REPAIRING/CONFIRMING 只是结构 REPAIRING/PULLBACK 的直接映射
    展示上的 WAIT 有多种可能的结构原因（结构FAILED / 结构CONFIRMED但空间
    不足 / …），原因文本记在 trigger_reasons 里，不再是同一个空洞的 WAIT。

三层约束分类（替代此前不准确的"五道硬性闸门"表述）：
  Hard Blocker  ：当前绝对不能交易 —— 数据过期、大盘RED、板块明确不允许、
                  龙头non_leader、监管风险过高、候选观察期已过。→ 展示BLOCK。
  Soft Cap      ：可以继续观察，但限制展示能到的最高决策态 —— 板块刚起步
                  (NEW_START)、龙头未决(undetermined)、涨停空间不足。
  Setup Progression：结构事实层本身，见上。

Phase 1/2 明确不做 WARNING/EXIT（持仓监控态，本仓库无持仓/成交跟踪能力，硬做
就是编数据）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .w2s_risk_service import evaluate_space_gate

# ── 结构事实层（客观价格状态，只在这几个值之间流转）─────────────────────────
STRUCT_WATCH = "WATCH"
STRUCT_READY = "READY"
STRUCT_REPAIRING = "REPAIRING"
STRUCT_PULLBACK = "PULLBACK"
STRUCT_CONFIRMED = "CONFIRMED"
STRUCT_FAILED = "FAILED"

STRUCTURE_STATES = (STRUCT_WATCH, STRUCT_READY, STRUCT_REPAIRING, STRUCT_PULLBACK, STRUCT_CONFIRMED, STRUCT_FAILED)

# ── 交易决策层（展示值，兼容此前前端已经在用的 7 个标签，含义见模块说明）───────
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
    纯函数：只看价格结构事实，完全不理会任何闸门、不产生任何交易决策。返回
    {new_structural_state, recovery_high, pullback_low, pullback_started, trigger_reasons}。
    new_structural_state 只会是 STRUCTURE_STATES 里的一个（不会是 BUYABLE/WAIT/BLOCK
    这类决策词）。repair_anchor 优先用真实 VWAP（当日成交额/成交量算出，比5日线更
    能代表"今天这批买盘的平均成本"），VWAP 缺失（比如刚开盘还没有成交）时退回 MA5。
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
    if is_after_auction and auction_gap is not None and auction_gap >= auction_gap_min and state == STRUCT_WATCH:
        trigger_reasons.append(f"竞价Gap {auction_gap:.1f}% 超预期（阈值 {auction_gap_min:.1f}%）")
        state = STRUCT_READY

    # 重新收复关键位：WATCH/READY/FAILED 都可能在价格重新站上 repair_anchor 时进入
    # REPAIRING（FAILED 不是死态，价格随时可能再次收复，跟 WATCH/READY 走同一条
    # 重新进入的路——这是一个客观事实的重新发生，不是"决策"）。
    if state in (STRUCT_WATCH, STRUCT_READY, STRUCT_FAILED) and price > repair_anchor:
        trigger_reasons.append(f"现价 {price:.2f} 收复 max(昨收,VWAP/MA5)={repair_anchor:.2f}")
        return {
            "new_structural_state": STRUCT_REPAIRING,
            "recovery_high": price, "pullback_low": None, "pullback_started": False,
            "trigger_reasons": trigger_reasons,
        }

    if state == STRUCT_REPAIRING:
        if price <= repair_anchor:
            return {
                "new_structural_state": STRUCT_FAILED,
                "recovery_high": None, "pullback_low": None, "pullback_started": False,
                "trigger_reasons": ["跌破修复关键位，结构失效"],
            }
        rh = recovery_high if recovery_high is not None else price
        if price >= rh:
            # H1 还没形成有效回踩前持续抬高，还在建第一段修复高点
            return {
                "new_structural_state": STRUCT_REPAIRING,
                "recovery_high": price, "pullback_low": None, "pullback_started": False,
                "trigger_reasons": trigger_reasons,
            }
        if price < rh * (1 - pullback_min_pct / 100):
            # 回落幅度超过噪音阈值，判定为真正开始回踩：冻结 H1，开始记录 L1
            trigger_reasons.append(f"现价 {price:.2f} 较修复高点 {rh:.2f} 回踩超过 {pullback_min_pct:.1f}%，形成有效回踩")
            return {
                "new_structural_state": STRUCT_PULLBACK,
                "recovery_high": rh, "pullback_low": price, "pullback_started": True,
                "trigger_reasons": trigger_reasons,
            }
        # 小幅回落但未达到有效回踩阈值：视为噪音，H1 暂不冻结，继续留在 REPAIRING
        return {
            "new_structural_state": STRUCT_REPAIRING,
            "recovery_high": rh, "pullback_low": None, "pullback_started": False,
            "trigger_reasons": trigger_reasons,
        }

    if state == STRUCT_PULLBACK:
        if price <= repair_anchor:
            return {
                "new_structural_state": STRUCT_FAILED,
                "recovery_high": None, "pullback_low": None, "pullback_started": False,
                "trigger_reasons": ["跌破修复关键位，回踩确认失败"],
            }
        rh = recovery_high
        new_pullback_low = min(pullback_low, price) if pullback_low is not None else price
        if rh is not None and price > rh:
            trigger_reasons.append(f"现价 {price:.2f} 突破修复高点 {rh:.2f}，回踩结构确认完成")
            return {
                "new_structural_state": STRUCT_CONFIRMED,
                "recovery_high": rh, "pullback_low": new_pullback_low, "pullback_started": True,
                "trigger_reasons": trigger_reasons,
            }
        return {
            "new_structural_state": STRUCT_PULLBACK,
            "recovery_high": rh, "pullback_low": new_pullback_low, "pullback_started": True,
            "trigger_reasons": trigger_reasons,
        }

    if state == STRUCT_CONFIRMED:
        if price <= repair_anchor:
            return {
                "new_structural_state": STRUCT_FAILED,
                "recovery_high": None, "pullback_low": None, "pullback_started": False,
                "trigger_reasons": ["跌破关键位，结构确认失效"],
            }
        return {
            "new_structural_state": STRUCT_CONFIRMED,
            "recovery_high": recovery_high, "pullback_low": pullback_low, "pullback_started": pullback_started,
            "trigger_reasons": trigger_reasons,
        }

    # 理论上不会到达（state 只会是 STRUCTURE_STATES 里的一个），兜底原样返回
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
    纯函数（Hard Blocker 层）：硬性拦截原因列表，为空则不拦截。每次都重新算，
    从不因为"之前是不是BLOCK"而跳过判断——状态应该是环境的输出，不应该反过来
    绑架后续计算。leader_type=="undetermined"（龙头未决）不在这里硬拦截，走
    apply_leader_undetermined_cap 的软上限（Soft Cap 层）。
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


def derive_display_state(
    *,
    structural_state: str,
    sector_category: str,
    leader_type: str,
    limit_room: Optional[float],
    space_min_room_pct: float,
    is_mainline_sector: bool = True,
) -> tuple[str, Optional[str]]:
    """
    纯函数（Soft Cap 层 + 结构事实→交易决策映射）：给定结构事实和四个软上限
    输入，推导出展示态。这是唯一一处允许"结构=CONFIRMED"变成"决策=BUYABLE"
    的地方——BUYABLE 从来不是结构层自己会产生的值。

    `is_mainline_sector`（2026-08-23新增）：候选所属板块是否在当前 MAIN_UPTREND
    分类里按强度分排进前 N 名（`select_mainline_sector_ids`，N 默认3，`w2s_mainline_
    sector_top_n` 可配）。能同时做主升的板块不会很多，弱转强的 Edge 来自资金回流
    到最强的少数主线——不在前N名不是硬性拦截（候选照常追踪、结构照常推进），只是
    结构确认后不放行到 BUYABLE，跟 NEW_START/龙头未决是同一种软上限处理方式。
    默认 True 是为了兼容只想测试其它软上限、不关心这一个维度的调用方（比如老测试）。
    """
    if structural_state == STRUCT_FAILED:
        return WAIT, None
    if structural_state in (STRUCT_WATCH, STRUCT_READY):
        return structural_state, None

    # REPAIRING / PULLBACK / CONFIRMED 三种结构事实，NEW_START 一律封顶到 READY
    if sector_category == "NEW_START":
        return READY, "板块仍处早期(NEW_START)，暂不放行到修复/确认阶段"

    if structural_state == STRUCT_REPAIRING:
        return REPAIRING, None
    if structural_state == STRUCT_PULLBACK:
        return CONFIRMING, None

    # structural_state == STRUCT_CONFIRMED：这是唯一可能产出 BUYABLE 的分支
    if leader_type == "undetermined":
        return CONFIRMING, "龙头未决，二次突破已出现但暂不升级为 BUYABLE"
    if not is_mainline_sector:
        return CONFIRMING, "所属板块不在当前最强的主升前列，结构已确认但暂缓至 CONFIRMING"
    space_ok, space_reason = evaluate_space_gate(limit_room, space_min_room_pct)
    if not space_ok:
        return WAIT, f"结构已确认但{space_reason}，暂缓至 WAIT"
    return BUYABLE, None


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
    is_mainline_sector: bool = True,
) -> dict:
    """
    纯函数编排：结构事实（Setup Progression，不受闸门影响，持续推进）→
    Hard Blocker（每次重新算，硬性不通过则展示 BLOCK）→ Soft Cap + 结构→决策
    映射。返回：{display_state, structural_state, recovery_high, pullback_low,
    pullback_started, trigger_reasons, block_reasons}。
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
        display_state, cap_note = derive_display_state(
            structural_state=structural["new_structural_state"], sector_category=sector_category,
            leader_type=leader_type, limit_room=limit_room, space_min_room_pct=space_min_room_pct,
            is_mainline_sector=is_mainline_sector,
        )
        trigger_reasons = list(structural["trigger_reasons"])
        if cap_note:
            trigger_reasons.append(cap_note)

    return {
        "display_state": display_state,
        "structural_state": structural["new_structural_state"],
        "recovery_high": structural["recovery_high"],
        "pullback_low": structural["pullback_low"],
        "pullback_started": structural["pullback_started"],
        "trigger_reasons": trigger_reasons,
        "block_reasons": block_reasons,
    }
