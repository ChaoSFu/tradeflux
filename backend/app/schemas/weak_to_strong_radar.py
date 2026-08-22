from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class CandidateResponse(BaseModel):
    stock_code: str
    stock_name: str
    first_seen_date: date
    last_seen_date: date
    consecutive_miss_days: int
    candidate_source: str
    is_active: bool

    sector_id: Optional[int] = None
    sector_name: Optional[str] = None
    sector_category: Optional[str] = None
    sector_strength_score: Optional[float] = None
    sector_momentum_score: Optional[float] = None

    leader_type: Optional[str] = None
    leader_rank: Optional[int] = None
    leader_score: Optional[float] = None

    current_state: str
    setup_substate: Optional[str] = None
    refresh_sample_count: int

    price: Optional[float] = None
    prev_close: Optional[float] = None
    ma5: Optional[float] = None
    day_open: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    day_amount: Optional[float] = None
    turnover_rate: Optional[float] = None

    auction_gap: Optional[float] = None
    limit_price: Optional[float] = None
    limit_room: Optional[float] = None

    regulatory_risk_level: Optional[str] = None
    signal_enabled: bool

    trigger_reasons: Optional[str] = None
    block_reasons: Optional[str] = None

    last_refreshed_at: Optional[datetime] = None
    formula_version: str

    model_config = {"from_attributes": True}


class MarketGateResponse(BaseModel):
    trend_score: Optional[float] = None
    risk_score: Optional[float] = None
    market_state: str
    index_scores: dict[str, float]
    margin_balance_chg_pct: Optional[float] = None
    as_of_date: Optional[str] = None


class ChecklistGroup(BaseModel):
    """
    单组闸门检查结果。status: "pass" | "fail" | "phase2"（Phase 1 未实现的组，
    比如 SPACE/CHIPS/RISK，一律显示 phase2，绝不伪造 pass/fail）。
    """
    group: str
    status: str
    detail: str


class CandidateDetailResponse(CandidateResponse):
    checklist: list[ChecklistGroup]


class RefreshResultResponse(BaseModel):
    refreshed: int
    state_changed: int
    quote_missing: int
    duration_ms: int
    triggered_at: datetime


class RefreshStatusResponse(BaseModel):
    running: bool
    last_result: Optional[RefreshResultResponse] = None
    last_error: Optional[str] = None


class EventResponse(BaseModel):
    id: int
    timestamp: datetime
    stock_code: str
    old_state: Optional[str] = None
    new_state: str
    trigger_reasons: Optional[str] = None
    block_reasons: Optional[str] = None
    sector_phase: Optional[str] = None
    leader_type: Optional[str] = None
    price: Optional[float] = None
    formula_version: str

    model_config = {"from_attributes": True}


class W2SConfigResponse(BaseModel):
    prompt1: str
    prompt2: str
    is_prompt1_custom: bool
    is_prompt2_custom: bool
    default_prompt1: str
    default_prompt2: str
    w2s_min_yesterday_amount: float
    w2s_leader_gap_threshold: float
    w2s_observation_window_days: float
    w2s_divergence_health_threshold: float
    w2s_auction_gap_min: float
    w2s_sector_gate_allowed: str
    w2s_regulatory_risk_cap: str
    w2s_formula_version: str


class W2SConfigUpdateRequest(BaseModel):
    key: str
    value: Optional[str] = None
