from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from ..database import Base


class Stock(Base):
    __tablename__ = "stocks"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(10), unique=True, index=True, nullable=False)
    name = Column(String(50), nullable=False)
    market = Column(String(10), nullable=False, default="SH")  # SH or SZ
    is_st = Column(Boolean, default=False, nullable=False)
    is_new_stock = Column(Boolean, default=False, nullable=False)
    ipo_date = Column(Date, nullable=True)
    in_strong_pool = Column(Boolean, default=False, nullable=False)

    # Current state — updated via nightly recalculation
    phase = Column(String(30), nullable=True)
    leader_score = Column(Float, default=0.0, nullable=False)
    risk_score = Column(Float, default=0.0, nullable=False)
    emotion_score = Column(Float, default=0.0, nullable=False)
    board_count_60d = Column(Integer, default=0, nullable=False)
    board_down_count_60d = Column(Integer, default=0, nullable=False)  # 近60日最高连跌停数
    limit_up_days_60d = Column(Integer, default=0, nullable=False)
    limit_up_days_20d = Column(Integer, default=0, nullable=False)
    limit_up_days_10d = Column(Integer, default=0, nullable=False)
    pct_change_60d = Column(Float, default=0.0, nullable=False)    # 近60日累计涨幅 %
    pct_change_20d = Column(Float, default=0.0, nullable=False)    # 近20日累计涨幅 %
    pct_change_10d = Column(Float, default=0.0, nullable=False)    # 近10日累计涨幅 %
    top_10_pct_change_20d = Column(Boolean, default=False, nullable=False)

    # 主板块（每日更新时计算：watched板块中股票最多的优先，相同则涨停数→情绪分）
    # 是所有展示模块的单一数据源，确保仪表盘/龙头/强股池/涨跌停池一致
    primary_sector_id = Column(Integer, ForeignKey("sectors.id", ondelete="SET NULL"), nullable=True, index=True)
    primary_sector_name = Column(String(100), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    daily_snapshots = relationship(
        "StockDailySnapshot", back_populates="stock", cascade="all, delete-orphan",
        order_by="StockDailySnapshot.date"
    )
    sector_relations = relationship(
        "StockSectorRelation", back_populates="stock", cascade="all, delete-orphan"
    )
    signals = relationship("Signal", back_populates="stock")


class StockDailySnapshot(Base):
    """
    每日计算结果快照。只存计算结果，不存原始 OHLCV 行情。
    原始行情从东方财富实时拉取，计算完成后丢弃。
    """
    __tablename__ = "stock_daily_snapshots"
    __table_args__ = (
        UniqueConstraint("stock_id", "date", name="uq_snapshot_stock_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)

    # 关键行情指标（从 K 线计算后保留，用于评分和展示）
    close_price = Column(Float, nullable=True)     # 当日收盘价（用于历史 KLine 重建，计算 MA60/MA30）
    pct_change = Column(Float, nullable=True)      # 当日涨跌幅 %
    turnover_rate = Column(Float, nullable=True)   # 换手率 %

    # 每日标志位（从 K 线判断后写入）
    is_limit_up = Column(Boolean, default=False, nullable=False)
    is_limit_down = Column(Boolean, default=False, nullable=False)
    is_broken_board = Column(Boolean, default=False, nullable=False)  # 炸板
    is_one_word_limit_up = Column(Boolean, default=False, nullable=False)    # 一字板涨停（全天未跌破涨停价）
    is_one_word_limit_down = Column(Boolean, default=False, nullable=False)  # 一字板跌停（全天未涨破跌停价）

    # 滚动窗口统计
    board_count = Column(Integer, default=0, nullable=False)        # 当前连续涨停数
    limit_down_count = Column(Integer, default=0, nullable=False)   # 当前连续跌停数
    board_count_60d = Column(Integer, default=0, nullable=False)    # 60日内最高连涨停板数
    board_down_count_60d = Column(Integer, default=0, nullable=False)  # 60日内最高连跌停数
    limit_up_days_60d = Column(Integer, default=0, nullable=False)  # 近60日涨停天数
    limit_up_days_20d = Column(Integer, default=0, nullable=False)  # 近20日涨停天数
    limit_up_days_10d = Column(Integer, default=0, nullable=False)  # 近10日涨停天数
    pct_change_60d = Column(Float, default=0.0, nullable=False)     # 近60日累计涨幅 %
    pct_change_20d = Column(Float, default=0.0, nullable=False)     # 近20日累计涨幅 %
    pct_change_10d = Column(Float, default=0.0, nullable=False)     # 近10日累计涨幅 %
    top_10_pct_change_20d = Column(Boolean, default=False, nullable=False)  # 20日涨幅进前10%

    # 阶段（与 Stock.phase 同步写入，用于历史查询）
    phase = Column(String(30), nullable=True)   # "normal" | "weakening" | "broken"

    # 计算评分
    leader_score = Column(Float, default=0.0, nullable=False)
    risk_score = Column(Float, default=0.0, nullable=False)
    emotion_score = Column(Float, default=0.0, nullable=False)
    is_weak_to_strong = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, server_default=func.now())

    stock = relationship("Stock", back_populates="daily_snapshots")
