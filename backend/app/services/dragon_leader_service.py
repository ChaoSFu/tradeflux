"""
Dragon Leader Engine — identifies the market's leading stocks.

Leader types:
  overall       : 总龙头 — highest composite score
  emotion       : 情绪龙 — highest emotion_score × board_height
  trend         : 趋势龙 — highest continuity/momentum
  compensation  : 补涨龙 — weak now, sector gaining, recovery signal
  mid_cap       : 中盘核心

Score formula (rule-based, AI-ready override):
  leader_score = (
      board_streak × 15          # 当前连续涨停数（最直接龙头信号）
    + turnover_rate × 10         # 今日换手率（资金关注度）
    + limit_up_days_60d × 8      # 60日涨停密度（历史活跃度）
    + emotion_score × 0.4        # 综合情绪（含量能热度）
    + trend_continuity × 1.0     # 近期趋势连续性（最高30分）
    + sector_emotion × 0.1       # 板块情绪加成
  )
  trend_continuity = min(30, limit_up_days_10d × 3 + max(0, pct_change_10d) × 0.1)
"""
from sqlalchemy.orm import Session
from typing import List
from ..models.stock import Stock, StockDailySnapshot
from ..models.sector import Sector
from ..schemas.market_state import DragonLeader


def _compute_leader_score(stock: Stock, sector_emotion: float) -> float:
    latest = stock.daily_snapshots[-1] if stock.daily_snapshots else None
    board_streak = latest.board_count if latest else 0
    turnover = (latest.turnover_rate or 0.0) if latest else 0.0

    # 近期趋势连续性：10日涨停密度 + 10日累计涨幅（弱信号）
    trend_continuity = min(30.0,
        stock.limit_up_days_10d * 3.0
        + max(0.0, stock.pct_change_10d) * 0.1
    )

    # 换手率贡献上限15分：10%换手→5分，20%→10分，30%以上→15分
    turnover_contrib = min(15.0, turnover / 2.0)

    return (
        board_streak * 15                  # 当前连板（最直接的龙头信号，0–3板→0–45分）
        + turnover_contrib                 # 今日换手上限15分
        + stock.limit_up_days_60d * 8      # 60日涨停密度（历史活跃度）
        + stock.emotion_score * 0.4        # 综合情绪（含量能热度，最高40分）
        + trend_continuity * 1.0           # 近期趋势连续性（最高30分）
        + sector_emotion * 0.1             # 板块加成
    )


def identify_dragon_leaders(db: Session, top_n: int = 6) -> List[DragonLeader]:
    strong_stocks = (
        db.query(Stock)
        .filter(Stock.in_strong_pool == True)  # noqa: E712
        .all()
    )
    if not strong_stocks:
        return []

    # ── 主板块：直接读落库的 primary_sector（与所有模块一致）────────────────
    primary_sids = {s.primary_sector_id for s in strong_stocks if s.primary_sector_id}
    sector_map: dict[int, Sector] = {}
    if primary_sids:
        sector_map = {s.id: s for s in db.query(Sector).filter(Sector.id.in_(primary_sids)).all()}

    # ── 评分 & 排序 ──────────────────────────────────────────────────────
    scored: list[tuple[float, Stock, str, str]] = []
    for stock in strong_stocks:
        sector = sector_map.get(stock.primary_sector_id) if stock.primary_sector_id else None
        sector_emotion = sector.emotion_score if sector else 0.0
        score = _compute_leader_score(stock, sector_emotion)
        leader_type = _determine_leader_type(stock, sector)
        sector_name = stock.primary_sector_name or "Unknown"
        scored.append((score, stock, leader_type, sector_name))

    scored.sort(key=lambda x: x[0], reverse=True)

    leaders = []
    for score, stock, ltype, sector_name in scored[:top_n]:
        latest = stock.daily_snapshots[-1] if stock.daily_snapshots else None
        leaders.append(
            DragonLeader(
                stock_code=stock.code,
                stock_name=stock.name,
                sector_name=sector_name,
                leader_type=ltype,
                board_height=latest.board_count if latest else 0,
                leader_score=round(score, 1),
                risk_score=stock.risk_score,
            )
        )
    return leaders


def _determine_leader_type(stock: Stock, sector: Sector | None) -> str:
    if stock.emotion_score >= 80 and stock.board_count_60d >= 5:
        return "emotion"
    if stock.limit_up_days_60d >= 12:
        return "overall"
    if sector and sector.phase in (4, 5) and stock.risk_score < 40:
        return "compensation"
    if stock.leader_score >= 70:
        return "trend"
    return "mid_cap"
