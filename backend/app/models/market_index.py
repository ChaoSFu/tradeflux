from sqlalchemy import Column, Integer, String, Float, Date, DateTime, UniqueConstraint
from sqlalchemy.sql import func

from ..database import Base


class IndexDailySnapshot(Base):
    """
    指数日线（用于异常波动「偏离值」计算的基准）。
    index_code 形如 000001/399001/399006/000688。
    """
    __tablename__ = "index_daily_snapshots"
    __table_args__ = (
        UniqueConstraint("index_code", "date", name="uq_index_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    index_code = Column(String(16), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    close = Column(Float, nullable=True)
    pct_change = Column(Float, nullable=True)  # 当日涨跌幅 %

    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
