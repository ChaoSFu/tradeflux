"""
状态机纯函数单测（2026-08-22 二次重构：结构事实层/交易决策层彻底分离）。

覆盖：
  - compute_structural_transition：结构事实层，H1(recovery_high)/L1(pullback_low)
    两段式回踩确认，只产出 STRUCTURE_STATES 里的值（不会是 BUYABLE/WAIT/BLOCK）。
  - compute_gate_blocks：Hard Blocker 层，每次独立重算。
  - derive_display_state：Soft Cap 层 + 结构事实→交易决策映射，唯一允许产出
    BUYABLE 的地方。
  - compute_next_state：编排整体行为，包括 BLOCK 不再是死态、软上限不清空底层
    结构事实。
"""
from datetime import datetime, timedelta

from app.services.w2s_state_machine import (
    compute_structural_transition, compute_gate_blocks, compute_next_state,
    derive_display_state,
    check_data_freshness, classify_regulatory_risk,
    STRUCT_WATCH, STRUCT_READY, STRUCT_REPAIRING, STRUCT_PULLBACK, STRUCT_CONFIRMED, STRUCT_FAILED,
    WATCH, READY, REPAIRING, CONFIRMING, BUYABLE, WAIT, BLOCK,
    LOW, MEDIUM, HIGH, EXTREME,
)

ALLOWED_SECTORS = {"NEW_START", "EXPANDING", "MAIN_UPTREND", "HEALTHY_DIVERGENCE"}
RISK_CAP = {"HIGH", "EXTREME"}
MARKET_GATE_BLOCKED = {"RED"}

STRUCT_BASE = dict(
    prev_close=9.8, vwap=None, ma5=9.5,
    pullback_min_pct=1.5,
    auction_gap=None, auction_gap_min=3.0, is_after_auction=False,
)


def _struct(**overrides):
    kwargs = dict(STRUCT_BASE)
    kwargs.update(overrides)
    return compute_structural_transition(**kwargs)


GATE_BASE = dict(
    signal_enabled=True, market_state="GREEN", market_gate_blocked=MARKET_GATE_BLOCKED,
    sector_category="MAIN_UPTREND", sector_gate_allowed=ALLOWED_SECTORS,
    leader_type="core", regulatory_risk=LOW, regulatory_risk_cap=RISK_CAP,
    is_observation_expired=False,
)


def _gates(**overrides):
    kwargs = dict(GATE_BASE)
    kwargs.update(overrides)
    return compute_gate_blocks(**kwargs)


NEXT_BASE = dict(
    **GATE_BASE,
    prev_close=9.8, vwap=None, ma5=9.5,
    pullback_min_pct=1.5, auction_gap=None, auction_gap_min=3.0, is_after_auction=False,
    limit_room=10.0, space_min_room_pct=2.0,
)


def _next(**overrides):
    kwargs = dict(NEXT_BASE)
    kwargs.update(overrides)
    return compute_next_state(**kwargs)


# ── compute_structural_transition：只产出结构事实，不产出决策值 ─────────────

def test_watch_enters_repairing_on_recovering_above_anchor():
    r = _struct(structural_state=STRUCT_WATCH, price=10.5, recovery_high=None, pullback_low=None, pullback_started=False)
    assert r["new_structural_state"] == STRUCT_REPAIRING
    assert r["recovery_high"] == 10.5
    assert r["pullback_started"] is False


def test_failed_can_reenter_repairing_like_watch():
    r = _struct(structural_state=STRUCT_FAILED, price=10.5, recovery_high=None, pullback_low=None, pullback_started=False)
    assert r["new_structural_state"] == STRUCT_REPAIRING


def test_watch_stays_watch_below_anchor():
    r = _struct(structural_state=STRUCT_WATCH, price=9.0, recovery_high=None, pullback_low=None, pullback_started=False)
    assert r["new_structural_state"] == STRUCT_WATCH


def test_repairing_extends_recovery_high_on_new_high_no_pullback_yet():
    r = _struct(structural_state=STRUCT_REPAIRING, price=11.0, recovery_high=10.5, pullback_low=None, pullback_started=False)
    assert r["new_structural_state"] == STRUCT_REPAIRING
    assert r["recovery_high"] == 11.0
    assert r["pullback_started"] is False


def test_repairing_small_dip_under_threshold_is_not_a_real_pullback():
    # H1=10.5，回落到10.45只有约0.48%，低于1.5%噪音阈值——不冻结H1，留在REPAIRING
    r = _struct(structural_state=STRUCT_REPAIRING, price=10.45, recovery_high=10.5, pullback_low=None, pullback_started=False)
    assert r["new_structural_state"] == STRUCT_REPAIRING
    assert r["recovery_high"] == 10.5
    assert r["pullback_started"] is False


