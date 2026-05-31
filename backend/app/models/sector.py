from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base


class Sector(Base):
    __tablename__ = "sectors"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(20), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)

    # 板块基础信息（由 sync_boards 脚本填充，定期刷新）
    sector_type = Column(String(20), nullable=True)     # "concept" | "industry" | "region"
    stock_count = Column(Integer, default=0, nullable=False)          # 板块家数
    total_market_cap = Column(Float, default=0.0, nullable=False)     # 总市值（亿元）
    turnover_rate = Column(Float, default=0.0, nullable=False)        # 换手率 %
    amount = Column(Float, default=0.0, nullable=False)               # 成交额（亿元）
    pct_change_30d = Column(Float, default=0.0, nullable=False)       # 今日涨幅 % (legacy name, stores f3=today)
    pct_change_5d = Column(Float, default=0.0, nullable=False)        # 近5日涨幅 %
    pct_change_10d = Column(Float, default=0.0, nullable=False)       # 近10日涨幅 %
    pct_change_20d = Column(Float, default=0.0, nullable=False)       # 近20日涨幅 %
    pct_change_60d = Column(Float, default=0.0, nullable=False)       # 近60日涨幅 %
    is_watched = Column(Boolean, default=False, nullable=False)       # 是否为关注板块

    # Current lifecycle state
    phase = Column(Integer, default=0, nullable=False)  # 0-6 (Stealth → Dead Zone)
    strong_stock_count = Column(Integer, default=0, nullable=False)
    limit_up_count = Column(Integer, default=0, nullable=False)
    limit_down_count = Column(Integer, default=0, nullable=False)
    board_height = Column(Integer, default=0, nullable=False)      # highest current boards
    continuity_score = Column(Float, default=0.0, nullable=False)
    risk_score = Column(Float, default=0.0, nullable=False)
    emotion_score = Column(Float, default=0.0, nullable=False)

    leader_stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    stock_relations = relationship(
        "StockSectorRelation", back_populates="sector", cascade="all, delete-orphan"
    )
    daily_snapshots = relationship(
        "SectorDailySnapshot", back_populates="sector", cascade="all, delete-orphan",
        order_by="SectorDailySnapshot.date"
    )
    signals = relationship("Signal", back_populates="sector")
    leader_stock = relationship("Stock", foreign_keys=[leader_stock_id])


class StockSectorRelation(Base):
    __tablename__ = "stock_sector_relations"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True)
    sector_id = Column(Integer, ForeignKey("sectors.id"), nullable=False, index=True)
    is_leader = Column(Boolean, default=False, nullable=False)       # 龙头
    is_core = Column(Boolean, default=False, nullable=False)         # 核心
    is_compensation = Column(Boolean, default=False, nullable=False) # 补涨

    created_at = Column(DateTime, server_default=func.now())

    stock = relationship("Stock", back_populates="sector_relations")
    sector = relationship("Sector", back_populates="stock_relations")


class SectorDailySnapshot(Base):
    __tablename__ = "sector_daily_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    sector_id = Column(Integer, ForeignKey("sectors.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)

    phase = Column(Integer, default=0, nullable=False)
    strong_stock_count = Column(Integer, default=0, nullable=False)
    limit_up_count = Column(Integer, default=0, nullable=False)
    board_height = Column(Integer, default=0, nullable=False)
    continuity_score = Column(Float, default=0.0, nullable=False)
    risk_score = Column(Float, default=0.0, nullable=False)
    emotion_score = Column(Float, default=0.0, nullable=False)

    leader_stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=True)

    created_at = Column(DateTime, server_default=func.now())

    sector = relationship("Sector", back_populates="daily_snapshots")
    leader_stock = relationship("Stock", foreign_keys=[leader_stock_id])
