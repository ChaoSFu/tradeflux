"""
Golden Case：语义级测试，独立于下面的历史回测框架（第三轮 ChatGPT 审阅第17点：
不需要等完整回测框架落地才能开始——先用合成的多步价格序列覆盖真实交易日里
会出现的典型形态，直接调 compute_next_state（结构事实层+交易决策层完整编排），
按"一个交易日内连续多次 /refresh"的方式逐帧断言，比 test_w2s_state_machine.py
里的单分支单测更接近"这套系统在真实盘中会不会给出正确信号"这个问题。

**关于"神奇制药"案例**：多轮外部评审都提到过这个真实案例作为 Market/Sector
负反馈信号被淹没的例证，但本次会话里没有拿到该案例的具体历史价格/时间序列
数据，为避免编造假数据冒充真实回放，这里只还原评审描述的**形态特征**（板块/
大盘负反馈明显但结构层表面看起来仍在推进）作为合成场景，不是对该真实案例的
精确复现。等真正拿到那组历史数据后应该替换成 test_golden_case_market_negative_feedback_real_magic_pharma
这样命名的独立用例。

每个 Golden Case 是一个具名场景 + 一串 tick（每个 tick = 一次 /refresh 的
输入快照），断言每一步的 display_state/structural_state 都符合场景描述的
叙事，而不是只断言最终结果——中间步骤错了同样算这个场景没通过。
"""
from app.services.w2s_state_machine import (
    compute_next_state,
    WATCH, READY, REPAIRING, CONFIRMING, BUYABLE, WAIT, BLOCK,
    STRUCT_WATCH, STRUCT_REPAIRING, STRUCT_PULLBACK, STRUCT_CONFIRMED, STRUCT_FAILED,
    LOW, HIGH,
)

ALLOWED_SECTORS = {"NEW_START", "EXPANDING", "MAIN_UPTREND", "HEALTHY_DIVERGENCE"}
RISK_CAP = {"HIGH", "EXTREME"}
MARKET_GATE_BLOCKED = {"RED"}

BASE = dict(
    signal_enabled=True,
    market_gate_blocked=MARKET_GATE_BLOCKED,
    sector_gate_allowed=ALLOWED_SECTORS,
    regulatory_risk_cap=RISK_CAP,
    is_observation_expired=False,
    prev_close=9.8, vwap=None, ma5=9.5,
    pullback_min_pct=1.5, auction_gap=None, auction_gap_min=3.0, is_after_auction=False,
    limit_room=10.0, space_min_room_pct=2.0,
    market_state="GREEN", sector_category="MAIN_UPTREND", leader_type="core", regulatory_risk=LOW,
)


def _tick(prev_result, **overrides):
    """跑一次 tick：用上一轮的 structural_state/H1/L1 接力，其余字段用 BASE 默认值 + overrides 覆盖。"""
    kwargs = dict(BASE)
    if prev_result is None:
        kwargs.update(
            structural_state=STRUCT_WATCH, price=None, recovery_high=None,
            pullback_low=None, pullback_started=False,
        )
    else:
        kwargs.update(
            structural_state=prev_result["structural_state"],
            recovery_high=prev_result["recovery_high"],
            pullback_low=prev_result["pullback_low"],
            pullback_started=prev_result["pullback_started"],
        )
    kwargs.update(overrides)
    return compute_next_state(**kwargs)


def test_golden_case_standard_weak_to_strong_full_cycle():
    """标准弱转强模板：开盘收复关键位→建H1→有效回踩→二次突破→一路展示到BUYABLE，全程无闸门/软上限介入。"""
    r1 = _tick(None, price=10.5)
    assert (r1["display_state"], r1["structural_state"]) == (REPAIRING, STRUCT_REPAIRING)

    r2 = _tick(r1, price=10.3)  # 回踩超过1.5%噪音阈值，冻结H1=10.5，记L1
    assert (r2["display_state"], r2["structural_state"]) == (CONFIRMING, STRUCT_PULLBACK)
    assert r2["recovery_high"] == 10.5 and r2["pullback_low"] == 10.3

    r3 = _tick(r2, price=10.6)  # 突破冻结的H1
    assert (r3["display_state"], r3["structural_state"]) == (BUYABLE, STRUCT_CONFIRMED)

    r4 = _tick(r3, price=10.8)  # 继续持有在结构确认位以上，维持BUYABLE
    assert (r4["display_state"], r4["structural_state"]) == (BUYABLE, STRUCT_CONFIRMED)


