"""交易复盘日志请求/响应类型"""
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel

ACTIONS = {"买入", "加仓", "减仓", "卖出", "清仓"}
EXIT_ACTIONS = {"减仓", "卖出", "清仓"}
EMOTION_TAGS = {"计划内", "抄底做T", "逆势加仓", "回本补救", "追高", "其他"}
EXIT_REASONS = {"止损", "恐慌", "反弹跑", "目标达成", "其他"}


class TradeJournalCreate(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    action: str
    trade_time: datetime
    price: float
    position_pct: Optional[float] = None
    reason: Optional[str] = None
    planned_stop: Optional[float] = None
    target: Optional[float] = None
    emotion_tag: Optional[str] = None
    note: Optional[str] = None
    exit_reason: Optional[str] = None
    realized_pnl: Optional[float] = None
    pnl_pct: Optional[float] = None


class TradeJournalUpdate(BaseModel):
    stock_name: Optional[str] = None
    action: Optional[str] = None
    trade_time: Optional[datetime] = None
    price: Optional[float] = None
    position_pct: Optional[float] = None
    reason: Optional[str] = None
    planned_stop: Optional[float] = None
    target: Optional[float] = None
    emotion_tag: Optional[str] = None
    note: Optional[str] = None
    exit_reason: Optional[str] = None
    realized_pnl: Optional[float] = None
    pnl_pct: Optional[float] = None


class TradeJournalResponse(BaseModel):
    id: int
    stock_code: str
    stock_name: Optional[str] = None
    action: str
    trade_time: datetime
    price: float
    position_pct: Optional[float] = None
    reason: Optional[str] = None
    planned_stop: Optional[float] = None
    target: Optional[float] = None
    emotion_tag: Optional[str] = None
    note: Optional[str] = None
    exit_reason: Optional[str] = None
    realized_pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    mkt_temperature: Optional[float] = None
    mkt_phase: Optional[str] = None
    mkt_suggested_position: Optional[float] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TradeJournalListResponse(BaseModel):
    items: List[TradeJournalResponse]
    total: int
    # 汇总（当前筛选下）：便于列表页顶部展示
    realized_pnl_sum: float = 0.0
    win_count: int = 0
    loss_count: int = 0
