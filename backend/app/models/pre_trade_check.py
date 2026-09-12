"""
买入检查的每一次记录（2026-09-11 新增）。

**判定快照和后续结果分开存。** facts/checks/verdict 是 as_of 那一刻做判定时的样子，
一旦写入就不改；outcome_json 是用户事后点「查看后续走势」才算的，**永远不回写
verdict**——复盘要练的是决策质量，不是拿结果倒推"当时对不对"（Outcome Bias）。

必要字段建索引，其余整块存 JSON：规则一定会演化（rule_version），把几十个判定项
拆成列，改一次规则就要迁移一次表。JSON 用通用类型不用 JSONB——测试库是 SQLite。
"""
from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, Text
from sqlalchemy.sql import func

from ..database import Base


class PreTradeCheck(Base):
    __tablename__ = "pre_trade_checks"

    id = Column(Integer, primary_key=True, index=True)
    owner = Column(String(64), nullable=False, index=True)
    stock_code = Column(String(16), nullable=False, index=True)
    stock_name = Column(String(64), nullable=True)

    as_of = Column(DateTime, nullable=False, index=True)   # 所有事实的时间边界
    mode = Column(String(12), nullable=False)               # LIVE | HISTORICAL

    intended_price = Column(Float, nullable=True)
    position_pct = Column(Float, nullable=True)
    planned_stop = Column(Float, nullable=True)
    reason = Column(Text, nullable=True)
    thesis_sector = Column(String(100), nullable=True)

    verdict = Column(String(12), nullable=False, index=True)   # READY | WAIT | BLOCKED
    rule_version = Column(String(32), nullable=False)

    facts_json = Column(JSON, nullable=True)            # as_of 那一刻取到的事实（含来源/时间/质量）
    checks_json = Column(JSON, nullable=True)           # 逐项判定 + 结论
    manual_answers_json = Column(JSON, nullable=True)
    data_quality_json = Column(JSON, nullable=True)

    outcome_json = Column(JSON, nullable=True)          # 事后揭晓，不影响 verdict
    outcome_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
