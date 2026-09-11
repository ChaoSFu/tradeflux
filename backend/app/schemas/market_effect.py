from pydantic import BaseModel
from datetime import date
from typing import Any, List, Optional


class CohortOutcome(BaseModel):
    cohort_type: str
    label: str
    member_count: int
    valid_count: int
    # 有效样本里用当日行情补的只数（不在候选池、当天没快照）。旧缓存行没有这个键 → 0
    quote_count: int = 0
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
    # "snapshot" = 当日快照；"quote" = 不在候选池、用当日行情补的；None = 没有结果
    outcome_source: Optional[str] = None


class MarketEffectHistoryPoint(BaseModel):
    trade_date: date
    profit_strength: float
    loss_strength: float
    quadrant: str
    lifecycle_state: str
    breadth_source: str


class CohortSeriesValue(BaseModel):
    """曲线上一天一组的取值。**median 为 None 就是断点**，前端不许填 0。"""
    median_pct_change: Optional[float] = None
    member_count: int
    valid_count: int


class CohortSeriesPoint(BaseModel):
    trade_date: date
    # True=收盘终值 / False=还有盘中行（这一点是盘中价算的）/ None=不知道
    is_settled: Optional[bool] = None
    values: dict[str, CohortSeriesValue]


class CohortSeriesLegend(BaseModel):
    cohort_type: str
    label: str


class CohortSeriesResponse(BaseModel):
    as_of: Optional[date] = None
    formula_version: str
    cohorts: List[CohortSeriesLegend]
    points: List[CohortSeriesPoint]
    notes: List[str] = []
