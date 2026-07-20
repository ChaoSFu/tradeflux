"""
交易复盘日志（个人操作记录）。

一行 = 一次操作（买入/加仓/减仓/卖出/清仓），构成个人交易的执行流水。
建仓类操作填理由/计划止损/目标;平仓类操作填卖出触发/已实现盈亏。
建仓/加仓/减仓/卖出时自动带入当时的市场环境快照（温度/阶段/建议仓位），
供后续（P2）检测引擎判定「逆势」等问题时使用——留存交易当下的客观口径。
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Text
from sqlalchemy.sql import func

from ..database import Base


class TradeJournal(Base):
    __tablename__ = "trade_journal"

    id = Column(Integer, primary_key=True, index=True)
    # 归属用户（当前为单管理员账号,预留多用户维度:每人一份私有记录）
    owner = Column(String(64), nullable=False, index=True)

    stock_code = Column(String(16), nullable=False, index=True)
    stock_name = Column(String(64), nullable=True)

    # 买入 | 加仓 | 减仓 | 卖出 | 清仓
    action = Column(String(8), nullable=False)
    trade_time = Column(DateTime, nullable=False, index=True)
    price = Column(Float, nullable=False)
    position_pct = Column(Float, nullable=True)   # 本笔占总资金仓位 %

    # 建仓/加仓相关（事前摩擦:理由与止损必填由前端约束）
    reason = Column(Text, nullable=True)
    planned_stop = Column(Float, nullable=True)
    target = Column(Float, nullable=True)

    # 情绪/行为自评标签：计划内 | 抄底做T | 逆势加仓 | 回本补救 | 追高 | 其他
    emotion_tag = Column(String(16), nullable=True)
    note = Column(Text, nullable=True)            # 自由备注/情绪记录

    # 平仓相关（卖出/减仓/清仓）
    exit_reason = Column(String(16), nullable=True)   # 止损 | 恐慌 | 反弹跑 | 目标达成 | 其他
    realized_pnl = Column(Float, nullable=True)       # 本笔已实现盈亏（元）
    pnl_pct = Column(Float, nullable=True)            # 本笔盈亏 %

    # 交易当下的市场环境快照（自动带入,不用手填）
    mkt_temperature = Column(Float, nullable=True)
    mkt_phase = Column(String(24), nullable=True)
    mkt_suggested_position = Column(Float, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
