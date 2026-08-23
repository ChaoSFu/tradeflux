"""
弱转强雷达快速刷新编排：只碰 weak_to_strong_candidates/weak_to_strong_events
这两张自己的表 + 一次性批量实时报价，不触发/不依赖全量 daily_update。

流程：查 is_active 候选 → 跨日重置（signal_trade_date 不是今天则清空日内结构
字段）→ 按 Theme（Stock.primary_sector_id）分组跑 Sector Gate/Leader Gate →
批量拉实时报价（算真实VWAP/真实竞价Gap）→ 状态机（结构态持续推进 + 闸门覆盖
展示态）→ 展示态真变化才写一条事件日志。目标 <5-10 秒，所以只查候选相关的
少量 sector，不碰全市场/888个板块。
"""
from __future__ import annotations

from datetime import date as date_cls, datetime
from typing import Optional

from sqlalchemy.orm import Session

from ..models.stock import Stock, StockDailySnapshot
from ..models.sector import Sector
from ..models.regulatory import RegulatoryUnusual
from ..models.weak_to_strong_radar import WeakToStrongCandidate, WeakToStrongEvent, WeakToStrongSnapshot
from .eastmoney_fetcher import fetch_stock_quotes_batch, get_limit_pct
from . import w2s_config_service as cfg
from . import w2s_sector_gate_service as sector_gate
from . import w2s_leader_gate_service as leader_gate
from . import w2s_state_machine as sm
from . import w2s_market_gate_service as market_gate
from . import w2s_risk_service as risk
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


def _compute_vwap(
    amount: Optional[float], volume: Optional[float],
    low: Optional[float] = None, high: Optional[float] = None,
) -> Optional[float]:
    """
    纯函数：当日成交额/成交量算出真实VWAP。`volume` 参数按 StockQuote 的统一
    约定传入，单位已经是"股"（不是"手"）——东财/腾讯的原始字段单位是"手"，
    换算成股这一步在各自的解析函数（_fetch_quotes_eastmoney/
    _parse_tencent_quote_line）里已经做过了，这里不再重复*100。

    2026-08-23修复：此前这里无条件*100，隐含假设"所有数据源的volume字段
    单位都是手"——但新浪的公开行情接口本来就是"股"，三路并发/兜底上线后
    只要候选这次刷新恰好分到新浪那一路，算出来的VWAP就会系统性缩小100倍，
    被下面的合理性校验拦掉，悄悄退化成MA5，不是随机噪音、是必然发生。
    量为0/缺失（比如刚开盘还没有成交）时返回 None，调用方退回 MA5，不假装
    算出了一个VWAP。

    合理性校验（round3 review）：VWAP 物理上必须落在 [当日最低价, 当日最高价]
    区间内，超出说明成交量单位换算错了或接口字段本身异常——这种情况下返回一个
    错误的VWAP去参与结构判断比直接退回MA5更危险，所以校验不通过时也返回 None。
    """
    if amount is None or volume is None or volume <= 0 or amount <= 0:
        return None
    vwap = round(amount / volume, 4)
    if low is not None and high is not None and low > 0 and high > 0:
        if not (low * 0.999 <= vwap <= high * 1.001):
            return None
    return vwap


