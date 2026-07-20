from sqlalchemy import Column, Integer, String, Float, Date, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from ..database import Base


class IndexDailySnapshot(Base):
    """
    指数日线（异常波动「偏离值」基准 + 大盘趋势页K线/均线数据源）。
    index_code 形如 000001/399001/399006/000688/899050。
    OHLC/量额由 daily_update 的「大盘趋势数据同步」步骤写入。
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
    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)   # 成交量（源口径，同指数内可比）
    amount = Column(Float, nullable=True)   # 成交额（元，仅东财源提供）

    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class MarketBreadthDaily(Base):
    """
    大盘市场宽度/资金每日快照（大盘趋势页「市场资金与盘面」数据源）。
    一天一行；不同来源字段分批 upsert：两融（余额/净买入/上证收盘）、
    成交额（沪深/沪深京）、涨跌统计（家数/涨跌停/自然涨跌停/分布桶）。
    """
    __tablename__ = "market_breadth_daily"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, unique=True, index=True)

    # 融资融券（东财 RPT_DMSK_WINDVANE_MARGIN）
    margin_balance = Column(Float, nullable=True)    # 两融余额（元）
    margin_net_buy = Column(Float, nullable=True)    # 融资净买入（元）
    szzs_close = Column(Float, nullable=True)        # 上证收盘（两融对照）

    # 成交额（东财 RPT_DMSK_WINDVANE_SUMTVALLIST）
    deal_amount = Column(Float, nullable=True)       # 沪深两市成交额（元）
    deal_amount_hsj = Column(Float, nullable=True)   # 含北交所

    # 涨跌统计（quotederivates updowndistribution 三市求和，收盘后口径）
    up_count = Column(Integer, nullable=True)
    down_count = Column(Integer, nullable=True)
    flat_count = Column(Integer, nullable=True)
    limit_up_count = Column(Integer, nullable=True)
    limit_down_count = Column(Integer, nullable=True)
    natural_limit_up = Column(Integer, nullable=True)
    natural_limit_down = Column(Integer, nullable=True)
    up_buckets = Column(JSONB, nullable=True)        # 不含涨停 10档 [0-1%,...,9-10%+]
    down_buckets = Column(JSONB, nullable=True)      # 不含跌停 10档

    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
