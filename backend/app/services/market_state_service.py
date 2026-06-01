"""
Market State Engine — synthesizes cross-sector signals into an overall market picture.

Outputs:
  - market_phase          : bear_fear | caution | neutral | warm | bull_frenzy
  - profit_effect_score   : 赚钱效应 0–100
  - loss_effect_score     : 亏钱效应 0–100
  - emotion_cycle         : dormant | awakening | heating | euphoric | cooling | cold
  - emotional_temperature : 0–100
  - suggested_position    : 0–100 % (risk-adjusted)
"""
from sqlalchemy.orm import Session
from datetime import date
from typing import List
from ..models.stock import Stock, StockDailySnapshot
from ..models.sector import Sector, StockSectorRelation
from ..models.review import DailyReview
from ..schemas.market_state import (
    MarketStateResponse,
    MarketHistoryPoint,
    ActiveSector,
    DragonLeader,
    WeakToStrongCandidate,
    ProfitEffectGroup,
    SectorProfitEffect,
    ProfitEffectResponse,
)
from ..schemas.sector import PHASE_LABELS
from .dragon_leader_service import identify_dragon_leaders
from .weak_to_strong_service import detect_weak_to_strong_candidates


def _compute_profit_effect(sectors: List[Sector]) -> float:
    """赚钱效应: average emotion_score of expanding/euphoric sectors."""
    hot = [s for s in sectors if s.phase in (2, 3)]
    if not hot:
        return 10.0
    return min(100, sum(s.emotion_score for s in hot) / len(hot))


def _compute_loss_effect(sectors: List[Sector]) -> float:
    """亏钱效应: weighted risk from declining/dead sectors."""
    cold = [s for s in sectors if s.phase in (5, 6)]
    if not cold:
        return 10.0
    avg_risk = sum(s.risk_score for s in cold) / len(cold)
    return min(100, avg_risk * (len(cold) / max(len(sectors), 1)) * 2)


def _emotion_temperature(profit: float, loss: float) -> float:
    return max(0, min(100, profit - loss * 0.5 + 30))


def _classify_market_phase(temp: float) -> str:
    if temp >= 80:
        return "bull_frenzy"
    if temp >= 65:
        return "warm"
    if temp >= 45:
        return "neutral"
    if temp >= 30:
        return "caution"
    return "bear_fear"


def _classify_emotion_cycle(temp: float) -> str:
    if temp >= 80:
        return "euphoric"
    if temp >= 65:
        return "heating"
    if temp >= 50:
        return "awakening"
    if temp >= 35:
        return "cooling"
    if temp >= 20:
        return "dormant"
    return "cold"


def _suggested_position(phase: str, loss_effect: float) -> float:
    base = {
        "bull_frenzy": 70,
        "warm": 55,
        "neutral": 40,
        "caution": 25,
        "bear_fear": 10,
    }.get(phase, 30)
    # Penalise when loss effect is elevated
    return max(5, base - loss_effect * 0.2)


def get_current_market_state(db: Session) -> MarketStateResponse:
    # ── 只使用「已关注」板块（Sector Config 的单一控制点）────────────────
    sectors = db.query(Sector).filter(Sector.is_watched == True).all()  # noqa: E712
    profit = _compute_profit_effect(sectors)
    loss = _compute_loss_effect(sectors)
    temp = _emotion_temperature(profit, loss)
    phase = _classify_market_phase(temp)
    cycle = _classify_emotion_cycle(temp)
    position = _suggested_position(phase, loss)

    # 活跃板块：仅 Phase 2（扩张期）以上，Phase 1（启动期）不纳入，避免虚高
    active_sectors = [
        ActiveSector(
            sector_code=s.code,
            sector_name=s.name,
            phase=s.phase,
            phase_label=PHASE_LABELS.get(s.phase, "Unknown"),
            emotion_score=s.emotion_score,
            strong_stock_count=s.strong_stock_count,
            board_height=s.board_height,
        )
        for s in sorted(sectors, key=lambda x: x.emotion_score, reverse=True)
        if s.phase in (2, 3)
    ]

    dangerous = [s.name for s in sectors if s.phase in (5, 6)]
    strong = [s.name for s in sectors if s.phase in (2, 3)]

    today = date.today()

    return MarketStateResponse(
        date=today,
        market_phase=phase,
        profit_effect_score=round(profit, 1),
        loss_effect_score=round(loss, 1),
        emotion_cycle=cycle,
        emotional_temperature=round(temp, 1),
        suggested_position_level=round(position, 1),
        active_sectors=active_sectors,
        dangerous_sectors=dangerous,
        strong_sectors=strong,
        dragon_leaders=identify_dragon_leaders(db),
        weak_to_strong_candidates=detect_weak_to_strong_candidates(db, as_of=today),
    )