def test_repairing_meaningful_pullback_freezes_h1_and_enters_pullback():
    # H1=10.5，回落到10.3，跌幅约1.9%，超过1.5%阈值——冻结H1，进入PULLBACK记L1
    r = _struct(structural_state=STRUCT_REPAIRING, price=10.3, recovery_high=10.5, pullback_low=None, pullback_started=False)
    assert r["new_structural_state"] == STRUCT_PULLBACK
    assert r["recovery_high"] == 10.5
    assert r["pullback_low"] == 10.3
    assert r["pullback_started"] is True


def test_repairing_breaks_anchor_becomes_failed_and_resets_tracking():
    r = _struct(structural_state=STRUCT_REPAIRING, price=9.0, recovery_high=10.5, pullback_low=None, pullback_started=False)
    assert r["new_structural_state"] == STRUCT_FAILED
    assert r["recovery_high"] is None
    assert r["pullback_started"] is False


def test_pullback_tracks_lower_low_while_below_frozen_high():
    r = _struct(structural_state=STRUCT_PULLBACK, price=10.1, recovery_high=10.5, pullback_low=10.3, pullback_started=True)
    assert r["new_structural_state"] == STRUCT_PULLBACK
    assert r["pullback_low"] == 10.1  # 继续创新低
    assert r["recovery_high"] == 10.5  # H1 保持冻结不变


def test_pullback_breaks_frozen_high_becomes_confirmed():
    r = _struct(structural_state=STRUCT_PULLBACK, price=10.6, recovery_high=10.5, pullback_low=10.3, pullback_started=True)
    assert r["new_structural_state"] == STRUCT_CONFIRMED
    assert any("突破修复高点" in t for t in r["trigger_reasons"])


def test_pullback_breaks_anchor_becomes_failed():
    r = _struct(structural_state=STRUCT_PULLBACK, price=9.0, recovery_high=10.5, pullback_low=10.3, pullback_started=True)
    assert r["new_structural_state"] == STRUCT_FAILED
    assert r["pullback_started"] is False


def test_confirmed_holds_above_anchor():
    r = _struct(structural_state=STRUCT_CONFIRMED, price=11.0, recovery_high=10.5, pullback_low=10.3, pullback_started=True)
    assert r["new_structural_state"] == STRUCT_CONFIRMED


def test_confirmed_breaks_anchor_becomes_failed():
    r = _struct(structural_state=STRUCT_CONFIRMED, price=9.0, recovery_high=10.5, pullback_low=10.3, pullback_started=True)
    assert r["new_structural_state"] == STRUCT_FAILED
    assert r["recovery_high"] is None


def test_missing_price_data_holds_state_with_reason():
    r = _struct(structural_state=STRUCT_REPAIRING, price=None, prev_close=None, recovery_high=10.5, pullback_low=None, pullback_started=False)
    assert r["new_structural_state"] == STRUCT_REPAIRING
    assert r["trigger_reasons"] == ["缺少现价数据，暂无法判断结构"]


def test_vwap_used_as_anchor_reference_over_ma5():
    # prev_close=9.8, ma5=9.5, vwap=10.0 → repair_anchor应取max(9.8, vwap=10.0)=10.0，不是9.8
    r = _struct(structural_state=STRUCT_WATCH, price=10.05, vwap=10.0, ma5=9.5, recovery_high=None, pullback_low=None, pullback_started=False)
    assert r["new_structural_state"] == STRUCT_REPAIRING  # 10.05 > 10.0
    r2 = _struct(structural_state=STRUCT_WATCH, price=9.9, vwap=10.0, ma5=9.5, recovery_high=None, pullback_low=None, pullback_started=False)
    assert r2["new_structural_state"] == STRUCT_WATCH  # 9.9 < anchor(10.0)，若用MA5(9.5)则会误判进入REPAIRING


def test_auction_gap_moves_watch_to_ready_before_repairing_check():
    r = _struct(
        structural_state=STRUCT_WATCH, price=10.5, recovery_high=None, pullback_low=None, pullback_started=False,
        auction_gap=5.0, is_after_auction=True,
    )
    assert r["new_structural_state"] == STRUCT_REPAIRING
    assert any("竞价Gap" in t for t in r["trigger_reasons"])


# ── compute_gate_blocks：每次独立重算，undetermined 不是硬拦截 ──────────────

def test_gate_blocks_empty_when_all_clear():
    assert _gates() == []


def test_gate_blocks_stale_data():
    assert any("过期" in b for b in _gates(signal_enabled=False))


