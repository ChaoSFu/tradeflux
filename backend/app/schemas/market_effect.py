from pydantic import BaseModel
from datetime import date
from typing import Any, List, Optional


class CohortOutcome(BaseModel):
    cohort_type: str
    label: str
    member_count: int
    valid_count: int
    median_pct_change: Optional[float] = None
    red_ratio: Optional[float] = None
    large_loss_ratio: Optional[float] = None
    advance_ratio: Optional[float] = None
    broken_ratio: Optional[float] = None


class EvidenceItem(BaseModel):
    metric: str
    raw_value: Any
    sample_size: int
    direction: str


class MarketEffectDailyResponse(BaseModel):
    trade_date: date
    profit_strength: float
    loss_strength: float
    quadrant: str
    lifecycle_state: str
    breadth_source: str
    coverage_ratio: float
    cohorts: dict[str, CohortOutcome]
    evidence: List[EvidenceItem]
    summary: str
    formula_version: str


class CohortMember(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    board_count_before: Optional[int] = None
    outcome_pct_change: Optional[float] = None
    outcome_board_count: Optional[int] = None
    has_outcome: bool


class MarketEffectHistoryPoint(BaseModel):
    trade_date: date
    profit_strength: float
    loss_strength: float
    quadrant: str
    lifecycle_state: str
    breadth_source: str