def _reset_intraday_session(cand: WeakToStrongCandidate, today: date_cls) -> None:
    """
    跨日重置：candidate 的生命周期字段（first_seen/last_seen/candidate_source等）
    跨日保留，但状态机相关的"今天走到哪一步了"必须清空——否则第二天用户在
    定时刷新跑之前打开页面，看到的会是昨天收盘时的状态（比如昨天BUYABLE，
    今天还没开盘就显示BUYABLE），这是真实存在过的 bug，不是"可以晚点修"。
    """
    if cand.signal_trade_date == today:
        return
    cand.signal_trade_date = today
    cand.current_state = sm.WATCH
    cand.structural_state = sm.STRUCT_WATCH
    cand.setup_substate = None
    cand.recovery_high = None
    cand.pullback_low = None
    cand.pullback_started = False
    cand.refresh_sample_count = 0
    cand.auction_gap = None
    cand.trigger_reasons = None
    cand.block_reasons = None


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

    for cand in candidates:
        _reset_intraday_session(cand, today)

    stock_by_id = {s.id: s for s in db.query(Stock).filter(Stock.id.in_([c.stock_id for c in candidates])).all()}

    # 按 Theme（primary_sector_id）分组，跑 Sector Gate / Leader Gate
    sector_ids = {s.primary_sector_id for s in stock_by_id.values() if s.primary_sector_id}
    sectors_by_id = {s.id: s for s in db.query(Sector).filter(Sector.id.in_(sector_ids)).all()}
    sector_score_cache: dict[int, dict] = {}
    for sid, sector in sectors_by_id.items():
        sector_score_cache[sid] = sector_gate.score_sector(db, sector, today)

    mainline_top_n = int(cfg.get_numeric(db, cfg.KEY_MAINLINE_SECTOR_TOP_N))
    mainline_sector_ids = sector_gate.select_mainline_sector_ids(sector_score_cache, top_n=mainline_top_n)

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
    space_min_room_pct = cfg.get_numeric(db, cfg.KEY_SPACE_MIN_ROOM_PCT)
    pullback_min_pct = cfg.get_numeric(db, cfg.KEY_PULLBACK_MIN_PCT)
    is_after_auction = (now.hour, now.minute) >= AUCTION_CUTOFF_HOUR_MINUTE
    formula_version = cfg.get_string(db, cfg.KEY_FORMULA_VERSION)

    # Market Gate 全局算一次（同一次刷新里所有候选共用同一个市场状态，不是逐股算），
    # 复用 index_trend_service/MarketBreadthDaily/market_effect_service 已有的
    # 每日同步数据，不额外发起外部请求。
    gate = market_gate.get_market_gate(db)
    market_state = gate["market_state"]
    market_gate_blocked = cfg.get_market_gate_blocked(db)
    stats["market_state"] = market_state

    for cand in candidates:
        stock = stock_by_id.get(cand.stock_id)
        if stock is None:
            continue
        quote = quotes.get(stock.code)
        if quote is None:
            stats["quote_missing"] += 1
            cand.signal_enabled = sm.check_data_freshness(cand.last_refreshed_at, now)
            # 行情连续拉取失败超过新鲜度阈值时，展示态必须主动降级为BLOCK——
            # 之前这里只更新了signal_enabled、current_state原地不动，会出现界面
            # 一直显示几十分钟前的旧BUYABLE、却没有任何"数据已过期"提示的真实bug。
            # structural_state（结构事实层）依然完全不碰，跟"闸门临时不通过不清空
            # 结构进度"是同一条不变式——数据过期时我们没有新证据推翻旧结构，只是
            # 不能再信任它支撑一个交易决策。
            if not cand.signal_enabled and cand.current_state != sm.BLOCK:
                old_state = cand.current_state
                cand.current_state = sm.BLOCK
                cand.block_reasons = "数据过期，信号已禁用（距上次成功刷新超过新鲜度阈值）"
                stats["state_changed"] += 1
                db.add(WeakToStrongEvent(
                    timestamp=now, stock_code=stock.code, sector_id=stock.primary_sector_id,
                    sector_phase=cand.sector_category, sector_strength=cand.sector_strength_score,
                    sector_momentum=cand.sector_momentum_score,
                    leader_type=cand.leader_type, leader_rank=cand.leader_rank, leader_score=cand.leader_score,
                    setup_state=sm.BLOCK, structural_state=cand.structural_state,
                    recovery_high=cand.recovery_high, pullback_low=cand.pullback_low,
                    price=cand.price, prev_close=cand.prev_close,
                    old_state=old_state, new_state=sm.BLOCK,
                    trigger_reasons=None, block_reasons=cand.block_reasons,
                    data_freshness=None, formula_version=formula_version,
                ))
            continue

        sector_info = sector_score_cache.get(stock.primary_sector_id, {})
        leader_info = leader_score_cache.get(stock.code, {})
        leader_type = leader_info.get("leader_type", "undetermined")

        closes = _recent_closes(db, stock.id, today, limit=5)
        ma5 = compute_ma(closes, 5)
        vwap = _compute_vwap(quote.amount, quote.volume, low=quote.low, high=quote.high)
        limit_pct = get_limit_pct(stock.code, stock.is_st)
        limit_price = round(quote.prev_close * (1 + limit_pct / 100), 2) if quote.prev_close else None
        limit_room = (
            round((limit_price - quote.price) / quote.price * 100, 2)
            if (limit_price is not None and quote.price)
            else None
        )
        regulatory_risk = _resolve_regulatory_risk(db, stock.code, today)

        # 真实开盘Gap = (今开-昨收)/昨收——今开一旦开盘就固定，不会像"当前涨跌幅"
        # 那样全天漂移，09:26 定时刷新和用户任何时候手动刷新看到的都是同一个数。
        # 今开缺失时（接口偶发字段缺失）退回当前涨跌幅近似。
        # 合理性校验（round3 review）：开盘Gap物理上不可能超过该股当日涨跌停幅度
        # （留10%容差应对四舍五入），超出说明 open/prev_close 字段本身异常，
        # 宁可置 None（结构判断退化为不依赖竞价Gap的价格判断）也不能带着一个
        # 不可能的Gap值参与状态机。
        if quote.open is not None and quote.open > 0 and quote.prev_close and quote.prev_close > 0:
            auction_gap = round((quote.open - quote.prev_close) / quote.prev_close * 100, 2)
        else:
            auction_gap = quote.pct_change
        if auction_gap is not None and abs(auction_gap) > limit_pct * 1.1:
            auction_gap = None

        stops = risk.compute_stops(
            price=quote.price, ma5=ma5, pullback_low=cand.pullback_low, limit_down_pct=limit_pct,
        )
        stress_rr = risk.compute_stress_rr(
            price=quote.price, stress_stop=stops["stress_stop"], limit_room=limit_room,
        )

        result = sm.compute_next_state(
            structural_state=cand.structural_state,
            signal_enabled=True,
            market_state=market_state,
            market_gate_blocked=market_gate_blocked,
            sector_category=sector_info.get("sector_category", "DEAD"),
            sector_gate_allowed=sector_gate_allowed,
            leader_type=leader_type,
            regulatory_risk=regulatory_risk,
            regulatory_risk_cap=regulatory_risk_cap,
            is_observation_expired=False,
            price=quote.price,
            prev_close=quote.prev_close,
            vwap=vwap,
            ma5=ma5,
            recovery_high=cand.recovery_high,
            pullback_low=cand.pullback_low,
            pullback_started=cand.pullback_started,
            pullback_min_pct=pullback_min_pct,
            auction_gap=auction_gap,
            auction_gap_min=auction_gap_min,
            is_after_auction=is_after_auction,
            limit_room=limit_room,
            space_min_room_pct=space_min_room_pct,
            is_mainline_sector=stock.primary_sector_id in mainline_sector_ids,
        )
        new_state = result["display_state"]

        old_state = cand.current_state
        cand.sector_id = stock.primary_sector_id
        cand.sector_name = stock.primary_sector_name
        cand.sector_category = sector_info.get("sector_category")
        cand.sector_strength_score = sector_info.get("sector_strength_score")
        cand.sector_momentum_score = sector_info.get("sector_momentum_score")
        cand.sector_divergence_health = sector_info.get("sector_divergence_health")
        cand.is_mainline_sector = stock.primary_sector_id in mainline_sector_ids
        cand.leader_type = leader_type
        cand.leader_rank = leader_info.get("leader_rank")
        cand.leader_score = leader_info.get("core_leader_score")
        cand.current_state = new_state
        cand.structural_state = result["structural_state"]
        cand.recovery_high = result["recovery_high"]
        cand.pullback_low = result["pullback_low"]
        cand.pullback_started = result["pullback_started"]
        cand.price = quote.price
        cand.prev_close = quote.prev_close
        cand.ma5 = ma5
        cand.vwap = vwap
        cand.day_open = quote.open
        cand.day_high = quote.high
        cand.day_low = quote.low
        cand.day_amount = quote.amount
        cand.turnover_rate = quote.turnover_rate
        cand.auction_gap = auction_gap
        cand.limit_price = limit_price
        cand.limit_room = limit_room
        cand.technical_stop = stops["technical_stop"]
        cand.standard_stop = stops["standard_stop"]
        cand.stress_stop = stops["stress_stop"]
        cand.stress_rr = stress_rr
        cand.regulatory_risk_level = regulatory_risk
        cand.signal_enabled = True
        cand.data_freshness_seconds = 0.0
        cand.trigger_reasons = "；".join(result["trigger_reasons"]) if result["trigger_reasons"] else None
        cand.block_reasons = "；".join(result["block_reasons"]) if result["block_reasons"] else None
        cand.refresh_sample_count = (cand.refresh_sample_count or 0) + 1
        cand.last_refreshed_at = now
        cand.formula_version = formula_version

        effective_risk = (
            round((quote.price - stops["stress_stop"]) / quote.price * 100, 2)
            if (quote.price and stops["stress_stop"] is not None and quote.price > 0)
            else None
        )

        stats["refreshed"] += 1
        db.add(WeakToStrongSnapshot(
            trade_date=today, timestamp=now, stock_code=stock.code,
            price=quote.price, high=quote.high, low=quote.low,
            amount=quote.amount, volume=quote.volume, vwap=vwap,
            structural_state=cand.structural_state,
            recovery_high=cand.recovery_high, pullback_low=cand.pullback_low,
        ))
        if new_state != old_state:
            stats["state_changed"] += 1
            db.add(WeakToStrongEvent(
                timestamp=now, stock_code=stock.code, sector_id=stock.primary_sector_id,
                market_trend_score=gate["trend_score"], risk_appetite_score=gate["risk_score"],
                sector_phase=cand.sector_category, sector_strength=cand.sector_strength_score,
                sector_momentum=cand.sector_momentum_score,
                leader_type=leader_type, leader_rank=cand.leader_rank, leader_score=cand.leader_score,
                setup_state=new_state,
                structural_state=result["structural_state"],
                recovery_high=result["recovery_high"], pullback_low=result["pullback_low"],
                price=quote.price, prev_close=quote.prev_close, vwap=vwap,
                limit_price=limit_price, limit_room=limit_room,
                regulatory_risk=regulatory_risk,
                technical_stop=stops["technical_stop"], effective_risk=effective_risk, stress_rr=stress_rr,
                old_state=old_state, new_state=new_state,
                trigger_reasons=cand.trigger_reasons, block_reasons=cand.block_reasons,
                data_freshness=0.0, formula_version=formula_version,
            ))

    db.commit()
    stats["duration_ms"] = int((datetime.now() - started).total_seconds() * 1000)
    return stats
