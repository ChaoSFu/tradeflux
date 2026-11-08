"""
大市值概览的三张表：全市场每日轻量快照、每次同步的批次记录、个股 F10 板块缓存。

## 为什么不用 StockDailySnapshot

那张表只覆盖进过候选池的股票，不是全市场。跨入 / 跨出要证明「昨天 970 亿、今天
1030 亿」，门槛外那一侧的历史值必须也在——所以每天存全市场，不只存门槛上的。

## 单位

市值、成交额一律**亿元**（跟 Sector.total_market_cap 同一单位），涨跌幅、换手率是 %。
东财 clist 给的是元，腾讯行情给的是亿元 / 万元，换算在解析那一层做完，这里只有一种单位。
所有数值列都可空：None = 这个源那天没给，**不是 0**。
"""
from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.sql import func

from ..database import Base


class LargeCapMarketDaily(Base):
    """一个交易日一只股票一行（收盘后正式快照）。只有完整批次才会写进来。"""
    __tablename__ = "large_cap_market_daily"
    __table_args__ = (
        UniqueConstraint("trade_date", "stock_code", name="uq_large_cap_daily_date_code"),
    )

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(10), nullable=False, index=True)
    stock_name = Column(String(50), nullable=True)
    market = Column(String(4), nullable=True)            # SH / SZ
    total_market_cap = Column(Float, nullable=True)      # 总市值（亿元）
    float_market_cap = Column(Float, nullable=True)      # 流通市值（亿元）
    price = Column(Float, nullable=True)
    pct_change = Column(Float, nullable=True)            # 当日涨跌幅 %
    pct_change_5d = Column(Float, nullable=True)         # 近 5 个交易日涨跌幅 %（前复权）
    pct_change_10d = Column(Float, nullable=True)
    pct_change_20d = Column(Float, nullable=True)
    pct_change_60d = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)                # 成交额（亿元）
    turnover_rate = Column(Float, nullable=True)         # 换手率 %
    volume_ratio = Column(Float, nullable=True)          # 量比
    industry = Column(String(50), nullable=True)         # 东财二级行业（clist f100），当天的分类
    is_st = Column(Boolean, nullable=True)
    listing_date = Column(Date, nullable=True)           # 上市日（clist f26）
    source = Column(String(20), nullable=False)          # eastmoney_clist | tencent_quote
    batch_id = Column(Integer, nullable=True)
    observed_at = Column(DateTime, nullable=True)


class LargeCapSyncBatch(Base):
    """
    每次同步一行，成功失败都记。「拿到 800 只以上」不算完整：完整 = 每个市场都翻完了页、
    去重后条数等于源头报的总数、有效市值占比正常。
    """
    __tablename__ = "large_cap_sync_batch"

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=True, index=True)  # 数据自己带的交易日；整批失败时可能拿不到
    source = Column(String(20), nullable=False)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    expected_count = Column(Integer, nullable=True)       # 源头报的总数（腾讯备用：沿用的名单长度）
    observed_count = Column(Integer, nullable=True)       # 去重后实际拿到
    valid_market_cap_count = Column(Integer, nullable=True)
    pages_expected = Column(Integer, nullable=True)
    pages_ok = Column(Integer, nullable=True)
    is_complete = Column(Boolean, nullable=False, default=False)
    is_settled = Column(Boolean, nullable=True)           # None = 没拿到数据，无从判断
    applied = Column(Boolean, nullable=False, default=False)  # 这批行是否写进了日快照
    status = Column(String(20), nullable=False)           # ok | incomplete | failed | intraday | kept_existing
    warnings = Column(Text, nullable=True)                # JSON 数组
    error = Column(Text, nullable=True)


class LargeCapStockBoards(Base):
    """
    个股所属板块（东财 F10），大市值模块自己的缓存，**不碰 StockSectorRelation**
    ——那张表只关联关注板块，是别的页面的口径。

    boards 是 JSON：[[BK 码, 板块名, F10 里的排位], ...]。F10 里前三位是一/二/三级行业，
    之后是地区、风格、概念，类型由 sectors 表的 sector_type 决定。
    """
    __tablename__ = "large_cap_stock_boards"

    id = Column(Integer, primary_key=True)
    stock_code = Column(String(10), nullable=False, unique=True, index=True)
    boards = Column(Text, nullable=True)
    status = Column(String(10), nullable=False)          # ok | empty | error
    fetched_at = Column(DateTime, nullable=True)         # 最近一次拿到非空结果
    attempted_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
