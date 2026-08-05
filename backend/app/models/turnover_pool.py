from sqlalchemy import Column, Integer, String, Float, Date, DateTime, UniqueConstraint
from sqlalchemy.sql import func

from ..database import Base


class TurnoverPoolDaily(Base):
    """
    每日成交额前列股票快照（成交额概览页面数据源）。
    一天一批行，来自东财智能选股 prompt「成交额排序前N」；用于按板块聚合
    赚钱效应、以及跟前一天名单对比标出「新进」股票。
    """
    __tablename__ = "turnover_pool_daily"
    __table_args__ = (
        UniqueConstraint("date", "stock_code", name="uq_turnover_pool_date_code"),
    )

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(50), nullable=False)
    rank = Column(Integer, nullable=False)          # 当日按成交额排名 1..N
    amount = Column(Float, nullable=False)          # 成交额（元）
    pct_change = Column(Float, nullable=False)      # 涨跌幅 %
    turnover_rate = Column(Float, nullable=True)    # 换手率 %
    sector_name = Column(String(100), nullable=True)  # 归属板块（F10 主板块）
    market = Column(String(5), nullable=True)       # SH/SZ/BJ

    created_at = Column(DateTime, server_default=func.now())
