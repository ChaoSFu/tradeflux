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
    # 流通股本（股）与它的观测日。2026-09-03 加，用来推算换手率：
    #     换手率% = 成交量(股) / 流通股本 × 100
    # 来源是 LimitUpDailyDetail.float_market_cap（涨停池/炸板池的 ltsz 字段）÷ 当日收盘价，
    # **零新增请求**。观测日必须一起存：流通股本会因除权、解禁跳变，拿三个月前的值算
    # 今天的换手率可能差 20% 以上，而这种错不会报警——存了日期至少能看出它有多旧。
    float_shares = Column(Float, nullable=True)
    float_shares_date = Column(Date, nullable=True)

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
    # 成交量（股）/ 成交额（元），2026-09-03 接入。数据一直在 fuyao dump 的 parquet
    # 里（volume/turnover 两列），只是 load_bars 此前没读。零新增请求。
    #
    # volume_source 必须一起存：dump 未复权、腾讯是 qfq 前复权，而**复权会同时调整
    # 价和量**。不记来源的话，一段序列里混了两种口径也看不出来，量比、量能衰减
    # 这类比较会静默出错。跟收盘价的两口径问题同一条纪律。
    #
    # None = 该来源没给这个数，不是 0。turnover_rate 那一版就是用 0.0 顶替"不知道"，
    # 结果全市场换手率长期恒为 0，情绪分里的因子事实上死掉很久却没人发现。
    volume = Column(Float, nullable=True)          # 成交量（股）
    amount = Column(Float, nullable=True)          # 成交额（元）
    volume_source = Column(String(10), nullable=True)  # "dump" | "tencent" | "sina"

    # OHLC（2026-08-27新增）。此前快照只存 close_price，从快照重建 KLineBar 时
    # open/high/low 一律填 0.0 —— 而**炸板判定要 high**（盘中最高价是否触及涨停价）、
    # 一字板判定要 high/low。所以历史 bar 的这两个标志只能沿用当初落库的值，重算不了；
    # 一旦当初落库时判错，就永久错下去，没有任何机制能纠正。
    # NULL = 这一行是加列之前写的，重建时仍退回 0.0（老行为），不伪造 OHLC。
    open_price = Column(Float, nullable=True)
    high_price = Column(Float, nullable=True)
    low_price = Column(Float, nullable=True)

    # 这一行的当日观测是不是**收盘之后**取的（2026-08-26新增）。
    # False = 盘中快照，close_price 是当时的现价而不是收盘价，随时会变。
    #
    # 加这一列是因为一个真实的生产事故：daily_update 可以被 UI 随时手动触发，
    # 用户 2026-08-26 盘中点了 9 次（09:46~14:00）。腾讯K线接口盘中就发当日那根
    # 未收盘的 bar，`bar.date == today` 成立，于是每一次都把当时的现价写成了
    # "今日收盘价"。收盘后 15:30 那一跑对 19% 的股票K线拉取失败，代码走
    # "保留上次可信值"分支——但保留下来的其实是 14:00 的盘中价。600984 因此在库里
    # 记成 close=5.43/+9.92%（盘中封板那一刻），实际收盘 4.66/-5.67% 是炸板大阴线，
    # 而这个假涨停会直接进涨停板块雷达的连板数和龙头分。
    #
    # 有了它，"保留上次可信值"才第一次真的成立：只有 is_settled=True 的旧值才算
    # 终值可以保留，盘中值必须被清掉而不是就地转正。
    is_settled = Column(Boolean, default=False, nullable=False)

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
