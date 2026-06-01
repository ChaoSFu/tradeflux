from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional, List

PHASE_LABELS = {
    0: "Stealth",
    1: "Initiation",
    2: "Expansion",
    3: "Euphoria",
    4: "Divergence",
    5: "Decline",
    6: "Dead Zone",
}

PHASE_LABELS_ZH = {
    0: "隐匿期",
    1: "启动期",
    2: "扩张期",
    3: "高潮期",
    4: "分歧期",
    5: "衰退期",
    6: "死亡区",
}


class SectorBase(BaseModel):
    code: str
    name: str
    description: Optional[str] = None


class SectorCreate(SectorBase):
    pass


class SectorUpdate(BaseModel):
    name: Optional[str] = None
    phase: Optional[int] = None
    description: Optional[str] = None


class StockInSector(BaseModel):
    id: int
    code: str
    name: str
    is_leader: bool
    is_core: bool
    is_compensation: bool
    leader_score: float
    risk_score: float
    phase: Optional[str] = None

    model_config = {"from_attributes": True}


class SectorResponse(SectorBase):
    id: int
    phase: int
    phase_label: Optional[str] = None
    phase_label_zh: Optional[str] = None
    strong_stock_count: int
    limit_up_count: int
    limit_down_count: int = 0
    board_height: int
    continuity_score: float
    risk_score: float
    emotion_score: float
    # 板块基础指标
    sector_type: Optional[str] = None
    stock_count: int = 0
    pct_change_30d: float = 0.0   # 今日涨幅（legacy 字段名）
    pct_change_5d: float = 0.0
    pct_change_10d: float = 0.0
    pct_change_20d: float = 0.0
    pct_change_60d: float = 0.0
    amount: float = 0.0
    is_watched: bool = False
    leader_stock_id: Optional[int] = None
    leader_stock_name: Optional[str] = None
    leader_stock_code: Optional[str] = None
    # 跨板块排名（null = 未进前5，1 = 第一名）
    rank_5d: Optional[int] = None
    rank_10d: Optional[int] = None
    rank_20d: Optional[int] = None
    rank_60d: Optional[int] = None
    rank_lu: Optional[int] = None      # 涨停数排名
    rank_board: Optional[int] = None   # 连板高度排名
    rank_strong: Optional[int] = None  # 强势股数排名
    stocks: List[StockInSector] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class SectorDailySnapshotResponse(BaseModel):
    id: int
    sector_id: int
    date: date
    phase: int
    strong_stock_count: int
    limit_up_count: int
    board_height: int
    continuity_score: float
    risk_score: float
    emotion_score: float

    model_config = {"from_attributes": True}


class SectorListResponse(BaseModel):
    items: List[SectorResponse]
    total: int
