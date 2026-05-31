from pydantic import BaseModel
from datetime import date
from typing import List, Optional


# ─── Profit Effect ────────────────────────────────────────────────────────────

class ProfitEffectGroup(BaseModel):
    key: str           # "limit_up" | "oscillation" | "weakening" | "broken"
    label: str
    stock_count: int
    avg_pct: float
    up_count: int
    down_count: int
    flat_count: int = 0   # optional for backward-compat with stored JSON rows


class SectorProfitEffect(BaseModel):
    sector_code: str
    sector_name: str
    stock_count: int
    up_count: int
    down_count: int
    avg_pct: float


class ProfitEffectResponse(BaseModel):
    date: date
    has_data: bool
    overall_avg_pct: float
    overall_up_count: int
    overall_down_count: int
    overall_flat_count: int
    overall_limit_up_count: int
    overall_limit_down_count: int
    groups: List[ProfitEffectGroup]
    sectors: List[SectorProfitEffect]


class DragonLeader(BaseModel):
    stock_code: str
    stock_name: str
    sector_name: str
    leader_type: str   # overall | emotion | trend | compensation | mid_cap
    board_height: int
    leader_score: float
    risk_score: float


class WeakToStrongCandidate(BaseModel):
    stock_code: str
    stock_name: str
    sector_name: str
    confidence_score: float
    risk_level: str
    signal_type: str
    suggested_action: str
    explanation: str


class ActiveSector(BaseModel):
    sector_code: str
    sector_name: str
    phase: int
    phase_label: str
    emotion_score: float
    strong_stock_count: int
    board_height: int


class MarketStateResponse(BaseModel):
    date: date
    market_phase: str
    profit_effect_score: float          # 赚钱效应
    loss_effect_score: float            # 亏钱效应
    emotion_cycle: str
    emotional_temperature: float         # 0–100
    suggested_position_level: float      # %
    active_sectors: List[ActiveSector]
    dangerous_sectors: List[str]
    strong_sectors: List[str]
    dragon_leaders: List[DragonLeader]
    weak_to_strong_candidates: List[WeakToStrongCandidate]


class MarketHistoryPoint(BaseModel):
    date: date
    profit_effect_score: float
    loss_effect_score: float
    strong_pool_avg_pct: Optional[float] = None   # 强势股真实均涨幅 %
    profit_effect_groups: Optional[List[ProfitEffectGroup]] = None  # 分组均涨幅
    emotional_temperature: float
    suggested_position_level: float
    market_phase: Optional[str] = None
