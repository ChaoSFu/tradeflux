from pydantic import BaseModel, Field
from datetime import date, datetime
from typing import Optional, List


class StockBase(BaseModel):
    code: str
    name: str
    market: str = "SH"
    is_st: bool = False
    is_new_stock: bool = False


class StockCreate(StockBase):
    ipo_date: Optional[date] = None


class StockUpdate(BaseModel):
    name: Optional[str] = None
    in_strong_pool: Optional[bool] = None
    phase: Optional[str] = None
    leader_score: Optional[float] = None
    risk_score: Optional[float] = None
    emotion_score: Optional[float] = None


class StockResponse(StockBase):
    id: int
    ipo_date: Optional[date] = None
    in_strong_pool: bool
    phase: Optional[str] = None
    leader_score: float
    risk_score: float
    emotion_score: float
    board_count_60d: int
    board_down_count_60d: int = 0
    limit_up_days_60d: int
    limit_up_days_20d: int = 0
    limit_up_days_10d: int
    pct_change_60d: float = 0.0
    pct_change_20d: float = 0.0
    pct_change_10d: float = 0.0
    top_10_pct_change_20d: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Populated from relationships in service layer
    primary_sector: Optional[str] = None
    sector_id: Optional[int] = None
    sector_phase: Optional[int] = None
    is_leader: Optional[bool] = None
    # All filtered sector names for this stock (multi-sector display)
    sectors: List[str] = Field(default_factory=list)
    # Whether today's latest snapshot shows a limit-up / limit-down（权威标志，与板块聚合口径一致）
    today_is_limit_up: bool = False
    today_is_limit_down: bool = False
    # From latest snapshot — useful for intraday display
    today_pct_change: Optional[float] = None        # 今日涨跌幅 %
    today_board_count: Optional[int] = None         # 当前连续涨停数（截至今日快照）
    today_limit_down_count: Optional[int] = None    # 当前连续跌停数（截至今日快照）
    # 上一交易日是否涨/跌停（一致性强、需谨慎；仅对当前在交易的股票有效）
    yesterday_is_limit_up: bool = False
    yesterday_is_limit_down: bool = False
    # 距「涨幅严重异动」的近似上涨空间 %（还需累计涨多少触发；已触发/数据不足为 None）
    severe_up_room: Optional[float] = None

    model_config = {"from_attributes": True}


class StockDailySnapshotResponse(BaseModel):
    id: int
    stock_id: int
    date: date
    open_price: Optional[float] = None
    close_price: Optional[float] = None
    high_price: Optional[float] = None
    low_price: Optional[float] = None
    volume: Optional[float] = None
    turnover_rate: Optional[float] = None
    pct_change: Optional[float] = None
    is_limit_up: bool
    is_limit_down: bool
    is_broken_board: bool
    board_count: int
    board_count_60d: int
    limit_up_days_60d: int
    limit_up_days_20d: int = 0
    limit_up_days_10d: int
    top_10_pct_change_20d: bool
    phase: Optional[str] = None
    leader_score: float
    risk_score: float
    emotion_score: float
    is_weak_to_strong: bool

    model_config = {"from_attributes": True}


class StockListResponse(BaseModel):
    items: List[StockResponse]
    total: int
    page: int
    page_size: int


class LimitMoveTrendPoint(BaseModel):
    date: str
    limit_up_count: int
    limit_down_count: int
