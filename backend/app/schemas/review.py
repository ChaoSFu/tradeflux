from pydantic import BaseModel
from datetime import date, datetime
from typing import Optional, List, Any


class DailyReviewCreate(BaseModel):
    date: date
    market_phase: Optional[str] = None
    profit_effect_score: float = 0.0
    loss_effect_score: float = 0.0
    emotion_cycle: Optional[str] = None
    emotional_temperature: float = 50.0
    suggested_position_level: float = 30.0
    strong_sectors: Optional[List[str]] = None
    dangerous_sectors: Optional[List[str]] = None
    active_sectors: Optional[List[str]] = None
    dragon_changes: Optional[List[Any]] = None
    tomorrow_watchlist: Optional[List[str]] = None
    market_summary: Optional[str] = None


class DailyReviewResponse(DailyReviewCreate):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DailyReviewListResponse(BaseModel):
    items: List[DailyReviewResponse]
    total: int
