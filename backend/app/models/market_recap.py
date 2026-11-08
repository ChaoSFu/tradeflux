"""
每日盘面复盘的人工研究数据。**市场事实不在这里**——事实每次按日期从原有各表现算
（market_recap_service.build_context），这里只存人的判断，三类东西分开放：

    人工研究    MarketRecap（当日判断、情景、反思）、RecapWatchStock（重点观察池）、
                LeaderRelation（高标关联知识，跨日保留）、LeaderResponseDaily（每日响应）、
                NextDayPlan（次日预案草稿）
    锁定预案    RecapLockedVersion：锁定那一刻的完整快照，**只插不改**。锁定后再改草稿、
                再锁，就是新的一版；旧版原样留着，回答「行情发生之前我当时到底怎么判断的」

全部按 owner（登录用户名）隔离。
"""
from sqlalchemy import (
    JSON, Boolean, Column, Date, DateTime, Float, Index, Integer, String, Text, Time,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from ..database import Base


class MarketRecap(Base):
    """一个用户一个交易日一条草稿。"""
    __tablename__ = "market_recaps"
    __table_args__ = (UniqueConstraint("owner", "trade_date", name="uq_market_recap_owner_date"),)

    id = Column(Integer, primary_key=True)
    owner = Column(String(64), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)

    # 用户的次日交易姿态（attack 进攻 / probe 试错 / defend 防守 / unclear 暂不明确）。
    # **是人的判断，不是系统算出来的市场结论**
    market_posture = Column(String(16), nullable=True)
    market_evidence = Column(JSON, nullable=True)      # 勾选的数据依据：[{key, label, value}]
    evidence_note = Column(Text, nullable=True)
    invalidation = Column(JSON, nullable=True)         # 反证条件：[str]
    sector_picks = Column(JSON, nullable=True)         # [{code, name, tier: core|secondary|drop, state_label}]
    scenarios = Column(JSON, nullable=True)            # {"A": {...}, "B": {...}, "C": {...}, "N": {...}} 各自分开存
    flat_plan = Column(Boolean, nullable=False, default=False)   # 空仓预案也是有效预案

    discovery = Column(Text, nullable=True)
    mistake = Column(Text, nullable=True)
    mistake_type = Column(String(16), nullable=True)   # judgment | execution | strategy | none
    hypothesis = Column(Text, nullable=True)
    deviations = Column(JSON, nullable=True)           # 偏差记录：[{subject, original, actual, kind, cause, next_step, keep_verifying}]

    status = Column(String(16), nullable=False, default="draft")   # draft | locked
    key_points = Column(JSON, nullable=True)           # 明天最重要的三个观察点：[str]，最多 3 条
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class RecapWatchStock(Base):
    """
    重点观察池。**跨日保留，直到用户主动取消**：某天跌出自动候选名单，不会把这里的
    研究记录带走。取消只写 removed_date，不删行；再加进来是新的一行。
    某天的观察池 = added_date <= D 且（removed_date 为空或 > D）。
    """
    __tablename__ = "recap_watch_stocks"
    __table_args__ = (Index("ix_recap_watch_owner_code", "owner", "stock_code"),)

    id = Column(Integer, primary_key=True)
    owner = Column(String(64), nullable=False, index=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(50), nullable=True)
    added_date = Column(Date, nullable=False)
    removed_date = Column(Date, nullable=True)
    deep = Column(Boolean, nullable=False, default=False)   # 要建深入关联图谱的那 3~5 只
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class LeaderRelation(Base):
    """
    高标 → 属性 → 关联股票。**长期知识，跨日继承**；当天市场有没有响应另记在
    LeaderResponseDaily，不在这里——昨天的联动不能延续成今天已成立的事实。

    relation_basis 只表示关联依据的性质，**由属性类型决定，不让人手填**：
      A  industry / event        实际产业、业务、事件
      B  concept / history / structure   共享概念、历史联动、情绪结构
      C  name                    名称、谐音、叙事联想
    名称联想永远是 C，不可能被标成产业关联。
    """
    __tablename__ = "leader_relations"
    __table_args__ = (UniqueConstraint("owner", "leader_code", "attribute_type", "attribute_value",
                                       "related_code", name="uq_leader_relation"),)

    id = Column(Integer, primary_key=True)
    owner = Column(String(64), nullable=False, index=True)
    leader_code = Column(String(10), nullable=False, index=True)
    leader_name = Column(String(50), nullable=True)
    attribute_type = Column(String(16), nullable=False)
    attribute_value = Column(String(100), nullable=False)
    related_code = Column(String(10), nullable=False)
    related_name = Column(String(50), nullable=True)
    relation_basis = Column(String(1), nullable=False)
    evidence = Column(Text, nullable=True)                # 关联依据（文字）
    source = Column(String(20), nullable=False)          # manual | name_rule | sector_relation | snapshot_concept | limit_up_industry
    # 证据有没有核过。**不是可信度分**
    confidence_status = Column(String(12), nullable=False, default="unverified")  # unverified | verified | rejected
    first_recorded = Column(Date, nullable=False)
    last_verified = Column(Date, nullable=True)
    note = Column(Text, nullable=True)
    removed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class LeaderResponseDaily(Base):
    """
    某天某条关联的市场响应。领先 / 同步 / 滞后只有在**两边当天都有首封时间**时才允许记，
    时间存下来当证据；没有分时证据就只能是「未见明显响应 / 无法判断 / 待验证」。
    """
    __tablename__ = "leader_response_daily"
    __table_args__ = (UniqueConstraint("owner", "trade_date", "relation_id", name="uq_leader_response"),)

    id = Column(Integer, primary_key=True)
    owner = Column(String(64), nullable=False, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    relation_id = Column(Integer, nullable=False, index=True)
    leader_code = Column(String(10), nullable=False)
    related_code = Column(String(10), nullable=False)
    price_change = Column(Float, nullable=True)           # 记录时关联股当天涨跌幅（事实快照）
    limit_up = Column(Boolean, nullable=True)             # None = 不知道
    leader_trigger_time = Column(Time, nullable=True)     # 高标当天首封
    first_trigger_time = Column(Time, nullable=True)      # 关联股当天首封
    response_status = Column(String(16), nullable=False)  # leading | sync | lagging | no_response | unknown | pending
    evidence_source = Column(String(40), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class NextDayPlan(Base):
    """次日预案的候选股（草稿）。一天最多 5 只；锁定时整组进 RecapLockedVersion 的快照。"""
    __tablename__ = "next_day_plans"
    __table_args__ = (Index("ix_next_day_plan_owner_date", "owner", "recap_date"),)

    id = Column(Integer, primary_key=True)
    owner = Column(String(64), nullable=False)
    recap_date = Column(Date, nullable=False)
    sort = Column(Integer, nullable=False, default=0)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(50), nullable=True)
    leader_code = Column(String(10), nullable=True)
    sector_name = Column(String(100), nullable=True)
    scenario = Column(String(2), nullable=True)           # A | B | C | N（新核心）
    reason = Column(Text, nullable=True)
    relation_note = Column(Text, nullable=True)           # 关联依据（跟哪只高标、凭什么关联）
    watch_trigger = Column(Text, nullable=True)           # 观察触发：高标出现什么才去看它（不是买点）
    entry_trigger = Column(Text, nullable=False)          # 它自己的入场触发
    invalidation = Column(Text, nullable=False)
    planned_stop = Column(Float, nullable=True)           # 计划止损价
    risk_budget = Column(Float, nullable=True)            # 单笔风险预算（占账户 %）
    position_limit = Column(Float, nullable=True)         # 仓位上限（占账户 %）
    observe_until = Column(Date, nullable=True)
    status = Column(String(16), nullable=False, default="waiting")  # waiting | triggered | not_triggered | invalid | unverified
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class RecapLockedVersion(Base):
    """
    锁定的预案版本。**只插不改**——没有任何更新它的代码路径。

    locked_at 跟目标交易日 09:30 比，决定这一版是「盘前锁定」还是「事后记录」。目标交易日
    在锁定时可能还不在交易日历里（日历只到今天），所以 target_trade_date 是锁定时的推测，
    验证时再按日历重新确定。
    """
    __tablename__ = "recap_locked_versions"
    __table_args__ = (UniqueConstraint("owner", "recap_date", "version", name="uq_recap_locked_version"),)

    id = Column(Integer, primary_key=True)
    owner = Column(String(64), nullable=False, index=True)
    recap_date = Column(Date, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    locked_at = Column(DateTime, nullable=False)
    target_trade_date = Column(Date, nullable=True)
    target_is_provisional = Column(Boolean, nullable=False, default=True)
    payload = Column(JSON, nullable=False)                # 判断、观察池、关联、情景、预案、反思
    data_status = Column(JSON, nullable=True)             # 锁定时各事实模块的数据状态与口径


class MarketPopularCoreRun(Base):
    """
    某个交易日人气核心池的一次冻结：各召回来源当时的数据状态和统计范围。
    同一天同一规则版本只留一份（重新冻结是覆盖）。
    """
    __tablename__ = "market_popular_core_runs"
    __table_args__ = (UniqueConstraint("trade_date", "rule_version", name="uq_popular_core_run"),)

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    rule_version = Column(String(16), nullable=False)
    sources = Column(JSON, nullable=False)                # {source: {status, scope, n, note}}
    built_at = Column(DateTime, nullable=False)


class MarketPopularCoreDaily(Base):
    """
    人气核心池每日快照：那一天谁凭什么进了池（辨识度依据）和当时的结构状态。
    **不重存行情历史**，只存成员资格和排名依据；不能拿今天的池子回填过去。
    区间涨幅 / 排名是全市场快照（前复权）口径；涨停次数、连板、高板是已跟踪股票口径。
    """
    __tablename__ = "market_popular_core_daily"
    __table_args__ = (UniqueConstraint("trade_date", "stock_code", "rule_version", name="uq_popular_core"),)

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    stock_code = Column(String(10), nullable=False)
    stock_name = Column(String(50), nullable=True)
    board = Column(String(8), nullable=True)              # 主板 / 创业板 / 科创板（涨跌停制度不同）
    is_st = Column(Boolean, nullable=True)
    rule_version = Column(String(16), nullable=False)
    selection_sources = Column(JSON, nullable=False)      # [{source, rank, value, scope}]
    pct_change = Column(Float, nullable=True)             # 当日涨跌幅
    rank_5d = Column(Integer, nullable=True)
    rank_10d = Column(Integer, nullable=True)
    rank_20d = Column(Integer, nullable=True)
    rank_60d = Column(Integer, nullable=True)
    pct_change_5d = Column(Float, nullable=True)
    pct_change_10d = Column(Float, nullable=True)
    pct_change_20d = Column(Float, nullable=True)
    pct_change_60d = Column(Float, nullable=True)
    limit_up_days_10d = Column(Integer, nullable=True)
    limit_up_days_20d = Column(Integer, nullable=True)
    limit_up_days_60d = Column(Integer, nullable=True)
    current_board = Column(Integer, nullable=True)
    historical_max_board = Column(Integer, nullable=True)
    turnover_rank = Column(Integer, nullable=True)
    amount = Column(Float, nullable=True)                 # 成交额（亿元）
    limit_up_next_avg_pct = Column(Float, nullable=True)  # 冻结时的值；历史日期现算时不回填
    limit_up_next_samples = Column(Integer, nullable=True)
    lifecycle_state = Column(String(20), nullable=True)
    sector = Column(String(100), nullable=True)
    sector_basis = Column(String(30), nullable=True)
    data_status = Column(JSON, nullable=True)             # 这一行哪些字段缺了、为什么


class LeaderActivationEvent(Base):
    """
    人气核心活跃事件：当天发生的事实，只用来提醒去看关联池，**不是买入信号**。
    同一天同一只可以有多种事件；(交易日, 股票, 事件类型, 规则版本) 唯一，重复刷新是覆盖。
    """
    __tablename__ = "leader_activation_events"
    __table_args__ = (UniqueConstraint("trade_date", "leader_code", "event_type", "rule_version",
                                       name="uq_leader_activation"),)

    id = Column(Integer, primary_key=True)
    trade_date = Column(Date, nullable=False, index=True)
    leader_code = Column(String(10), nullable=False)
    leader_name = Column(String(50), nullable=True)
    event_type = Column(String(24), nullable=False)
    event_time = Column(Time, nullable=True)              # 只有首封时间这类真实时间戳才填
    evidence = Column(Text, nullable=True)
    source = Column(String(40), nullable=True)
    data_status = Column(String(12), nullable=False, default="ok")
    rule_version = Column(String(16), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
