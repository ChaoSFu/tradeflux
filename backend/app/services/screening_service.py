"""
强势股筛选引擎。

职责：
  1. 从 K 线序列计算所有窗口统计指标
  2. 将指标与 ScreeningCriteria 对比，判断是否入池
  3. 计算情绪、风险、龙头评分
"""
from dataclasses import dataclass
from typing import List, Optional
from datetime import date

from sqlalchemy.orm import Session

from ..models.screening import ScreeningCriteria
from .eastmoney_fetcher import KLineBar


# ---------------------------------------------------------------------------
# 窗口统计结果
# ---------------------------------------------------------------------------

@dataclass
class StockWindowStats:
    """从 K 线计算出的所有窗口统计指标"""
    code: str
    name: str
    is_st: bool
    trading_days: int          # 总交易日数（判断次新股）

    # 今日数据
    today_close_price: float
    today_pct_change: float
    today_turnover: float
    today_is_limit_up: bool
    today_is_limit_down: bool
    today_is_broken_board: bool
    today_is_one_word_limit_up: bool
    today_is_one_word_limit_down: bool

    # 滚动窗口
    board_count_current: int       # 当前连续涨停数（截至今日）
    limit_down_count_current: int  # 当前连续跌停数（截至今日）
    board_count_60d: int           # 近60日最高连涨停板数
    board_down_count_60d: int      # 近60日最高连跌停数
    limit_up_days_60d: int     # 近60日涨停天数
    limit_up_days_20d: int     # 近20日涨停天数
    limit_up_days_10d: int     # 近10日涨停天数
    pct_change_60d: float      # 近60日累计涨幅
    pct_change_20d: float      # 近20日累计涨幅
    pct_change_10d: float      # 近10日累计涨幅

    # 计算评分
    emotion_score: float       # 0–100
    risk_score: float          # 0–100
    leader_score: float        # 0–100（粗算，最终评分由 dragon_leader_service 精算）

    # 是否被标记为次新
    is_new_stock: bool

    # 阶段分类
    ma60: float                # 60日均线（收盘价）
    ma30: float                # 30日均线（收盘价）
    consecutive_declines: int  # 从今日起连续下跌天数
    phase: str                 # "broken"（破位）| "weakening"（走弱）| "normal"


