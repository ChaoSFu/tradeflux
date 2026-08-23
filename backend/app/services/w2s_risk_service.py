"""
弱转强雷达 Space Gate 降级判断 + 三层止损 + 压力情景风险回报比（Phase 2）。

三层止损（由紧到松）：
  技术止损 technical_stop  = 回踩低点(pullback_low)，缺失时退回 MA5——结构失效的判定位。
  标准止损 standard_stop   = 技术止损基础上留 2% 缓冲，避免正常回踩噪音刚好触发就被震出。
  压力止损 stress_stop     = 模拟"买入后次日直接跌停开盘"的极端情形（T+1 不能当日止损，
                              这是真实存在、不能靠盯盘规避的风险）—— price*(1-跌停幅度%)。

压力情景风险回报比 Stress R/R = 今日剩余涨停空间% / 跌停幅度%——分母使用现有的
get_actual_limit_pct(code, is_st) 动态算出的真实涨跌停规则（按证券规则动态算出，
代码里从不是写死的常量），是同板块类型的固定值，不是概率加权预期，只回答
"如果明天真跌停，今天剩的上涨空间值不值得担这个风险"，不是完整的期望收益模型。

核心函数是纯函数，DB 相关的取数（limit_down_pct 需要 is_st 标志算出真实涨跌停
规则，注意不是 get_limit_pct() 那个给K线涨跌停判定用的容差阈值）留给调用方传入。
"""
from __future__ import annotations

from typing import Optional

STANDARD_STOP_BUFFER_PCT = 2.0  # 标准止损相对技术止损的缓冲比例


def evaluate_space_gate(limit_room: Optional[float], min_room_pct: float) -> tuple[bool, Optional[str]]:
    """
    纯函数：涨停空间是否充足，供 BUYABLE 降级判断用。
    limit_room 缺失时保守判"不充分"（不能拿缺失数据当宽松放行的理由）。
    """
    if limit_room is None:
        return False, "涨停空间数据缺失，暂无法判断"
    if limit_room < min_room_pct:
        return False, f"涨停空间仅剩 {limit_room:.1f}%，低于阈值 {min_room_pct:.1f}%，追高风险大"
    return True, None


def compute_stops(
    *,
    price: Optional[float],
    ma5: Optional[float],
    pullback_low: Optional[float],
    limit_down_pct: float,
) -> dict:
    """纯函数：三层止损位。price 缺失时全部返回 None（止损位必须锚定真实现价）。"""
    if price is None:
        return {"technical_stop": None, "standard_stop": None, "stress_stop": None}

    technical_stop = pullback_low if pullback_low is not None else ma5
    standard_stop = (
        round(technical_stop * (1 - STANDARD_STOP_BUFFER_PCT / 100), 2)
        if technical_stop is not None else None
    )
    stress_stop = round(price * (1 - limit_down_pct / 100), 2)

    return {
        "technical_stop": round(technical_stop, 2) if technical_stop is not None else None,
        "standard_stop": standard_stop,
        "stress_stop": stress_stop,
    }


def compute_stress_rr(
    *, price: Optional[float], stress_stop: Optional[float], limit_room: Optional[float],
) -> Optional[float]:
    """
    纯函数：压力情景风险回报比 = 剩余涨停空间% / 跌停亏损%。
    分子分母任一缺失或亏损%<=0（price异常）时返回 None，不编造比值。
    """
    if price is None or stress_stop is None or limit_room is None or price <= 0:
        return None
    risk_pct = (price - stress_stop) / price * 100
    if risk_pct <= 0:
        return None
    return round(limit_room / risk_pct, 2)
