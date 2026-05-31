"""
Weak-to-Strong Engine — detects stocks transitioning from weakness to strength.

Detection patterns（按信号强度排序）:
  weak_to_strong        : 昨日走弱/破位，今日涨停（最强信号）
  broken_board_recovery : 近5日内炸板，随后今日涨停复板
  rebound_acceleration  : 连续下跌≥3日后，今日涨幅>3%且强于前日

Each signal carries:
  - confidence_score  (0–100)
  - risk_level        (low | medium | high)
  - explanation       (plain text)
  - suggested_action  (observe | watchlist | low_position_trial | hold | reduce | avoid)

IMPORTANT: suggested_action intentionally never generates "must buy" language.
"""
from sqlalchemy.orm import Session
from datetime import date
from typing import List, Optional
from ..models.stock import Stock, StockDailySnapshot
from ..models.sector import Sector, StockSectorRelation  # StockSectorRelation used in get_signals
from ..models.signal import Signal
from ..schemas.market_state import WeakToStrongCandidate


_PHASE_CN = {
    "broken": "破位",
    "weakening": "走弱",
    "normal": "正常",
}


def _last_n_snapshots(stock: Stock, n: int) -> list[StockDailySnapshot]:
    snaps = sorted(stock.daily_snapshots, key=lambda s: s.date)
    return snaps[-n:] if len(snaps) >= n else snaps


def _detect_weak_to_strong(stock: Stock) -> Optional[dict]:
    """
    昨日走弱/破位，今日涨停 — 最强弱转强信号。
    依赖 StockDailySnapshot.phase（daily_update 已落库）。
    """
    snaps = _last_n_snapshots(stock, 3)
    if len(snaps) < 2:
        return None
    yesterday = snaps[-2]
    today = snaps[-1]
    if not today.is_limit_up:
        return None
    prev_phase = yesterday.phase
    if prev_phase not in ("weakening", "broken"):
        return None

    conf = 88.0 if prev_phase == "broken" else 78.0
    phase_label = _PHASE_CN.get(prev_phase, prev_phase)
    return {
        "signal_type": "weak_to_strong",
        "confidence_score": conf,
        "risk_level": "medium",
        "explanation": (
            f"{stock.name} 昨日处于{phase_label}状态，今日强势涨停，"
            f"多方力量快速修复，弱转强信号明确。"
        ),
        "suggested_action": "low_position_trial",
    }


def _detect_broken_board_recovery(stock: Stock) -> Optional[dict]:
    """
    炸板后复板：近5日内有炸板，今日重新涨停。
    放宽为7日窗口，核心要求今日必须涨停（而非仅"涨幅>5%"）。
    """
    snaps = _last_n_snapshots(stock, 7)
    if len(snaps) < 3:
        return None
    today = snaps[-1]
    if not today.is_limit_up:
        return None
    prev = snaps[:-1]
    # 找到最近一次炸板
    broken_idx = next(
        (len(prev) - 1 - i for i, s in enumerate(reversed(prev)) if s.is_broken_board),
        None,
    )
    if broken_idx is None:
        return None
    days_since_broken = len(prev) - broken_idx
    conf = min(85.0, 55.0 + (7 - days_since_broken) * 5)
    return {
        "signal_type": "broken_board_recovery",
        "confidence_score": round(conf, 1),
        "risk_level": "medium",
        "explanation": (
            f"{stock.name} 炸板后第 {days_since_broken} 日复板涨停，"
            f"强势修复信号，注意板块联动情况。"
        ),
        "suggested_action": "watchlist",
    }


def _detect_rebound_acceleration(stock: Stock) -> Optional[dict]:
    """
    连续下跌后反弹加速：连跌≥3日 + 今日涨幅>3% 且强于前日。
    扩展至10日窗口，提升触发概率。
    """
    snaps = _last_n_snapshots(stock, 10)
    if len(snaps) < 5:
        return None
    today = snaps[-1]
    pct_today = today.pct_change or 0.0
    pct_prev = (snaps[-2].pct_change or 0.0) if len(snaps) >= 2 else 0.0

    # 从昨日往前数连续下跌天数
    consec_down = 0
    for s in reversed(snaps[:-1]):
        if (s.pct_change or 0) < 0:
            consec_down += 1
        else:
            break

    if consec_down < 3:
        return None
    if pct_today <= 3.0 or pct_today <= pct_prev:
        return None

    conf = min(75.0, 45.0 + consec_down * 5 + pct_today * 2)
    return {
        "signal_type": "rebound_acceleration",
        "confidence_score": round(conf, 1),
        "risk_level": "low",
        "explanation": (
            f"{stock.name} 经历 {consec_down} 日连续下跌后今日反弹 {pct_today:.1f}%"
            f"（强于前日 {pct_prev:.1f}%），关注量能是否持续放大。"
        ),
        "suggested_action": "low_position_trial",
    }


def detect_weak_to_strong_candidates(
    db: Session, as_of: date | None = None
) -> List[WeakToStrongCandidate]:
    if as_of is None:
        as_of = date.today()

    strong_stocks = db.query(Stock).filter(Stock.in_strong_pool == True).all()  # noqa: E712
    if not strong_stocks:
        return []

    stock_ids = [s.id for s in strong_stocks]

    candidates: list[WeakToStrongCandidate] = []
    for stock in strong_stocks:
        # 主板块：直接读落库值（与仪表盘/龙头等模块一致）
        sector_name = stock.primary_sector_name or "Unknown"

        # 按优先级检测，取最强信号
        detected = (
            _detect_weak_to_strong(stock)
            or _detect_broken_board_recovery(stock)
            or _detect_rebound_acceleration(stock)
        )
        if detected:
            candidates.append(
                WeakToStrongCandidate(
                    stock_code=stock.code,
                    stock_name=stock.name,
                    sector_name=sector_name,
                    confidence_score=detected["confidence_score"],
                    risk_level=detected["risk_level"],
                    signal_type=detected["signal_type"],
                    suggested_action=detected["suggested_action"],
                    explanation=detected["explanation"],
                )
            )

    candidates.sort(key=lambda c: c.confidence_score, reverse=True)
    return candidates[:10]


def get_signals(
    db: Session,
    page: int = 1,
    page_size: int = 20,
    signal_type: str | None = None,
    risk_level: str | None = None,
    stock_id: int | None = None,
    sector_id: int | None = None,
):
    from ..schemas.signal import SignalResponse, SignalListResponse

    q = db.query(Signal).filter(Signal.is_active == True)  # noqa: E712
    if signal_type:
        q = q.filter(Signal.signal_type == signal_type)
    if risk_level:
        q = q.filter(Signal.risk_level == risk_level)
    if stock_id:
        q = q.filter(Signal.stock_id == stock_id)
    if sector_id:
        q = q.filter(Signal.sector_id == sector_id)

    q = q.order_by(Signal.confidence_score.desc(), Signal.date.desc())
    total = q.count()
    signals = q.offset((page - 1) * page_size).limit(page_size).all()

    items = []
    for sig in signals:
        stock = db.query(Stock).filter(Stock.id == sig.stock_id).first() if sig.stock_id else None
        sector = db.query(Sector).filter(Sector.id == sig.sector_id).first() if sig.sector_id else None
        resp = SignalResponse.model_validate(sig)
        resp.stock_code = stock.code if stock else None
        resp.stock_name = stock.name if stock else None
        resp.sector_name = sector.name if sector else None
        items.append(resp)

    return SignalListResponse(items=items, total=total, page=page, page_size=page_size)
