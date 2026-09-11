from sqlalchemy import (
    Boolean, Column, Integer, String, Float, Date, DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from ..database import Base


class MarketEffectDaily(Base):
    """
    每日市场效应结果缓存（赚钱效应 / 亏钱效应 精简版 MVP）。
    每个交易日一行，懒计算写入（首次请求缺失时现算），也可由回填脚本批量写入。
    冻结群体反馈直接查询 stock_daily_snapshots 按交易日计算（该表本身按日不可变，
    天然满足「T 日群体只用 T 日已知事实冻结」的防未来数据泄漏要求），不单独建
    冻结群体成员表。
    """
    __tablename__ = "market_effect_daily"

    id = Column(Integer, primary_key=True, index=True)
    trade_date = Column(Date, nullable=False, unique=True, index=True)

    profit_strength = Column(Float, nullable=False)   # 赚钱效应强度 0-100
    loss_strength = Column(Float, nullable=False)      # 亏钱效应强度 0-100
    quadrant = Column(String(20), nullable=False)      # benign_spread | strong_divergence | quiet_chaos | loss_spread
    lifecycle_state = Column(String(20), nullable=False)  # 简化5态

    # 全市场广度口径：full_market=真实全市场（来自 MarketBreadthDaily）
    # tracked_pool=跟踪股票池近似（MarketBreadthDaily 无历史数据时的退化口径）
    breadth_source = Column(String(20), nullable=False)
    coverage_ratio = Column(Float, default=1.0, nullable=False)

    cohorts_json = Column(JSONB, nullable=True)    # 6个冻结群体的次日反馈明细
    evidence_json = Column(JSONB, nullable=True)   # 结构化证据列表，供前端下钻/拼接结论
    summary = Column(String(500), nullable=True)   # 一句话结论（由 evidence 模板拼接）

    formula_version = Column(String(20), default="market_effect_v0.1.0", nullable=False)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class CohortOutcomeQuote(Base):
    """
    昨日群体里、今天**不在候选池**的票，今天的行情（2026-09-11 新增）。

    市场效应的「今日反馈」原来只看快照，而快照只有候选池里的票才有当天那一行——
    掉出池子的（多半是走弱的）要到第二天日更补历史才有。于是当天的反馈只算了幸存者：
    09-10 昨日涨停缓存 +3.83%（28/48），全部 48 只其实是 -1.04%，符号是反的。

    日更对完涨跌停之后，对这批票批量查一次行情存在这里，**只给市场效应用**。
    不写进快照表：那样板块统计这类「今天全市场」的数字都会跟着变。
    次日日更用收盘数据补上快照行之后，市场效应以快照为准，这里的行自然不再起作用。
    """
    __tablename__ = "cohort_outcome_quotes"
    __table_args__ = (UniqueConstraint("trade_date", "stock_id", name="uq_cohort_outcome_quote"),)

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True)
    close_price = Column(Float, nullable=True)
    pct_change = Column(Float, nullable=True)
    is_limit_up = Column(Boolean, nullable=True)
    is_limit_down = Column(Boolean, nullable=True)
    # 抓行情那一刻收盘了没有（bar_is_settled）。盘中跑的是现价，收盘后跑的是终值
    is_settled = Column(Boolean, nullable=True)
    fetched_at = Column(DateTime, nullable=True)
