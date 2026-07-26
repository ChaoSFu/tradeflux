from sqlalchemy import Column, Integer, String, Float, Date, DateTime
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