def compute_window_stats(
    code: str,
    name: str,
    is_st: bool,
    bars: List[KLineBar],
    new_stock_months: int = 12,
    listing_date: Optional[date] = None,
    is_sector_leader: bool = False,
) -> Optional[StockWindowStats]:
    """
    从 K 线序列计算所有窗口统计指标。
    bars 应按日期升序排列。
    """
    if not bars:
        return None

    bars = sorted(bars, key=lambda b: b.date)
    n = len(bars)

    recent_60 = bars[-60:] if n >= 60 else bars
    recent_20 = bars[-20:] if n >= 20 else bars
    recent_10 = bars[-10:] if n >= 10 else bars

    # 近 60/20/10 日涨停天数
    limit_up_days_60 = sum(1 for b in recent_60 if b.is_limit_up)
    limit_up_days_20 = sum(1 for b in recent_20 if b.is_limit_up)
    limit_up_days_10 = sum(1 for b in recent_10 if b.is_limit_up)

    # 近 60 日最高连板数 & 当前连续涨停数
    max_board = 0
    cur_board = 0
    for b in recent_60:
        if b.is_limit_up:
            cur_board += 1
            max_board = max(max_board, cur_board)
        else:
            cur_board = 0
    board_count_current = cur_board

    # 近 60 日最高连跌停数
    max_down_board = 0
    cur_down = 0
    for b in recent_60:
        if b.is_limit_down:
            cur_down += 1
            max_down_board = max(max_down_board, cur_down)
        else:
            cur_down = 0

    # 当前连续跌停数（从最近一日往历史倒推，遇到非跌停即停止）
    cur_limit_down = 0
    for b in reversed(bars):
        if b.is_limit_down:
            cur_limit_down += 1
        else:
            break
    limit_down_count_current = cur_limit_down

    # 近 60/20/10 日累计涨幅
    pct_60d = sum(b.pct_change for b in recent_60)
    pct_20d = sum(b.pct_change for b in recent_20)
    pct_10d = sum(b.pct_change for b in recent_10)

    # 次新股判断
    # 优先用上市日期（精确）；没有则用 K 线条数估算（65日数据下不可靠，保守设为 False）
    if listing_date is not None:
        from datetime import date as _today_date
        days_since_ipo = (_today_date.today() - listing_date).days
        is_new = days_since_ipo < new_stock_months * 30  # 月均约30日历天
    else:
        # 无上市日期时：若 K 线条数明显少于请求量（65条），可能是新股；
        # 否则保守判断为非次新，避免把所有老股都误判为次新
        is_new = n < 20  # 只有上市不足 20 交易日的才视为次新

    # 60日均线 / 30日均线（收盘价均值）
    recent_30 = bars[-30:] if n >= 30 else bars
    ma60 = sum(b.close_price for b in recent_60) / len(recent_60) if recent_60 else 0.0
    ma30 = sum(b.close_price for b in recent_30) / len(recent_30) if recent_30 else 0.0

    # 从今日起连续下跌天数（向历史倒推，遇到非负涨幅即停止）
    consecutive_declines = 0
    for b in reversed(bars):
        if b.pct_change < 0:
            consecutive_declines += 1
        else:
            break

    # 今日数据
    today = bars[-1]

    # 阶段分类（优先级：破位 > 走弱 > 正常）
    if ma60 > 0 and today.close_price < ma60:
        phase = "broken"       # 跌破60日均线 → 破位
    elif consecutive_declines >= 4 or (ma30 > 0 and today.close_price < ma30):
        phase = "weakening"    # 连跌4天及以上，或跌破30日均线（且未破60日均线）→ 走弱
    else:
        phase = "normal"

    # ── 情绪分：衡量市场参与度与量能热度 ──────────────────────────────────
    #
    # 核心改进：加入换手率趋势（近5日 vs 近20日），区分"量能放大"与"缩量维持"
    # 排除今日数据（收盘后 turnover_rate 可能为 0，影响趋势判断）
    bars_ex = bars[:-1]  # 排除今日
    t5_vals  = [b.turnover_rate for b in bars_ex[-5:]  if b.turnover_rate > 0]
    t20_vals = [b.turnover_rate for b in bars_ex[-20:] if b.turnover_rate > 0]
    if t5_vals and t20_vals:
        avg_t5  = sum(t5_vals)  / len(t5_vals)
        avg_t20 = sum(t20_vals) / len(t20_vals)
        turn_ratio = avg_t5 / avg_t20 if avg_t20 > 0 else 1.0
    else:
        turn_ratio = 1.0
    # 0.5x→0分, 1.0x→5分, 1.5x→10分, 2.0x→15分（上限15）
    turn_expansion = min(15.0, max(0.0, (turn_ratio - 0.5) * 10.0))

    emotion = min(100.0,
        limit_up_days_60 * 1.2                  # 历史活跃度（降权，与龙头分解耦）
        + max_board * 5.0                        # 历史板高
        + (12.0 if today.is_limit_up else 0)
        + turn_expansion                         # ★ 量能扩张信号（0–15）
        + (today.turnover_rate * 0.8 if today.turnover_rate > 0 else 0)
    )

    # ── 风险分：衡量下行风险烈度 ──────────────────────────────────────────
    #
    # 修复：
    #   1. 移除"活跃度不足"因子（逻辑错误：刚启动的票会被误判高风险）
    #   2. 炸板加入时间权重（近3日 >> 3-10日）
    #   3. 加入 consecutive_declines（连续下跌是走弱最直接信号）
    recent_3_broken = sum(1 for b in recent_10[-3:] if b.is_broken_board)
    older_broken    = sum(1 for b in recent_10[:-3] if b.is_broken_board)
    risk = min(100.0,
        recent_3_broken * 28.0               # 近3日炸板（高危）
        + older_broken * 12.0               # 3–10日炸板（中危）
        + max(0, max_board - 4) * 8.0       # 高板位分歧风险
        + (15.0 if today.is_limit_down else 0)
        + min(30.0, consecutive_declines * 8.0)  # ★ 连续下跌（已有变量直接用）
    )

    # ── 龙头分：衡量当前板块领涨地位 ────────────────────────────────────
    #
    # 因子                 满分   说明
    # ─────────────────────────────────────────────────────────────────────
    # 当前连板数            30    正在进行的涨停序列（0–3 → 0–30）
    # 近10日涨停密度        30    最近两周活跃度（0–7 → 0–30）
    # 情绪归一化            20    综合热度（经验范围 20–80）
    # 历史板高              12    60日最高板数（1–8 → 0–12）
    # 60日涨停密度           8    持续活跃度（3–21 → 0–8）
    # 今日涨停加分          +5    状态奖励
    # 今日换手加分          +5    量能配合
    # ★ 板块龙头加成        +12   板块内 is_leader 标记
    # 炸板惩罚             −12    信号破坏
    # ─────────────────────────────────────────────────────────────────────
    streak_score   = min(30.0, board_count_current * 11.0)
    recent_score   = (limit_up_days_10 / 7.0) * 30.0
    emotion_norm   = max(0.0, (emotion - 20.0) / 60.0) * 20.0
    hist_score     = max(0.0, (max_board - 1) / 7.0) * 12.0
    density_score  = max(0.0, (limit_up_days_60 - 3) / 18.0) * 8.0
    today_bonus    = 5.0 if today.is_limit_up else 0.0
    turnover_bonus = min(5.0, today.turnover_rate * 0.5)
    sector_bonus   = 12.0 if is_sector_leader else 0.0   # ★ 板块龙头加成
    broken_penalty = 12.0 if today.is_broken_board else 0.0
    leader = max(0.0, min(100.0,
        streak_score + recent_score + emotion_norm + hist_score + density_score
        + today_bonus + turnover_bonus + sector_bonus - broken_penalty
    ))

    return StockWindowStats(
        code=code,
        name=name,
        is_st=is_st,
        trading_days=n,
        today_close_price=today.close_price,
        today_pct_change=today.pct_change,
        today_turnover=today.turnover_rate,
        today_is_limit_up=today.is_limit_up,
        today_is_limit_down=today.is_limit_down,
        today_is_broken_board=today.is_broken_board,
        today_is_one_word_limit_up=today.is_one_word_limit_up,
        today_is_one_word_limit_down=today.is_one_word_limit_down,
        board_count_current=board_count_current,
        limit_down_count_current=limit_down_count_current,
        board_count_60d=max_board,
        board_down_count_60d=max_down_board,
        limit_up_days_60d=limit_up_days_60,
        limit_up_days_20d=limit_up_days_20,
        limit_up_days_10d=limit_up_days_10,
        pct_change_60d=pct_60d,
        pct_change_20d=pct_20d,
        pct_change_10d=pct_10d,
        emotion_score=round(emotion, 1),
        risk_score=round(risk, 1),
        leader_score=round(leader, 1),
        is_new_stock=is_new,
        ma60=round(ma60, 3),
        ma30=round(ma30, 3),
        consecutive_declines=consecutive_declines,
        phase=phase,
    )


