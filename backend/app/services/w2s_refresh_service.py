"""
弱转强雷达快速刷新编排：只碰 weak_to_strong_candidates/weak_to_strong_events
这两张自己的表 + 一次性批量实时报价，不触发/不依赖全量 daily_update。

流程：查 is_active 候选 → 按 Theme（Stock.primary_sector_id）分组跑 Sector
Gate/Leader Gate → 批量拉实时报价 → 状态机 → 状态真变化才写一条事件日志。
目标 <5-10 秒，所以只查候选相关的少量 sector，不碰全市场/888个板块。
"""
from __future__ import annotations

from datetime import date as date_cls, datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models.stock import Stock, StockDailySnapshot
from ..models.sector import Sector
from ..models.regulatory import RegulatoryUnusual
from ..models.weak_to_strong_radar import WeakToStrongCandidate, WeakToStrongEvent
from .eastmoney_fetcher import fetch_stock_quotes_batch, get_limit_pct
from . import w2s_config_service as cfg
from . import w2s_sector_gate_service as sector_gate
from . import w2s_leader_gate_service as leader_gate
from . import w2s_state_machine as sm
from .w2s_candidate_service import compute_ma, _recent_closes

AUCTION_CUTOFF_HOUR_MINUTE = (9, 25)  # 9:25 集合竞价结束


def _market_code(market: str) -> int:
    return 1 if (market or "").upper() == "SH" else 0


def _resolve_regulatory_risk(db: Session, code: str, today: date_cls) -> str:
    row = (
        db.query(RegulatoryUnusual)
        .filter(RegulatoryUnusual.security_code == code)
        .order_by(RegulatoryUnusual.predict_end.desc().nullslast())
        .first()
    )
    if row is None:
        return sm.LOW
    is_under = (row.predict_end is None or row.predict_end >= today) and (
        row.predict_start is None or row.predict_start <= today
    )
    days_lifted = (today - row.predict_end).days if (not is_under and row.predict_end) else None
    return sm.classify_regulatory_risk(is_under, days_lifted)


