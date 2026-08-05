from pydantic import BaseModel
from typing import List, Literal, Optional


class TurnoverStock(BaseModel):
    code: str
    name: str
    rank: int
    amount: float             # 成交额（元）
    pct_change: float         # 涨跌幅 %
    turnover_rate: Optional[float] = None
    sector_name: Optional[str] = None
    market: Optional[str] = None
    is_new: bool              # 相比前一交易日名单是否新进


class TurnoverSectorGroup(BaseModel):
    name: str
    count: int
    avg_pct_change: float
    up_count: int
    down_count: int
    flat_count: int
    total_amount: float
    new_count: int
    bias: Literal["bullish", "bearish", "mixed"]


class TurnoverOverviewResponse(BaseModel):
    date: Optional[str] = None
    stocks: List[TurnoverStock] = []
    sector_groups: List[TurnoverSectorGroup] = []
    total_amount: float = 0.0
    new_count: int = 0
    overall_avg_pct: float = 0.0
    errors: List[str] = []