def test_gate_blocks_market_red():
    assert any("大盘闸门" in b for b in _gates(market_state="RED"))


def test_gate_blocks_sector_disallowed():
    assert any("板块分类" in b for b in _gates(sector_category="DECLINING"))


def test_gate_blocks_non_leader():
    assert any("非板块核心龙头" in b for b in _gates(leader_type="non_leader"))


def test_gate_blocks_undetermined_leader_is_not_a_hard_block():
    assert _gates(leader_type="undetermined") == []


def test_gate_blocks_regulatory_high():
    assert any("监管" in b for b in _gates(regulatory_risk=HIGH))


# ── derive_display_state：唯一能产出 BUYABLE 的地方 ─────────────────────────

def test_derive_display_watch_and_ready_pass_through():
    assert derive_display_state(structural_state=STRUCT_WATCH, sector_category="MAIN_UPTREND", leader_type="core", limit_room=10.0, space_min_room_pct=2.0) == (WATCH, None)
    assert derive_display_state(structural_state=STRUCT_READY, sector_category="MAIN_UPTREND", leader_type="core", limit_room=10.0, space_min_room_pct=2.0) == (READY, None)


def test_derive_display_failed_becomes_wait():
    state, note = derive_display_state(structural_state=STRUCT_FAILED, sector_category="MAIN_UPTREND", leader_type="core", limit_room=10.0, space_min_room_pct=2.0)
    assert state == WAIT


def test_derive_display_repairing_and_pullback_map_directly():
    assert derive_display_state(structural_state=STRUCT_REPAIRING, sector_category="MAIN_UPTREND", leader_type="core", limit_room=10.0, space_min_room_pct=2.0)[0] == REPAIRING
    assert derive_display_state(structural_state=STRUCT_PULLBACK, sector_category="MAIN_UPTREND", leader_type="core", limit_room=10.0, space_min_room_pct=2.0)[0] == CONFIRMING


def test_derive_display_confirmed_with_no_caps_is_buyable():
    state, note = derive_display_state(structural_state=STRUCT_CONFIRMED, sector_category="MAIN_UPTREND", leader_type="core", limit_room=10.0, space_min_room_pct=2.0)
    assert state == BUYABLE
    assert note is None


def test_derive_display_new_start_caps_any_progress_to_ready():
    for struct in (STRUCT_REPAIRING, STRUCT_PULLBACK, STRUCT_CONFIRMED):
        state, note = derive_display_state(structural_state=struct, sector_category="NEW_START", leader_type="core", limit_room=10.0, space_min_room_pct=2.0)
        assert state == READY
        assert note is not None


def test_derive_display_leader_undetermined_caps_confirmed_to_confirming():
    state, note = derive_display_state(structural_state=STRUCT_CONFIRMED, sector_category="MAIN_UPTREND", leader_type="undetermined", limit_room=10.0, space_min_room_pct=2.0)
    assert state == CONFIRMING
    assert note is not None


def test_derive_display_insufficient_space_downgrades_confirmed_to_wait():
    state, note = derive_display_state(structural_state=STRUCT_CONFIRMED, sector_category="MAIN_UPTREND", leader_type="core", limit_room=1.0, space_min_room_pct=2.0)
    assert state == WAIT
    assert note is not None


def test_derive_display_non_mainline_sector_caps_confirmed_to_confirming():
    # 板块分类是MAIN_UPTREND、龙头明确、空间充足，但不在当前强度前N名——结构已确认仍不给BUYABLE
    state, note = derive_display_state(
        structural_state=STRUCT_CONFIRMED, sector_category="MAIN_UPTREND", leader_type="core",
        limit_room=10.0, space_min_room_pct=2.0, is_mainline_sector=False,
    )
    assert state == CONFIRMING
    assert note is not None


def test_derive_display_mainline_sector_true_allows_buyable():
    state, note = derive_display_state(
        structural_state=STRUCT_CONFIRMED, sector_category="MAIN_UPTREND", leader_type="core",
        limit_room=10.0, space_min_room_pct=2.0, is_mainline_sector=True,
    )
    assert state == BUYABLE
    assert note is None


# ── compute_next_state：编排行为，BLOCK 不再是死态 ──────────────────────────

def test_next_state_market_red_displays_block_but_keeps_structural_progress():
    result = _next(
        structural_state=STRUCT_PULLBACK, market_state="RED",
        price=10.6, recovery_high=10.5, pullback_low=10.3, pullback_started=True,
    )
    assert result["display_state"] == BLOCK
    # 关键：底层结构事实照常基于价格推进到 CONFIRMED，只是展示被闸门覆盖
    assert result["structural_state"] == STRUCT_CONFIRMED
    assert result["recovery_high"] == 10.5


