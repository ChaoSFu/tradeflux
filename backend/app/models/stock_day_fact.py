"""
个股某个交易日的事实（2026-11-25）：用到哪几只就按代码问选股 prompt，存一行，同一天不再问。

替代原来「每天翻一遍全市场」的用法：人气核心池的成员字段、每日复盘看的观察股 / 关联股，
都只需要几十只。只能存**当天**——选股接口给的是此刻的数据，历史日期补不回来（缺了就是缺了）。
数据日期以接口列名里的日期为准（OPEN_PRICE<140>{2026-11-25}），对不上不存。

单位跟 large_cap_market_daily 一样：金额、市值亿元，涨跌幅 %。所有数值列可空：None = 接口没给，不是 0。
"""
from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String, UniqueConstraint

from ..database import Base


class StockDayFact(Base):
    __tablename__ = "stock_day_facts"
    __table_args__ = (UniqueConstraint("trade_date", "stock_code", name="uq_stock_day_fact_date_code"),)

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(10), nullable=False, index=True)
    stock_name = Column(String(50), nullable=True)
    market = Column(String(4), nullable=True)
    is_st = Column(Boolean, nullable=True)
    price = Column(Float, nullable=True)
    pct_change = Column(Float, nullable=True)
    pct_change_5d = Column(Float, nullable=True)        # 区间涨跌幅（前复权，今天收盘 ÷ 5 个交易日前收盘）
    pct_change_10d = Column(Float, nullable=True)
    pct_change_20d = Column(Float, nullable=True)
    pct_change_60d = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)               # 亿元
    turnover_rate = Column(Float, nullable=True)
    volume_ratio = Column(Float, nullable=True)
    total_market_cap = Column(Float, nullable=True)     # 亿元
    settled = Column(Boolean, nullable=False, default=False)   # 取的时候收盘了没有（盘中取的收盘后再取一次）
    fetched_at = Column(DateTime, nullable=True)
