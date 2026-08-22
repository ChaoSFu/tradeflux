"""
7态状态机纯函数单测：BLOCK 优先级、WATCH→READY→REPAIRING→CONFIRMING→BUYABLE 逐级推进、
数据新鲜度与监管风险分级。
"""
from datetime import datetime, timedelta

from app.services.w2s_state_machine import (
    compute_next_state, check_data_freshness, classify_regulatory_risk,
    WATCH, READY, REPAIRING, CONFIRMING, BUYABLE, WAIT, BLOCK,
    LOW, MEDIUM, HIGH, EXTREME,
)

ALLOWED_SECTORS = {"NEW_START", "EXPANDING", "MAIN_UPTREND", "HEALTHY_DIVERGENCE"}
RISK_CAP = {"HIGH", "EXTREME"}
MARKET_GATE_BLOCKED = {"RED"}

BASE_KWARGS = dict(
    market_state="GREEN",
    market_gate_blocked=MARKET_GATE_BLOCKED,
    sector_gate_allowed=ALLOWED_SECTORS,
    regulatory_risk_cap=RISK_CAP,
    is_observation_expired=False,
    pullback_low=None,
    auction_gap=None,
    auction_gap_min=3.0,
    is_after_auction=False,
    limit_room=10.0,
    space_min_room_pct=2.0,
)


def _call(**overrides):
    kwargs = dict(BASE_KWARGS)
    kwargs.update(overrides)
    return compute_next_state(**kwargs)


def test_stale_data_blocks_regardless_of_other_inputs():
    state, triggers, blocks = _call(
        current_state=WATCH, signal_enabled=False, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=10, prev_close=9, ma5=9.5,
    )
    assert state == BLOCK
    assert any("过期" in b for b in blocks)


def test_disallowed_sector_blocks():
    state, _, blocks = _call(
        current_state=WATCH, signal_enabled=True, sector_category="DECLINING",
        leader_type="core", regulatory_risk=LOW, price=10, prev_close=9, ma5=9.5,
    )
    assert state == BLOCK
    assert any("板块分类" in b for b in blocks)


def test_non_leader_blocks():
    state, _, blocks = _call(
        current_state=WATCH, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="non_leader", regulatory_risk=LOW, price=10, prev_close=9, ma5=9.5,
    )
    assert state == BLOCK
    assert any("龙头" in b for b in blocks)


def test_high_regulatory_risk_blocks():
    state, _, blocks = _call(
        current_state=WATCH, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=HIGH, price=10, prev_close=9, ma5=9.5,
    )
    assert state == BLOCK
    assert any("监管" in b for b in blocks)


def test_watch_repairs_when_price_recovers_anchor():
    state, triggers, _ = _call(
        current_state=WATCH, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=10.5, prev_close=9.8, ma5=9.5,
    )
    assert state == REPAIRING
    assert triggers


def test_watch_stays_wait_like_below_anchor():
    state, _, _ = _call(
        current_state=WATCH, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=9.0, prev_close=9.8, ma5=9.5,
    )
    assert state == WATCH


def test_repairing_breaks_pullback_low_to_confirming():
    state, triggers, _ = _call(
        current_state=REPAIRING, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=11.0, prev_close=9.8, ma5=9.5,
        pullback_low=10.5,
    )
    assert state == CONFIRMING
    assert triggers


def test_repairing_falls_back_to_wait_when_losing_anchor():
    state, _, _ = _call(
        current_state=REPAIRING, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=9.0, prev_close=9.8, ma5=9.5,
    )
    assert state == WAIT


def test_confirming_promotes_to_buyable():
    state, triggers, _ = _call(
        current_state=CONFIRMING, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=11.5, prev_close=9.8, ma5=9.5,
        pullback_low=10.5,
    )
    assert state == BUYABLE
    assert triggers


def test_confirming_fails_back_to_wait_below_pullback_low():
    state, _, _ = _call(
        current_state=CONFIRMING, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=10.0, prev_close=9.8, ma5=9.5,
        pullback_low=10.5,
    )
    assert state == WAIT


def test_buyable_holds_while_above_anchor():
    state, _, _ = _call(
        current_state=BUYABLE, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=11.0, prev_close=9.8, ma5=9.5,
    )
    assert state == BUYABLE


def test_missing_price_data_waits_not_blocks():
    state, _, blocks = _call(
        current_state=WATCH, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=None, prev_close=None, ma5=None,
    )
    assert state == WAIT
    assert blocks == ["缺少现价数据，暂无法判断结构"]


def test_market_gate_red_blocks_regardless_of_other_inputs():
    state, _, blocks = _call(
        current_state=WATCH, signal_enabled=True, market_state="RED",
        sector_category="MAIN_UPTREND", leader_type="core", regulatory_risk=LOW,
        price=10.5, prev_close=9.8, ma5=9.5,
    )
    assert state == BLOCK
    assert any("大盘闸门" in b for b in blocks)


def test_market_gate_yellow_does_not_block():
    state, _, _ = _call(
        current_state=WATCH, signal_enabled=True, market_state="YELLOW",
        sector_category="MAIN_UPTREND", leader_type="core", regulatory_risk=LOW,
        price=10.5, prev_close=9.8, ma5=9.5,
    )
    assert state == REPAIRING


def test_auction_gap_exceeding_threshold_moves_watch_to_ready_then_repairing():
    # Gap 达标先跳 READY，同一次调用里价格若已收复关键位会继续推进到 REPAIRING
    # （READY 和 WATCH 共享同一条"收复 max(昨收,MA5)"判断，属预期行为，不是跳级）。
    state, triggers, _ = _call(
        current_state=WATCH, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=10.5, prev_close=9.8, ma5=9.5,
        auction_gap=5.0, is_after_auction=True,
    )
    assert state == REPAIRING
    assert any("竞价Gap" in t for t in triggers)


def test_auction_gap_below_threshold_stays_watch():
    state, _, _ = _call(
        current_state=WATCH, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=9.0, prev_close=9.8, ma5=9.5,
        auction_gap=1.0, is_after_auction=True,
    )
    assert state == WATCH


def test_confirming_downgrades_to_wait_when_space_insufficient():
    state, triggers, _ = _call(
        current_state=CONFIRMING, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=11.5, prev_close=9.8, ma5=9.5,
        pullback_low=10.5, limit_room=1.0,
    )
    assert state == WAIT
    assert any("涨停空间" in t for t in triggers)


def test_confirming_promotes_to_buyable_when_space_sufficient():
    state, _, _ = _call(
        current_state=CONFIRMING, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=11.5, prev_close=9.8, ma5=9.5,
        pullback_low=10.5, limit_room=5.0,
    )
    assert state == BUYABLE


def test_buyable_downgrades_to_wait_when_space_becomes_insufficient():
    state, triggers, _ = _call(
        current_state=BUYABLE, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=11.0, prev_close=9.8, ma5=9.5,
        limit_room=0.5,
    )
    assert state == WAIT
    assert any("涨停空间" in t for t in triggers)


def test_space_gate_missing_room_data_is_insufficient():
    state, triggers, _ = _call(
        current_state=CONFIRMING, signal_enabled=True, sector_category="MAIN_UPTREND",
        leader_type="core", regulatory_risk=LOW, price=11.5, prev_close=9.8, ma5=9.5,
        pullback_low=10.5, limit_room=None,
    )
    assert state == WAIT
    assert any("空间数据缺失" in t for t in triggers)


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
