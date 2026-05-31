from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional, List


class SignalBase(BaseModel):
    date: date
    signal_type: str
    confidence_score: float = 0.0
    risk_level: str = "medium"
    explanation: Optional[str] = None
    suggested_action: str = "observe"


class SignalCreate(SignalBase):
    stock_id: Optional[int] = None
    sector_id: Optional[int] = None


class SignalResponse(SignalBase):
    id: int
    stock_id: Optional[int] = None
    sector_id: Optional[int] = None
    stock_code: Optional[str] = None
    stock_name: Optional[str] = None
    sector_name: Optional[str] = None
    is_active: bool
    is_triggered: bool
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class SignalListResponse(BaseModel):
    items: List[SignalResponse]
    total: int
    page: int
    page_size: int
