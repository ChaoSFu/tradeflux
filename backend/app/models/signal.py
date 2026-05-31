from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base


class Signal(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=True, index=True)
    sector_id = Column(Integer, ForeignKey("sectors.id"), nullable=True, index=True)

    date = Column(Date, nullable=False, index=True)
    signal_type = Column(String(50), nullable=False)
    # signal_type values: weak_to_strong | broken_board_recovery | divergence_repair |
    #   rebound_acceleration | sector_repair | emotional_recovery | dragon_leader_change

    confidence_score = Column(Float, default=0.0, nullable=False)  # 0–100
    risk_level = Column(String(20), nullable=False, default="medium")  # low | medium | high
    explanation = Column(Text, nullable=True)
    # suggested_action: observe | watchlist | low_position_trial | hold | reduce | avoid
    suggested_action = Column(String(30), nullable=False, default="observe")

    is_active = Column(Boolean, default=True, nullable=False)
    is_triggered = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    stock = relationship("Stock", back_populates="signals")
    sector = relationship("Sector", back_populates="signals")
