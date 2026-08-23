"""
弱转强雷达（Weak-to-Strong Radar）Phase 1 数据模型。

两张表，跟 Stock/StockDailySnapshot 是同一种"当前态 vs 追加日志"关系：
  - WeakToStrongCandidate：当前态，一股一行，每次 /refresh 原地更新。
  - WeakToStrongEvent：追加写状态变化日志，只在 current_state 真的变化时插入一条，
    不是每次刷新都写。Phase 2/3 专属字段（market_trend_score 等）Phase 1 不写，
    全部 nullable。
"""
from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, Text
from sqlalchemy.sql import func

from ..database import Base


class WeakToStrongCandidate(Base):
    __tablename__ = "weak_to_strong_candidates"

    id = Column(Integer, primary_key=True, index=True)
    stock_id = Column(Integer, ForeignKey("stocks.id"), nullable=False, index=True, unique=True)
    stock_code = Column(String(10), nullable=False, index=True)
    stock_name = Column(String(50), nullable=False)

    # ── 候选池生命周期 ──────────────────────────────────────────────────────
    first_seen_date = Column(Date, nullable=False)
    last_seen_date = Column(Date, nullable=False)
    consecutive_miss_days = Column(Integer, default=0, nullable=False)
    candidate_source = Column(String(20), nullable=False, default="prompt1")  # prompt1|prompt2|both
    is_active = Column(Boolean, default=True, nullable=False, index=True)
    # 弱转强分型占位字段（架构预留，Phase 2 恒为 GENERIC，不接分型专属逻辑）：
    # 未来会拆 TREND/EMOTION/ANTI_NUCLEAR 三套 Policy（各自的强弱定义/龙头打分/
    # 市场权限不同），届时先以 Shadow Mode 并行计算记录、不参与正式 BUYABLE 判断，
    # 验证过后再逐步切换，不会现在直接重写生产状态机。
    setup_type = Column(String(20), nullable=False, default="GENERIC")

    # ── Sector Gate（Theme = Stock.primary_sector_id）─────────────────────
    sector_id = Column(Integer, ForeignKey("sectors.id"), nullable=True)
    sector_name = Column(String(100), nullable=True)
    sector_category = Column(String(30), nullable=True)
    # NEW_START/EXPANDING/MAIN_UPTREND/HEALTHY_DIVERGENCE/HIGH_LEVEL_WARNING/DECLINING/DEAD
    sector_strength_score = Column(Float, nullable=True)
    sector_momentum_score = Column(Float, nullable=True)
    sector_divergence_health = Column(Float, nullable=True)  # 仅 phase=4（分歧阶段）有值，独立暴露的板块负反馈原始分
    is_mainline_sector = Column(Boolean, default=False, nullable=False)  # 2026-08-23新增：所属板块是否在当前MAIN_UPTREND强度前N名内，不在则结构确认封顶CONFIRMING

    # ── Leader Gate ─────────────────────────────────────────────────────
    leader_type = Column(String(20), nullable=True)  # core|backup|undetermined|non_leader
    leader_rank = Column(Integer, nullable=True)
    leader_score = Column(Float, nullable=True)  # Core Leader Score，跟 Stock.leader_score 是不同的分

    # ── 状态机（2026-08-22 二次重构：结构事实与交易决策彻底分离）───────────────
    current_state = Column(String(20), nullable=False, default="WATCH", index=True)
    # 交易决策层：结构事实 + 闸门 + 软上限推导出的最终展示值。
    # WATCH|READY|REPAIRING|CONFIRMING|BUYABLE|WAIT|BLOCK ——BUYABLE/WAIT/BLOCK
    # 从来不是结构层自己会产生的值，只在这一层由 derive_display_state 推导。
    structural_state = Column(String(20), nullable=False, default="WATCH")
    # 结构事实层：只看价格行为，不受任何闸门影响，闸门临时不通过时底层仍持续
    # 推进。WATCH|READY|REPAIRING|PULLBACK|CONFIRMED|FAILED——注意这里没有
    # BUYABLE/WAIT/BLOCK，"是否可以交易"是决策层的事，不是结构事实。
    # current_state=BLOCK 时 structural_state 可能已经在往前走（甚至已经是
    # CONFIRMED），闸门恢复后展示立刻反映真实进度。
    setup_substate = Column(String(20), nullable=True)  # 保留字段，Phase 2 暂未使用
    recovery_high = Column(Float, nullable=True)   # H1：修复阶段的高点，检测到有效回踩后冻结
    pullback_low = Column(Float, nullable=True)    # L1：回踩阶段的滚动最低价，pullback_started 后才开始记录
    pullback_started = Column(Boolean, default=False, nullable=False)  # 是否已经出现过一次有效回踩（非噪音级别的小回落）
    signal_trade_date = Column(Date, nullable=True)  # 当前这套结构态属于哪个交易日，跨日刷新时用于重置
    refresh_sample_count = Column(Integer, default=0, nullable=False)  # 当日已刷新次数，前端老实展示样本量

    # ── 行情快照（每次 refresh 覆盖，来自 fetch_stock_quotes_batch）─────────
    price = Column(Float, nullable=True)
    prev_close = Column(Float, nullable=True)
    ma5 = Column(Float, nullable=True)
    vwap = Column(Float, nullable=True)  # 当日成交额/成交量算出的真实VWAP，缺失（如尚无成交）时为None
    day_open = Column(Float, nullable=True)
    day_high = Column(Float, nullable=True)
    day_low = Column(Float, nullable=True)
    day_amount = Column(Float, nullable=True)
    turnover_rate = Column(Float, nullable=True)

    # ── 竞价字段（9:25 后填充）──────────────────────────────────────────────
    auction_gap = Column(Float, nullable=True)
    auction_sector_gap = Column(Float, nullable=True)
    is_auction_exceeded = Column(Boolean, nullable=True)

    # ── Space Gate（Phase 2：数值 + 空间不足降级判断）───────────────────────
    limit_price = Column(Float, nullable=True)
    limit_room = Column(Float, nullable=True)

    # ── Phase 2：三层止损 + 压力情景风险回报比 ─────────────────────────────
    technical_stop = Column(Float, nullable=True)  # 技术止损：回踩低点/MA5
    standard_stop = Column(Float, nullable=True)   # 标准止损：技术位基础上留缓冲
    stress_stop = Column(Float, nullable=True)      # 压力止损：模拟次日跌停开盘的极端情形
    stress_rr = Column(Float, nullable=True)        # 压力情景风险回报比 = 涨停空间% / 跌停幅度%

    # ── 风险 ────────────────────────────────────────────────────────────
    regulatory_risk_level = Column(String(10), nullable=True)  # LOW|MEDIUM|HIGH|EXTREME

    # ── 数据新鲜度保护 ──────────────────────────────────────────────────────
    signal_enabled = Column(Boolean, default=True, nullable=False)
    data_freshness_seconds = Column(Float, nullable=True)

    # ── 解释性文本（JSON 编码的字符串列表）────────────────────────────────────
    trigger_reasons = Column(Text, nullable=True)
    block_reasons = Column(Text, nullable=True)

    last_refreshed_at = Column(DateTime, nullable=True)
    refresh_duration_ms = Column(Integer, nullable=True)
    formula_version = Column(String(20), nullable=False, default="w2s_radar_v0.1.0")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class WeakToStrongEvent(Base):
    """状态变化事件日志：只在 current_state 实际改变时插入，追加写不覆盖。"""
    __tablename__ = "weak_to_strong_events"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    stock_code = Column(String(10), nullable=False, index=True)
    sector_id = Column(Integer, ForeignKey("sectors.id"), nullable=True)
    theme_id = Column(Integer, nullable=True)  # = sector_id，字段名对齐用户规格里的 theme_id

    # Phase 2：Market Gate（Phase 1 恒为 null）
    market_trend_score = Column(Float, nullable=True)
    risk_appetite_score = Column(Float, nullable=True)

    # Sector Gate（Phase 1 已populate）
    sector_phase = Column(String(30), nullable=True)  # = sector_category
    sector_strength = Column(Float, nullable=True)
    sector_momentum = Column(Float, nullable=True)

    # Leader Gate（Phase 1 已populate）
    leader_type = Column(String(20), nullable=True)
    leader_rank = Column(Integer, nullable=True)
    leader_score = Column(Float, nullable=True)

    # Phase 3：板块带动性（Phase 1 恒为 null）
    leadership_impact = Column(Float, nullable=True)

    setup_state = Column(String(20), nullable=True)
    structural_state = Column(String(20), nullable=True)  # 结构事实层原始值，独立于 setup_state（展示态）
    recovery_high = Column(Float, nullable=True)  # H1 原始值：事件发生时刻的修复高点
    pullback_low = Column(Float, nullable=True)   # L1 原始值：事件发生时刻的回踩低点
    # 保留每次状态变化时刻的 H1/L1 原始快照，供以后回看真实样本调 w2s_pullback_min_pct
    # 这类阈值时用——不用等专门的回测框架，这两列本身就是最小可用的调参数据源。

    # 行情快照
    price = Column(Float, nullable=True)
    prev_close = Column(Float, nullable=True)
    vwap = Column(Float, nullable=True)  # Phase 1 结构性缺失，恒为 null
    ma5 = Column(Float, nullable=True)
    limit_price = Column(Float, nullable=True)
    limit_room = Column(Float, nullable=True)

    regulatory_risk = Column(String(10), nullable=True)

    # Phase 3：日内获利估算 / T1 抛压（Phase 1 恒为 null）
    profit_volume_ratio = Column(Float, nullable=True)
    t1_supply_risk = Column(Float, nullable=True)

    # Phase 2：风险回报（Phase 1 恒为 null）
    technical_stop = Column(Float, nullable=True)
    effective_risk = Column(Float, nullable=True)
    stress_rr = Column(Float, nullable=True)

    old_state = Column(String(20), nullable=True)
    new_state = Column(String(20), nullable=False)
    trigger_reasons = Column(Text, nullable=True)  # JSON 编码字符串列表
    block_reasons = Column(Text, nullable=True)

    data_freshness = Column(Float, nullable=True)
    formula_version = Column(String(20), nullable=False, default="w2s_radar_v0.1.0")


