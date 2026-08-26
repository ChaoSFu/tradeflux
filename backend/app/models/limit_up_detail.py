from sqlalchemy import (
    Column, Integer, String, Float, Date, Time, DateTime, ForeignKey, Text, UniqueConstraint,
)
from sqlalchemy.sql import func

from ..database import Base


class LimitUpDailyDetail(Base):
    """
    某只股票某个交易日的**涨停事件事实**（涨停板块雷达数据源），2026-08-25新增。

    为什么不塞进 StockDailySnapshot：
      · StockDailySnapshot 是日级状态与滚动窗口指标（连板数/N日涨幅/评分/阶段），
        由 K线窗口计算得出，语义是"这一天收盘后这只股票是什么状态"。
      · 这里存的是涨停这个事件本身的细节（谁先封的、封了几次、排队多少钱），
        来自完全不同的外部接口，而且**有明显的日内时效**——用户可能在 10:00、
        13:30、14:50 分别手动刷新，封单额和最终封板时间每次都会变。
      两者混在一张表里，会让"这一行是收盘定论还是盘中快照"变得无法区分，正是本
      仓库前几轮刚清理掉的那类语义混淆。所以单独建表，并且带 refreshed_at 明确
      标注这份数据是什么时候抓的。

    字段可空一律表示"这个来源没给"，不是 0——封单额为0（无人排队）和不知道封单额
    是两回事，页面要分别显示 0.00亿 和 —。
    """
    __tablename__ = "limit_up_daily_details"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_limit_up_detail_stock_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True)
    stock_code = Column(String(10), nullable=False, index=True)  # 冗余存一份，免去聚合时回查
    stock_name = Column(String(50), nullable=True)
    trade_date = Column(Date, nullable=False, index=True)

    # 涨停原因（催化剂，**不是板块归属**——归组一律走 StockSectorRelation）
    limit_reason = Column(String(500), nullable=True)   # "粮食安全+控股股东增持+..."
    limit_content = Column(Text, nullable=True)         # AI生成的详述，含免责声明

    # 涨停事件事实
    first_limit_time = Column(Time, nullable=True)      # 首次封板时间
    last_limit_time = Column(Time, nullable=True)       # 最终封板时间（≠首封时才有意义）
    seal_amount = Column(Float, nullable=True)          # 封单额（元）
    broken_times = Column(Integer, nullable=True)       # 当日炸板次数
    board_count = Column(Integer, nullable=True)        # 连板数
    limit_stat_days = Column(Integer, nullable=True)    # "N日M板" 的 N
    limit_stat_count = Column(Integer, nullable=True)   # "N日M板" 的 M

    # 当日行情（来自同一个涨停池接口，顺手存下，避免为了展示再查一遍）
    price = Column(Float, nullable=True)
    pct_change = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)               # 成交额（元）
    turnover_rate = Column(Float, nullable=True)        # 换手率 %
    float_market_cap = Column(Float, nullable=True)     # 流通市值（元），留给以后算封单占比
    em_industry = Column(String(50), nullable=True)     # 东财行业名，仅参考不用于归组

    # 数据溯源：页面必须能显示"这份数据是什么时候、从哪来的"
    source = Column(String(30), nullable=True)          # "eastmoney"
    source_trade_date = Column(Date, nullable=True)     # 向数据源请求的交易日
    refreshed_at = Column(DateTime, nullable=True)      # 本行最后一次成功刷新的时刻

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class BrokenBoardDailyDetail(Base):
    """
    炸板（盘中触及涨停、收盘没封住）当日明细。单独一张表而不是在上面加个标志位：
    炸板股当天**没有**涨停，把它塞进 limit_up_daily_details 会让"这张表里的行
    等于当天涨停股"这个最基本的语义失效，聚合时每处都要记得排除。
    主要用途是算板块封板率 = 涨停数 /(涨停数 + 炸板数)。
    """
    __tablename__ = "broken_board_daily_details"
    __table_args__ = (
        UniqueConstraint("stock_id", "trade_date", name="uq_broken_board_detail_stock_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True)
    stock_code = Column(String(10), nullable=False, index=True)
    stock_name = Column(String(50), nullable=True)
    trade_date = Column(Date, nullable=False, index=True)

    first_limit_time = Column(Time, nullable=True)   # 首次触及涨停的时间（没有最终封板时间）
    broken_times = Column(Integer, nullable=True)
    pct_change = Column(Float, nullable=True)
    em_industry = Column(String(50), nullable=True)

    # 2026-08-26 补全。这些字段东财 getTopicZBPool 一直都返回，只是此前没解析——
    # 炸板这一块要回答的是"封板有多不坚决"，光有涨跌幅和首封时间答不了：
    # 一个 6天5板 的高位股炸板，和一个首板冲高回落，对板块的含义完全不同。
    price = Column(Float, nullable=True)             # 最新价（元）
    limit_price = Column(Float, nullable=True)       # 当日涨停价（元）
    board_count = Column(Integer, nullable=True)     # 连板数
    limit_stat_days = Column(Integer, nullable=True) # "N天M板" 的 N
    limit_stat_count = Column(Integer, nullable=True)# "N天M板" 的 M
    turnover_rate = Column(Float, nullable=True)     # 换手率 %
    amount = Column(Float, nullable=True)            # 成交额（元）
    float_market_cap = Column(Float, nullable=True)  # 流通市值（元）
    amplitude = Column(Float, nullable=True)         # 振幅 %

    source = Column(String(30), nullable=True)
    source_trade_date = Column(Date, nullable=True)
    refreshed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
