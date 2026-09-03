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


class SectorIndexDaily(Base):
    """
    **板块指数日线**（相对强度 RS_sector 的基准，2026-09-03 新增）。

    ## 为什么不塞进 SectorDailySnapshot

    那张表存的是板块**统计**（涨停数、板高、情绪分、风险分），2026-08-21 才开始写，
    只有几天历史。而板块指数能一次性回填 300 根（实测 BK0832 工业互联网
    2025-06-16 ~ 2026-09-03）。往里灌 300 天历史，就会造出一大批
    `phase=0 / limit_up_count=0 / board_height=0` 的行——那是**用 0 表达"不知道"**，
    本仓库为这个模式栽过太多次（换手率恒为0、盘中价冒充收盘价、连板缺失当成1板）。

    板块的**行情**和板块的**统计**是两类事实，分开存，各自语义干净。
    表结构刻意镜像 IndexDailySnapshot，RS 那边的锚点对齐逻辑可以直接复用。

    ## 数据来源

    东财 push2his，secid = "90." + Sector.code（`BK0832` → `90.BK0832`）。
    **不能走 fetch_index_kline()**：那个函数是腾讯优先的（为那 5 个固定指数定的，
    因为 push2his 长期被限流），而腾讯根本没有 BK 板块码，会静默落到兜底再报错。
    """
    __tablename__ = "sector_index_daily"
    __table_args__ = (
        UniqueConstraint("sector_code", "date", name="uq_sector_index_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    sector_code = Column(String(20), nullable=False, index=True)   # BK0832，对应 sectors.code
    date = Column(Date, nullable=False, index=True)
    close = Column(Float, nullable=True)
    pct_change = Column(Float, nullable=True)   # 当日涨跌幅 %
    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)       # 成交额（元）

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
    szzs_pe = Column(Float, nullable=True)           # 上证指数滚动市盈率（中证指数官方口径）
    kc50_pe = Column(Float, nullable=True)           # 科创50滚动市盈率（同一中证指数官网口径，2026-08-24新增）
    bz50_pe = Column(Float, nullable=True)           # 北证50滚动市盈率（同一中证指数官网口径，2026-08-24新增；
                                                      # 深证成指/创业板指是深交所自己发布的原生指数，不在中证
                                                      # 指数官网的perf库里，同一接口查不到，未接入）

    # 成交额（东财 RPT_DMSK_WINDVANE_SUMTVALLIST）
    deal_amount = Column(Float, nullable=True)       # 沪深两市成交额（元，收盘官方值）
    deal_amount_hsj = Column(Float, nullable=True)   # 含北交所
    # 盘中快照（读取时随实时数据刷新;收盘后 deal_amount 为准,predicted 留存供预测vs实际复盘）
    intraday_amount = Column(Float, nullable=True)   # 当日盘中最新累计成交额（元）
    predicted_amount = Column(Float, nullable=True)  # 预测全天成交额（元，差额外推）

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