def run_refresh(db: Session, now: Optional[datetime] = None) -> dict:
    """
    执行一次快速刷新，返回 {"refreshed": n, "state_changed": n, "quote_missing": n, "duration_ms": int}。
    """
    started = datetime.now()
    now = now or started
    today = now.date()

    candidates = (
        db.query(WeakToStrongCandidate).filter(WeakToStrongCandidate.is_active == True).all()  # noqa: E712
    )
    stats = {"refreshed": 0, "state_changed": 0, "quote_missing": 0, "duration_ms": 0}
    if not candidates:
        stats["duration_ms"] = int((datetime.now() - started).total_seconds() * 1000)
        return stats

    stock_by_id = {s.id: s for s in db.query(Stock).filter(Stock.id.in_([c.stock_id for c in candidates])).all()}

    # 按 Theme（primary_sector_id）分组，跑 Sector Gate / Leader Gate
    sector_ids = {s.primary_sector_id for s in stock_by_id.values() if s.primary_sector_id}
    sectors_by_id = {s.id: s for s in db.query(Sector).filter(Sector.id.in_(sector_ids)).all()}
    sector_score_cache: dict[int, dict] = {}
    for sid, sector in sectors_by_id.items():
        sector_score_cache[sid] = sector_gate.score_sector(db, sector, today)

    theme_groups: dict[int, list[int]] = {}
    for cand in candidates:
        stock = stock_by_id.get(cand.stock_id)
        if stock is None or not stock.primary_sector_id:
            continue
        theme_groups.setdefault(stock.primary_sector_id, []).append(stock.id)
    leader_score_cache: dict[str, dict] = {}
    for sid, stock_ids in theme_groups.items():
        leader_score_cache.update(leader_gate.score_leaders_for_theme(db, sid, stock_ids))

    codes_markets = [
        (stock_by_id[c.stock_id].code, _market_code(stock_by_id[c.stock_id].market))
        for c in candidates if c.stock_id in stock_by_id
    ]
    quotes = fetch_stock_quotes_batch(codes_markets)

    sector_gate_allowed = cfg.get_sector_gate_allowed(db)
    regulatory_risk_cap = cfg.get_regulatory_risk_cap(db)
    auction_gap_min = cfg.get_numeric(db, cfg.KEY_AUCTION_GAP_MIN)
    is_after_auction = (now.hour, now.minute) >= AUCTION_CUTOFF_HOUR_MINUTE
    formula_version = cfg.get_string(db, cfg.KEY_FORMULA_VERSION)

    for cand in candidates:
        stock = stock_by_id.get(cand.stock_id)
        if stock is None:
            continue
        quote = quotes.get(stock.code)
        if quote is None:
            stats["quote_missing"] += 1
            cand.signal_enabled = sm.check_data_freshness(cand.last_refreshed_at, now)
            continue

        sector_info = sector_score_cache.get(stock.primary_sector_id, {})
        leader_info = leader_score_cache.get(stock.code, {})
        leader_type = leader_info.get("leader_type", "undetermined")

        closes = _recent_closes(db, stock.id, today, limit=5)
        ma5 = compute_ma(closes, 5)
        limit_pct = get_limit_pct(stock.code, stock.is_st)
        limit_price = round(quote.prev_close * (1 + limit_pct / 100), 2) if quote.prev_close else None
        limit_room = (
            round((limit_price - quote.price) / quote.price * 100, 2)
            if (limit_price is not None and quote.price)
            else None
        )
        regulatory_risk = _resolve_regulatory_risk(db, stock.code, today)

        new_state, triggers, blocks = sm.compute_next_state(
            current_state=cand.current_state,
            signal_enabled=True,
            sector_category=sector_info.get("sector_category", "DEAD"),
            sector_gate_allowed=sector_gate_allowed,
            leader_type=leader_type,
            regulatory_risk=regulatory_risk,
            regulatory_risk_cap=regulatory_risk_cap,
            is_observation_expired=False,
            price=quote.price,
            prev_close=quote.prev_close,
            ma5=ma5,
            pullback_low=cand.pullback_low,
            auction_gap=quote.pct_change,
            auction_gap_min=auction_gap_min,
            is_after_auction=is_after_auction,
        )

        if new_state == sm.REPAIRING and quote.price is not None:
            cand.pullback_low = quote.price if cand.pullback_low is None else min(cand.pullback_low, quote.price)
        if new_state in (sm.WATCH, sm.WAIT):
            cand.pullback_low = None

        old_state = cand.current_state
        cand.sector_id = stock.primary_sector_id
        cand.sector_name = stock.primary_sector_name
        cand.sector_category = sector_info.get("sector_category")
        cand.sector_strength_score = sector_info.get("sector_strength_score")
        cand.sector_momentum_score = sector_info.get("sector_momentum_score")
        cand.leader_type = leader_type
        cand.leader_rank = leader_info.get("leader_rank")
        cand.leader_score = leader_info.get("core_leader_score")
        cand.current_state = new_state
        cand.price = quote.price
        cand.prev_close = quote.prev_close
        cand.ma5 = ma5
        cand.day_open = quote.open
        cand.day_high = quote.high
        cand.day_low = quote.low
        cand.day_amount = quote.amount
        cand.turnover_rate = quote.turnover_rate
        # 没有独立的"9:25集合竞价专属价"数据源，Phase 1 用当前报价涨跌幅近似——
        # 跟传给状态机的 auction_gap 输入必须是同一个值，否则前端展示的
        # auction_gap 会跟实际驱动 READY 判断的数字对不上。
        cand.auction_gap = quote.pct_change
        cand.limit_price = limit_price
        cand.limit_room = limit_room
        cand.regulatory_risk_level = regulatory_risk
        cand.signal_enabled = True
        cand.data_freshness_seconds = 0.0
        cand.trigger_reasons = "；".join(triggers) if triggers else None
        cand.block_reasons = "；".join(blocks) if blocks else None
        cand.refresh_sample_count = (cand.refresh_sample_count or 0) + 1
        cand.last_refreshed_at = now
        cand.formula_version = formula_version

        stats["refreshed"] += 1
        if new_state != old_state:
            stats["state_changed"] += 1
            db.add(WeakToStrongEvent(
                timestamp=now, stock_code=stock.code, sector_id=stock.primary_sector_id,
                sector_phase=cand.sector_category, sector_strength=cand.sector_strength_score,
                sector_momentum=cand.sector_momentum_score,
                leader_type=leader_type, leader_rank=cand.leader_rank, leader_score=cand.leader_score,
                setup_state=new_state,
                price=quote.price, prev_close=quote.prev_close, ma5=ma5,
                limit_price=limit_price, limit_room=limit_room,
                regulatory_risk=regulatory_risk,
                old_state=old_state, new_state=new_state,
                trigger_reasons=cand.trigger_reasons, block_reasons=cand.block_reasons,
                data_freshness=0.0, formula_version=formula_version,
            ))

    db.commit()
    stats["duration_ms"] = int((datetime.now() - started).total_seconds() * 1000)
    return stats
