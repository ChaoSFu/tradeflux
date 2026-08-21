"""
弱转强雷达 Leader Gate：Core Leader Score + 同 Theme（= Stock.primary_sector_id）
内排名 / "龙头未决" 判定。

核心打分/排序函数都是纯函数（只吃基础数值，不开 DB session），方便单测直接
构造数字调用。DB 相关的"查同 Theme 候选/查强势池全体分布"逻辑放在薄封装里。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from ..models.stock import Stock, StockDailySnapshot
from ..models.sector import StockSectorRelation
from . import w2s_config_service as cfg

CORE = "core"
BACKUP = "backup"
UNDETERMINED = "undetermined"
NON_LEADER = "non_leader"


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def compute_market_percentile(leader_score: float, universe_scores: list[float]) -> float:
    """纯函数：leader_score 在 universe_scores（一般是全体强势池 Stock.leader_score）里的百分位，0-1。"""
    if not universe_scores:
        return 0.0
    below = sum(1 for s in universe_scores if s < leader_score)
    return below / len(universe_scores)


def compute_core_leader_score(
    *,
    sector_rank_position: int,          # 1-based，在同 Theme 内按 Stock.leader_score 排名
    market_percentile: float,           # 0-1，compute_market_percentile 的结果
    board_count_60d: int,
    limit_up_days_20d: int,
    turnover_rate: Optional[float],
    yesterday_pct_change: Optional[float],
    is_sector_leader: bool,
) -> float:
    """纯函数：Core Leader Score（0-100）。所有输入都来自已有字段的组合，不新算连板/涨停天数。"""
    rs_in_sector_table = {1: 40, 2: 28, 3: 18, 4: 10}
    rs_in_sector = rs_in_sector_table.get(sector_rank_position, 0)

    rs_vs_market = _clamp(market_percentile * 15, 0, 15)

    board_history = _clamp(board_count_60d * 4 + limit_up_days_20d * 1.5, 0, 20)

    capital_capacity = _clamp((turnover_rate or 0) / 3, 0, 10)

    if yesterday_pct_change is None:
        divergence_resilience = 0.0
    elif yesterday_pct_change > -3:
        divergence_resilience = 10.0
    elif yesterday_pct_change > -6:
        divergence_resilience = 5.0
    else:
        divergence_resilience = 0.0

    sector_leadership_bonus = 5.0 if is_sector_leader else 0.0

    total = (
        rs_in_sector + rs_vs_market + board_history
        + capital_capacity + divergence_resilience + sector_leadership_bonus
    )
    return round(_clamp(total), 1)


@dataclass
class ThemeCandidate:
    code: str
    core_leader_score: float


@dataclass
class ThemeLeaderResult:
    code: str
    leader_type: str
    leader_rank: int


def rank_within_theme(candidates: list[ThemeCandidate], gap_threshold: float = 8.0) -> list[ThemeLeaderResult]:
    """
    纯函数：同一 Theme 内按 core_leader_score 排序，分配 leader_type/leader_rank。
    第一二名分差 < gap_threshold → 都标 undetermined（"龙头未决"，不强行指定）；
    分差达标 → 第一名 core、第二名 backup；rank>=3 → non_leader。
    """
    if not candidates:
        return []
    ordered = sorted(candidates, key=lambda c: c.core_leader_score, reverse=True)
    results: list[ThemeLeaderResult] = []
    gap = (ordered[0].core_leader_score - ordered[1].core_leader_score) if len(ordered) >= 2 else None

    for i, c in enumerate(ordered):
        rank = i + 1
        if rank == 1:
            leader_type = UNDETERMINED if (gap is not None and gap < gap_threshold) else CORE
        elif rank == 2:
            leader_type = UNDETERMINED if (gap is not None and gap < gap_threshold) else BACKUP
        else:
            leader_type = NON_LEADER
        results.append(ThemeLeaderResult(code=c.code, leader_type=leader_type, leader_rank=rank))
    return results


def score_leaders_for_theme(db: Session, sector_id: int, candidate_stock_ids: list[int]) -> dict[str, dict]:
    """
    薄封装：给定一个 Theme 里的候选股票，查出所需字段、算 Core Leader Score、
    做同 Theme 内排名，返回 {stock_code: {"core_leader_score", "leader_type", "leader_rank"}}。
    """
    if not candidate_stock_ids:
        return {}

    stocks = db.query(Stock).filter(Stock.id.in_(candidate_stock_ids)).all()
    if not stocks:
        return {}

    # 同 Theme 内按 Stock.leader_score 排名（不同于 Core Leader Score，这是基础分排名）
    theme_stocks = (
        db.query(Stock)
        .filter(Stock.primary_sector_id == sector_id, Stock.in_strong_pool == True)  # noqa: E712
        .order_by(Stock.leader_score.desc())
        .all()
    )
    sector_rank_by_id = {s.id: i + 1 for i, s in enumerate(theme_stocks)}

    universe_scores = [
        s.leader_score for s in db.query(Stock).filter(Stock.in_strong_pool == True).all()  # noqa: E712
    ]

    is_leader_by_stock_id = {
        r.stock_id
        for r in db.query(StockSectorRelation).filter(
            StockSectorRelation.sector_id == sector_id,
            StockSectorRelation.stock_id.in_(candidate_stock_ids),
            StockSectorRelation.is_leader == True,  # noqa: E712
        ).all()
    }

    # 昨日快照（用于分歧抗跌能力）——每只股票最近两条快照里较早的一条
    latest_dates = (
        db.query(StockDailySnapshot.stock_id, StockDailySnapshot.date)
        .filter(StockDailySnapshot.stock_id.in_(candidate_stock_ids))
        .order_by(StockDailySnapshot.stock_id, StockDailySnapshot.date.desc())
        .all()
    )
    seen: dict[int, list] = {}
    for sid, d in latest_dates:
        seen.setdefault(sid, []).append(d)
    yesterday_date_by_stock: dict[int, object] = {
        sid: dates[1] if len(dates) > 1 else None for sid, dates in seen.items()
    }
    yesterday_snaps: dict[int, StockDailySnapshot] = {}
    for sid, ydate in yesterday_date_by_stock.items():
        if ydate is None:
            continue
        snap = (
            db.query(StockDailySnapshot)
            .filter(StockDailySnapshot.stock_id == sid, StockDailySnapshot.date == ydate)
            .first()
        )
        if snap:
            yesterday_snaps[sid] = snap

    theme_candidates: list[ThemeCandidate] = []
    per_stock: dict[str, dict] = {}
    for stock in stocks:
        rank_pos = sector_rank_by_id.get(stock.id, 999)
        percentile = compute_market_percentile(stock.leader_score, universe_scores)
        yday = yesterday_snaps.get(stock.id)
        score = compute_core_leader_score(
            sector_rank_position=rank_pos,
            market_percentile=percentile,
            board_count_60d=stock.board_count_60d or 0,
            limit_up_days_20d=stock.limit_up_days_20d or 0,
            turnover_rate=(yday.turnover_rate if yday else None),
            yesterday_pct_change=(yday.pct_change if yday else None),
            is_sector_leader=(stock.id in is_leader_by_stock_id),
        )
        theme_candidates.append(ThemeCandidate(code=stock.code, core_leader_score=score))
        per_stock[stock.code] = {"core_leader_score": score}

    gap_threshold = cfg.get_numeric(db, cfg.KEY_LEADER_GAP_THRESHOLD)
    for r in rank_within_theme(theme_candidates, gap_threshold):
        per_stock[r.code]["leader_type"] = r.leader_type
        per_stock[r.code]["leader_rank"] = r.leader_rank

    return per_stock