class WeakToStrongSnapshot(Base):
    """
    候选专属日内快照（2026-08-23新增）：每次 /refresh 对每个活跃候选追加一行，
    不像 WeakToStrongEvent 只在展示态变化时才写——这里要的是稠密的价格采样，
    供以后回看真实样本调 H1/L1 回踩噪音阈值（w2s_pullback_min_pct）、未来做
    回测重放用。

    刻意不新增任何自动定时抓取：这张表只在现有 run_refresh 触发的时候（09:26
    定时 + 用户手动点刷新）顺手多写一行，复用同一次已经拉到的报价数据，不产生
    任何新的东财接口请求，也不改变现有的刷新频率——是否需要新增自动定时抓取
    （比如每5-15分钟一次）是独立的运营决策，会改变对东财接口的稳定态请求压力，
    需要单独跟用户确认，见 docs/WEAK_TO_STRONG_RADAR.md 第6节"已知运营缺口"。
    """
    __tablename__ = "weak_to_strong_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    trade_date = Column(Date, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    stock_code = Column(String(10), nullable=False, index=True)

    price = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    amount = Column(Float, nullable=True)
    volume = Column(Float, nullable=True)
    vwap = Column(Float, nullable=True)  # 已经过合理性校验的真实VWAP，None代表当次校验未通过或量为0

    structural_state = Column(String(20), nullable=True)  # 写入时刻的结构事实层状态，方便回看时对齐H1/L1
    recovery_high = Column(Float, nullable=True)
    pullback_low = Column(Float, nullable=True)