def test_next_state_recovers_once_gate_clears_without_losing_progress():
    # 上一轮 market=RED，展示BLOCK但结构事实已经到CONFIRMED；这一轮market转好
    blocked = _next(
        structural_state=STRUCT_PULLBACK, market_state="RED",
        price=10.6, recovery_high=10.5, pullback_low=10.3, pullback_started=True,
    )
    recovered = _next(
        structural_state=blocked["structural_state"], market_state="GREEN",
        price=10.7, recovery_high=blocked["recovery_high"], pullback_low=blocked["pullback_low"],
        pullback_started=blocked["pullback_started"],
    )
    assert recovered["display_state"] == BUYABLE


def test_next_state_leader_undetermined_shows_confirming_not_buyable():
    result = _next(
        structural_state=STRUCT_PULLBACK, leader_type="undetermined",
        price=10.6, recovery_high=10.5, pullback_low=10.3, pullback_started=True,
    )
    assert result["display_state"] == CONFIRMING
    assert result["structural_state"] == STRUCT_CONFIRMED


def test_next_state_non_mainline_sector_caps_at_confirming_but_keeps_structural_confirmed():
    result = _next(
        structural_state=STRUCT_PULLBACK, is_mainline_sector=False,
        price=10.6, recovery_high=10.5, pullback_low=10.3, pullback_started=True,
    )
    assert result["display_state"] == CONFIRMING
    assert result["structural_state"] == STRUCT_CONFIRMED


def test_next_state_mainline_sector_promotion_immediately_unlocks_buyable():
    # 场景：结构已确认但当时板块排不进前3，只显示CONFIRMING；后续该板块强度上升
    # 挤进前3后（不需要重新走一遍回踩），同一结构立刻放行到BUYABLE
    capped = _next(
        structural_state=STRUCT_PULLBACK, is_mainline_sector=False,
        price=10.6, recovery_high=10.5, pullback_low=10.3, pullback_started=True,
    )
    assert capped["display_state"] == CONFIRMING
    promoted = _next(
        structural_state=capped["structural_state"], is_mainline_sector=True,
        price=10.65, recovery_high=capped["recovery_high"], pullback_low=capped["pullback_low"],
        pullback_started=capped["pullback_started"],
    )
    assert promoted["display_state"] == BUYABLE
    assert promoted["structural_state"] == STRUCT_CONFIRMED


def test_next_state_new_start_sector_caps_at_ready():
    result = _next(
        structural_state=STRUCT_WATCH, sector_category="NEW_START",
        price=10.5, recovery_high=None, pullback_low=None, pullback_started=False,
    )
    assert result["display_state"] == READY
    assert result["structural_state"] == STRUCT_REPAIRING


def test_next_state_insufficient_space_downgrades_to_wait_but_keeps_structural_confirmed():
    result = _next(
        structural_state=STRUCT_PULLBACK, limit_room=1.0, space_min_room_pct=2.0,
        price=10.6, recovery_high=10.5, pullback_low=10.3, pullback_started=True,
    )
    assert result["display_state"] == WAIT
    assert result["structural_state"] == STRUCT_CONFIRMED


def test_next_state_full_cycle_to_buyable():
    r1 = _next(structural_state=STRUCT_WATCH, price=10.5, recovery_high=None, pullback_low=None, pullback_started=False)
    assert r1["display_state"] == REPAIRING
    r2 = _next(structural_state=r1["structural_state"], price=10.3, recovery_high=r1["recovery_high"], pullback_low=None, pullback_started=False)
    assert r2["display_state"] == CONFIRMING
    r3 = _next(structural_state=r2["structural_state"], price=10.6, recovery_high=r2["recovery_high"], pullback_low=r2["pullback_low"], pullback_started=True)
    assert r3["display_state"] == BUYABLE
    assert r3["structural_state"] == STRUCT_CONFIRMED


# ── 数据新鲜度 / 监管风险分级（未改动，保留原有覆盖）───────────────────────

def test_check_data_freshness():
    now = datetime(2026, 8, 21, 9, 30)
    assert check_data_freshness(now - timedelta(seconds=100), now, max_staleness_seconds=600) is True
    assert check_data_freshness(now - timedelta(seconds=900), now, max_staleness_seconds=600) is False
    assert check_data_freshness(None, now) is False


def test_classify_regulatory_risk():
    assert classify_regulatory_risk(True, None) == EXTREME
    assert classify_regulatory_risk(False, 1) == HIGH
    assert classify_regulatory_risk(False, 8) == MEDIUM
    assert classify_regulatory_risk(False, 30) == LOW
    assert classify_regulatory_risk(False, None) == LOW