def get_market_history(db: Session, days: int = 30) -> List[MarketHistoryPoint]:
    reviews = (
        db.query(DailyReview)
        .order_by(DailyReview.date.desc())
        .limit(days)
        .all()
    )
    def _parse_groups(raw) -> list:
        if not raw:
            return []
        try:
            return [ProfitEffectGroup(**g) for g in raw]
        except Exception:
            return []

    return [
        MarketHistoryPoint(
            date=r.date,
            profit_effect_score=r.profit_effect_score,
            loss_effect_score=r.loss_effect_score,
            strong_pool_avg_pct=getattr(r, "strong_pool_avg_pct", None),
            profit_effect_groups=_parse_groups(getattr(r, "profit_effect_groups", None)),
            emotional_temperature=r.emotional_temperature,
            suggested_position_level=r.suggested_position_level,
            market_phase=r.market_phase,
        )
        for r in reversed(reviews)
    ]


def get_profit_effect(db: Session, min_stocks: int = 3) -> ProfitEffectResponse:
    """当日赚钱效应 — 强势股池整体 + 按前日状态分组 + 板块维度."""

    _empty = ProfitEffectResponse(
        date=date.today(),
        has_data=False,
        overall_avg_pct=0.0,
        overall_up_count=0,
        overall_down_count=0,
        overall_flat_count=0,
        overall_limit_up_count=0,
        overall_limit_down_count=0,
        groups=[],
        sectors=[],
    )

    # ── 找最近两个交易日快照日期 ────────────────────────────────────────────
    recent_dates = (
        db.query(StockDailySnapshot.date)
        .distinct()
        .order_by(StockDailySnapshot.date.desc())
        .limit(2)
        .all()
    )
    if not recent_dates:
        return _empty

    today_date = recent_dates[0][0]
    yesterday_date = recent_dates[1][0] if len(recent_dates) > 1 else None

    # ── 强势股池 ────────────────────────────────────────────────────────────
    strong_stocks = db.query(Stock).filter(Stock.in_strong_pool == True).all()
    if not strong_stocks:
        return _empty

    stock_map: dict[int, Stock] = {s.id: s for s in strong_stocks}
    stock_id_list = list(stock_map.keys())

    # ── 今日快照 ────────────────────────────────────────────────────────────
    today_snaps: dict[int, StockDailySnapshot] = {
        snap.stock_id: snap
        for snap in db.query(StockDailySnapshot).filter(
            StockDailySnapshot.date == today_date,
            StockDailySnapshot.stock_id.in_(stock_id_list),
        ).all()
    }

    if not today_snaps:
        return _empty

    # ── 昨日快照（仅用于判断前日状态）──────────────────────────────────────
    yesterday_snaps: dict[int, StockDailySnapshot] = {}
    if yesterday_date:
        yesterday_snaps = {
            snap.stock_id: snap
            for snap in db.query(StockDailySnapshot).filter(
                StockDailySnapshot.date == yesterday_date,
                StockDailySnapshot.stock_id.in_(stock_id_list),
            ).all()
        }

    # ── 整体统计 ────────────────────────────────────────────────────────────
    all_pcts: List[float] = []
    all_lu = 0
    all_ld = 0
    for snap in today_snaps.values():
        if snap.pct_change is not None:
            all_pcts.append(snap.pct_change)
        if snap.is_limit_up:
            all_lu += 1
        if snap.is_limit_down:
            all_ld += 1

    if not all_pcts:
        return _empty

    def _classify(p: float) -> str:
        if p > 0.5:
            return "up"
        if p < -0.5:
            return "down"
        return "flat"

    overall_up = sum(1 for p in all_pcts if _classify(p) == "up")
    overall_down = sum(1 for p in all_pcts if _classify(p) == "down")
    overall_flat = len(all_pcts) - overall_up - overall_down

    # ── 分组统计 ─────────────────────────────────────────────────────────────
    # 分组依据：前日状态
    #   limit_up   — 前日涨停 (yesterday is_limit_up=True)
    #   broken     — 当前 stock.phase='broken' (破位)
    #   weakening  — 当前 stock.phase='weakening' (走弱)
    #   oscillation — 其余强势股 (震荡)
    GROUP_ORDER = ["limit_up", "oscillation", "weakening", "broken"]
    GROUP_LABELS = {
        "limit_up": "昨日涨停龙头",
        "oscillation": "昨日震荡龙头",
        "weakening": "昨日走弱龙头",
        "broken": "昨日破位龙头",
    }
    groups_data: dict[str, dict] = {
        k: {"pcts": [], "up": 0, "down": 0, "flat": 0} for k in GROUP_ORDER
    }

    for stock_id, snap in today_snaps.items():
        if snap.pct_change is None:
            continue
        p = snap.pct_change
        stock = stock_map.get(stock_id)
        if stock is None:
            continue

        prev = yesterday_snaps.get(stock_id)
        if prev and prev.is_limit_up:
            # 前日涨停（快照 flag 最准确）
            gk = "limit_up"
        elif prev and prev.phase in ("broken", "weakening"):
            # 前日阶段已落库（daily_update 写入）：直接使用
            gk = prev.phase  # type: ignore[assignment]
        else:
            # 兜底：前日快照无 phase（历史数据未回填）或无快照时，用当前 stock.phase 近似
            if stock.phase == "broken":
                gk = "broken"
            elif stock.phase == "weakening":
                gk = "weakening"
            else:
                gk = "oscillation"

        gd = groups_data[gk]
        gd["pcts"].append(p)
        c = _classify(p)
        gd[c] += 1

    groups: List[ProfitEffectGroup] = []
    for key in GROUP_ORDER:
        gd = groups_data[key]
        pcts = gd["pcts"]
        groups.append(ProfitEffectGroup(
            key=key,
            label=GROUP_LABELS[key],
            stock_count=len(pcts),
            avg_pct=round(sum(pcts) / len(pcts), 2) if pcts else 0.0,
            up_count=gd["up"],
            down_count=gd["down"],
            flat_count=gd["flat"],
        ))

    # ── 板块维度 ─────────────────────────────────────────────────────────────
    # 只统计强势股所属的「已关注」板块（与 SectorPool 的 is_watched 过滤保持一致）
    relations = (
        db.query(StockSectorRelation)
        .filter(StockSectorRelation.stock_id.in_(stock_id_list))
        .all()
    )

    sector_stock_ids: dict[int, List[int]] = {}
    for rel in relations:
        sector_stock_ids.setdefault(rel.sector_id, []).append(rel.stock_id)

    # 只加载已关注的板块（与 SectorPool 的 _should_show_sector_tag 一致）
    all_sector_map: dict[int, Sector] = {
        s.id: s
        for s in db.query(Sector).filter(Sector.is_watched == True).all()
    }

    sector_effects: List[SectorProfitEffect] = []
    for sector_id, sids in sector_stock_ids.items():
        sector = all_sector_map.get(sector_id)
        if sector is None:
            continue  # 未关注板块跳过
        pcts: List[float] = []
        up, down, flat = 0, 0, 0
        for sid in sids:
            snap = today_snaps.get(sid)
            if snap and snap.pct_change is not None:
                p = snap.pct_change
                pcts.append(p)
                c = _classify(p)
                if c == "up":
                    up += 1
                elif c == "down":
                    down += 1
                else:
                    flat += 1
        # 与 SectorPool 的 min_stocks 过滤保持一致
        if len(pcts) < min_stocks:
            continue
        sector_effects.append(SectorProfitEffect(
            sector_code=sector.code,
            sector_name=sector.name,
            stock_count=len(pcts),
            up_count=up,
            down_count=down,
            avg_pct=round(sum(pcts) / len(pcts), 2),
            sector_pct_today=round(sector.pct_change_30d or 0.0, 2),
        ))

    sector_effects.sort(key=lambda x: x.avg_pct, reverse=True)

    return ProfitEffectResponse(
        date=today_date,
        has_data=True,
        overall_avg_pct=round(sum(all_pcts) / len(all_pcts), 2),
        overall_up_count=overall_up,
        overall_down_count=overall_down,
        overall_flat_count=overall_flat,
        overall_limit_up_count=all_lu,
        overall_limit_down_count=all_ld,
        groups=groups,
        sectors=sector_effects,
    )
