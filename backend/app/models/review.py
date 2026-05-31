from sqlalchemy import Column, Integer, String, Float, Date, DateTime, Text
from sqlalchemy.sql import func
from sqlalchemy.types import JSON
from ..database import Base


class DailyReview(Base):
    __tablename__ = "daily_reviews"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, unique=True, index=True)

    # Market state scalars
    market_phase = Column(String(50), nullable=True)
    profit_effect_score = Column(Float, default=0.0, nullable=False)    # 赚钱效应 0–100（合成评分，保留兼容）
    loss_effect_score = Column(Float, default=0.0, nullable=False)      # 亏钱效应 0–100（合成评分，保留兼容）
    strong_pool_avg_pct = Column(Float, nullable=True)                  # 强势股池当日均涨幅 % （真实值）
    emotion_cycle = Column(String(50), nullable=True)
    emotional_temperature = Column(Float, default=50.0, nullable=False) # 0–100
    suggested_position_level = Column(Float, default=30.0, nullable=False)  # %

    # 强势股池当日涨跌停统计（真实值，用于赚钱效应历史回顾）
    overall_up_count = Column(Integer, nullable=True)
    overall_down_count = Column(Integer, nullable=True)
    overall_limit_up_count = Column(Integer, nullable=True)
    overall_limit_down_count = Column(Integer, nullable=True)

    # JSON arrays (sector names / stock codes)
    strong_sectors = Column(JSON, nullable=True)
    dangerous_sectors = Column(JSON, nullable=True)
    active_sectors = Column(JSON, nullable=True)       # 活跃板块快照（phase >= 2）
    dragon_changes = Column(JSON, nullable=True)       # 龙头列表快照
    tomorrow_watchlist = Column(JSON, nullable=True)

    # 赚钱效应分组与板块快照（用于历史查询）
    profit_effect_groups = Column(JSON, nullable=True)   # List[{key, label, stock_count, avg_pct, up, down}]
    profit_effect_sectors = Column(JSON, nullable=True)  # List[{sector_name, stock_count, avg_pct, up, down}]

    # Narrative content (may be AI-generated in the future)
    market_summary = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