# ---------------------------------------------------------------------------
# 筛选条件评估
# ---------------------------------------------------------------------------

def evaluate_criteria(
    stats: StockWindowStats,
    criteria: ScreeningCriteria,
    all_pct_20d: List[float],
) -> bool:
    """
    判断一只股票是否满足筛选条件。

    静态过滤（AND，全部满足才不被排除）：
      - exclude_st: 排除 ST
      - exclude_new_stock: 排除次新

    动态入池条件（OR，任一满足即可）：
      - min_board_count_60d
      - min_limit_up_days_60d
      - min_limit_up_days_10d
      - top_pct_rank_20d
    """
    # 静态过滤
    if criteria.exclude_st and stats.is_st:
        return False
    if criteria.exclude_new_stock and stats.is_new_stock:
        return False

    # 计算 20 日涨幅百分位排名（越小越靠前）
    top_rank_pct: Optional[float] = None
    if all_pct_20d:
        rank = sum(1 for p in all_pct_20d if p > stats.pct_change_20d)
        top_rank_pct = rank / len(all_pct_20d) * 100  # 0% = 第一名

    # 动态条件（任一满足）
    qualifies = False

    if criteria.min_board_count_60d is not None:
        if stats.board_count_60d > criteria.min_board_count_60d:
            qualifies = True

    if not qualifies and criteria.min_limit_up_days_60d is not None:
        if stats.limit_up_days_60d > criteria.min_limit_up_days_60d:
            qualifies = True

    if not qualifies and criteria.min_limit_up_days_10d is not None:
        if stats.limit_up_days_10d > criteria.min_limit_up_days_10d:
            qualifies = True

    if not qualifies and criteria.top_pct_rank_20d is not None and top_rank_pct is not None:
        if top_rank_pct <= criteria.top_pct_rank_20d:
            qualifies = True

    return qualifies


# ---------------------------------------------------------------------------
# DB 辅助
# ---------------------------------------------------------------------------

def get_active_criteria(db: Session) -> Optional[ScreeningCriteria]:
    """获取当前生效的筛选条件（取最新一条 is_active=True）"""
    return (
        db.query(ScreeningCriteria)
        .filter(ScreeningCriteria.is_active == True)  # noqa: E712
        .order_by(ScreeningCriteria.updated_at.desc())
        .first()
    )