def test_golden_case_noise_pullback_does_not_reset_progress():
    """噪音级回踩不该被误判为有效回踩：H1建立后小幅震荡（<1.5%）应该继续REPAIRING累积H1，不提前冻结。"""
    r1 = _tick(None, price=10.5)
    assert r1["structural_state"] == STRUCT_REPAIRING and r1["recovery_high"] == 10.5

    r2 = _tick(r1, price=10.45)  # 回落约0.48%，噪音级，不冻结H1
    assert r2["structural_state"] == STRUCT_REPAIRING
    assert r2["recovery_high"] == 10.5 and r2["pullback_low"] is None

    r3 = _tick(r2, price=10.7)  # 继续新高，H1上移，全程没有误判成"已回踩"
    assert r3["structural_state"] == STRUCT_REPAIRING and r3["recovery_high"] == 10.7

    r4 = _tick(r3, price=10.5)  # 现在才是真正超过噪音阈值的回踩（相对10.7跌约1.87%）
    assert r4["structural_state"] == STRUCT_PULLBACK and r4["recovery_high"] == 10.7


def test_golden_case_false_breakout_then_genuine_retry():
    """假突破失败后允许重新挑战：第一次冲高后直接跌破关键位（FAILED），后续价格重新收复后应视为全新的合法结构，不是死态。"""
    r1 = _tick(None, price=10.5)
    assert r1["structural_state"] == STRUCT_REPAIRING

    r2 = _tick(r1, price=9.5)  # 直接跌破 repair_anchor(9.8)，结构失效
    assert r2["structural_state"] == STRUCT_FAILED
    assert r2["recovery_high"] is None and r2["display_state"] == WAIT

    r3 = _tick(r2, price=10.2)  # 重新收复关键位，FAILED 是合法重新进入点，不是死态
    assert r3["structural_state"] == STRUCT_REPAIRING and r3["recovery_high"] == 10.2

    r4 = _tick(r3, price=10.0)
    r5 = _tick(r4, price=10.4)  # 完整走完第二次尝试的确认
    assert r5["structural_state"] == STRUCT_CONFIRMED and r5["display_state"] == BUYABLE


def test_golden_case_leader_undetermined_then_resolves():
    """龙头未决期间结构照常推进但封顶CONFIRMING，龙头分出胜负后同一结构立刻放行到BUYABLE，不需要重新走一遍回踩。"""
    r1 = _tick(None, price=10.5, leader_type="undetermined")
    r2 = _tick(r1, price=10.3, leader_type="undetermined")
    r3 = _tick(r2, price=10.6, leader_type="undetermined")
    assert r3["structural_state"] == STRUCT_CONFIRMED
    assert r3["display_state"] == CONFIRMING  # 结构已确认，但龙头未决压低展示

    r4 = _tick(r3, price=10.65, leader_type="core")  # 分出胜负，同一结构立刻放行
    assert r4["structural_state"] == STRUCT_CONFIRMED
    assert r4["display_state"] == BUYABLE


def test_golden_case_space_insufficient_then_frees_up():
    """结构确认但涨停空间不足时降级WAIT（不是编造一个"没到位"的假结构），空间随价格回落重新充足后立刻恢复BUYABLE，不清空结构进度。"""
    r1 = _tick(None, price=10.5, limit_room=1.0)
    r2 = _tick(r1, price=10.3, limit_room=1.0)
    r3 = _tick(r2, price=10.6, limit_room=1.0)  # 结构已确认，但空间不足2%阈值
    assert r3["structural_state"] == STRUCT_CONFIRMED
    assert r3["display_state"] == WAIT

    r4 = _tick(r3, price=10.55, limit_room=2.5)  # 空间重新充足（比如涨停价随其它因素上修），结构不变仍是CONFIRMED
    assert r4["structural_state"] == STRUCT_CONFIRMED
    assert r4["display_state"] == BUYABLE


def test_golden_case_market_negative_feedback_pattern_synthetic():
    """
    合成场景（非真实案例回放，见文件头说明）：大盘/板块负反馈明显（这里用
    market_state=RED 模拟"风险偏好分因T-1冻结群体次日大面而骤降"这类信号）
    时，即便某只候选自身的价格结构表面看起来还在推进，展示也必须是BLOCK，
    不能因为个股结构好看就放行——这正是"弱转强成立≠值得买入"要拦住的场景。
    大盘负反馈解除后，若结构在此期间已经走到确认，应立刻反映，不需要重新
    走一遍回踩（跟"BLOCK不是死态"是同一条不变式，这里从"市场负反馈"这个
    具体业务场景角度再验证一遍）。
    """
    r1 = _tick(None, price=10.5, market_state="RED")
    assert r1["structural_state"] == STRUCT_REPAIRING
    assert r1["display_state"] == BLOCK  # 结构表面在推进，但市场负反馈期间坚决不放行

    r2 = _tick(r1, price=10.3, market_state="RED")
    assert r2["structural_state"] == STRUCT_PULLBACK
    assert r2["display_state"] == BLOCK

    r3 = _tick(r2, price=10.6, market_state="RED")
    assert r3["structural_state"] == STRUCT_CONFIRMED
    assert r3["display_state"] == BLOCK  # 结构已经完整确认，但市场负反馈仍在，展示依然是BLOCK

    r4 = _tick(r3, price=10.65, market_state="GREEN")  # 大盘负反馈解除
    assert r4["structural_state"] == STRUCT_CONFIRMED
    assert r4["display_state"] == BUYABLE  # 立刻反映真实进度，不需要重新走H1/L1
