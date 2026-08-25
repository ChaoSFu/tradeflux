// ─── Stock ───────────────────────────────────────────────────────────────────

export interface Stock {
  id: number
  code: string
  name: string
  market: string
  is_st: boolean
  is_new_stock: boolean
  ipo_date: string | null
  in_strong_pool: boolean
  phase: string | null
  leader_score: number
  risk_score: number
  emotion_score: number
  board_count_60d: number
  board_down_count_60d: number
  limit_up_days_60d: number
  limit_up_days_20d: number
  limit_up_days_10d: number
  pct_change_60d: number
  pct_change_20d: number
  pct_change_10d: number
  top_10_pct_change_20d: boolean
  created_at: string | null
  updated_at: string | null
  primary_sector: string | null
  sector_id: number | null
  sector_phase: number | null
  is_leader: boolean | null
  // Multi-sector tags (filtered by display criteria)
  sectors: string[]
  // Whether today's latest snapshot is a limit-up / limit-down（权威标志）
  today_is_limit_up: boolean
  today_is_one_word_limit_up?: boolean
  today_is_one_word_limit_down?: boolean
  today_is_limit_down: boolean
  // From latest snapshot
  today_pct_change: number | null
  today_board_count: number | null        // 连续涨停数
  today_limit_down_count: number | null   // 连续跌停数
  // 上一交易日是否涨/跌停（一致性强、需谨慎）
  yesterday_is_limit_up: boolean
  yesterday_is_limit_down: boolean
  // 距「涨幅严重异动」近似上涨空间 %（还需累计涨多少触发；已触发/数据不足为 null）
  severe_up_room: number | null
}

export interface StockSnapshot {
  id: number
  stock_id: number
  date: string
  open_price: number | null
  close_price: number | null
  high_price: number | null
  low_price: number | null
  volume: number | null
  turnover_rate: number | null
  pct_change: number | null
  is_limit_up: boolean
  is_limit_down: boolean
  is_broken_board: boolean
  board_count: number
  board_count_60d: number
  board_down_count_60d: number
  limit_up_days_60d: number
  limit_up_days_20d: number
  limit_up_days_10d: number
  top_10_pct_change_20d: boolean
  phase: string | null
  leader_score: number
  risk_score: number
  emotion_score: number
  is_weak_to_strong: boolean
}

export interface StockListResponse {
  items: Stock[]
  total: number
  page: number
  page_size: number
}

// ─── Sector ──────────────────────────────────────────────────────────────────

export interface StockInSector {
  id: number
  code: string
  name: string
  is_leader: boolean
  is_core: boolean
  is_compensation: boolean
  leader_score: number
  risk_score: number
  phase: string | null
}

export interface Sector {
  id: number
  code: string
  name: string
  description: string | null
  phase: number
  phase_label: string | null
  phase_label_zh: string | null
  strong_stock_count: number
  limit_up_count: number
  limit_down_count: number
  one_word_up_count?: number    // 当日一字板涨停数
  one_word_down_count?: number  // 当日一字板跌停数
  board_height: number
  continuity_score: number
  risk_score: number
  emotion_score: number
  sector_type: string | null
  stock_count: number
  pct_change_30d: number   // 今日涨幅（legacy 字段名）
  pct_change_5d: number
  pct_change_10d: number
  pct_change_20d: number
  pct_change_60d: number
  amount: number
  is_watched: boolean
  leader_stock_id: number | null
  leader_stock_name: string | null
  leader_stock_code: string | null
  rank_5d: number | null
  rank_10d: number | null
  rank_20d: number | null
  rank_60d: number | null
  rank_lu: number | null
  rank_board: number | null
  rank_strong: number | null
  stocks: StockInSector[]
  created_at: string | null
  updated_at: string | null
}

export interface SectorSnapshot {
  id: number
  sector_id: number
  date: string
  phase: number
  strong_stock_count: number
  limit_up_count: number
  board_height: number
  continuity_score: number
  risk_score: number
  emotion_score: number
}

export interface SectorListResponse {
  items: Sector[]
  total: number
}

// ─── Signal ───────────────────────────────────────────────────────────────────

export type RiskLevel = 'low' | 'medium' | 'high'
export type SuggestedAction =
  | 'observe'
  | 'watchlist'
  | 'low_position_trial'
  | 'hold'
  | 'reduce'
  | 'avoid'

// ─── Review ───────────────────────────────────────────────────────────────────

export interface DailyReview {
  id: number
  date: string
  market_phase: string | null
  profit_effect_score: number
  loss_effect_score: number
  emotion_cycle: string | null
  emotional_temperature: number
  suggested_position_level: number
  strong_sectors: string[] | null
  dangerous_sectors: string[] | null
  active_sectors: string[] | null
  dragon_changes: unknown[] | null
  tomorrow_watchlist: string[] | null
  market_summary: string | null
  created_at: string | null
  updated_at: string | null
}

export interface DailyReviewListResponse {
  items: DailyReview[]
  total: number
}

// ─── Market State ────────────────────────────────────────────────────────────

export interface DragonLeader {
  stock_code: string
  stock_name: string
  sector_name: string
  leader_type: string
  board_height: number
  leader_score: number
  risk_score: number
}

export interface WeakToStrongCandidate {
  stock_code: string
  stock_name: string
  sector_name: string
  confidence_score: number
  risk_level: RiskLevel
  signal_type: string
  suggested_action: SuggestedAction
  explanation: string
}

export interface ActiveSector {
  sector_code: string
  sector_name: string
  phase: number
  phase_label: string
  emotion_score: number
  strong_stock_count: number
  board_height: number
}

export interface MarketState {
  date: string
  market_phase: string
  profit_effect_score: number
  loss_effect_score: number
  emotion_cycle: string
  emotional_temperature: number
  suggested_position_level: number
  active_sectors: ActiveSector[]
  dangerous_sectors: string[]
  strong_sectors: string[]
  dragon_leaders: DragonLeader[]
  weak_to_strong_candidates: WeakToStrongCandidate[]
}

export interface LimitMoveTrendPoint {
  date: string
  limit_up_count: number
  limit_down_count: number
  top_up_sector: string | null
  top_up_sector_count: number | null
  top_down_sector: string | null
  top_down_sector_count: number | null
}

export interface SectorLimitTrendPoint {
  date: string
  limit_up_count: number
  limit_down_count: number
}

export interface SectorLimitTrendOption {
  name: string
  limit_up_total: number
  limit_down_total: number
}

// ─── 交易复盘日志 ─────────────────────────────────────────────────────────────

export type TradeAction = '买入' | '卖出'  // 加仓/减仓/清仓由系统按持仓状态推导
export type EmotionTag = '计划内' | '抄底做T' | '逆势加仓' | '回本补救' | '追高' | '其他'
export type ExitReason = '止损' | '恐慌' | '反弹跑' | '目标达成' | '其他'

export interface TradeJournalEntry {
  id: number
  stock_code: string
  stock_name: string | null
  action: TradeAction
  trade_time: string
  price: number
  position_pct: number | null
  reason: string | null
  planned_stop: number | null
  target: number | null
  emotion_tag: EmotionTag | null
  note: string | null
  exit_reason: ExitReason | null
  realized_pnl: number | null
  pnl_pct: number | null
  mkt_temperature: number | null
  mkt_phase: string | null
  mkt_suggested_position: number | null
  created_at: string | null
}

export interface TradeJournalList {
  items: TradeJournalEntry[]
  total: number
  realized_pnl_sum: number
  win_count: number
  loss_count: number
}

// ─── 大盘趋势分析 ─────────────────────────────────────────────────────────────

export interface IndexTrendPoint {
  date: string
  close: number
  open: number | null
  high: number | null
  low: number | null
  volume: number | null
  amount: number | null
  ma5: number | null
  ma10: number | null
  ma20: number | null
  ma60: number | null
  ma120: number | null
  ma250: number | null
}

export interface IndexSignal {
  date: string
  kind: string
  label: string
  side: 'bull' | 'bear' | 'warn'
}

export interface IndexTrendAnalysis {
  code: string
  name: string
  close: number
  pct_change: number
  pct_5d: number
  pct_20d: number
  score: number
  state: 'strong' | 'bullish' | 'range' | 'bearish' | 'weak'
  state_label: string
  alignment: 'bull' | 'bear' | 'mixed'
  above_ma5: boolean
  above_ma10: boolean
  above_ma20: boolean
  above_ma60: boolean
  ma20_slope_pct: number
  ma60_slope_pct: number
  bias20: number
  signals: IndexSignal[]
  series: IndexTrendPoint[]
}

export interface MarketTrendResponse {
  updated_at: string
  indices: IndexTrendAnalysis[]
  errors: string[]
}

export interface MarginPoint {
  date: string
  balance: number
  net_buy: number
  szzs_close: number
  szzs_pe: number | null
  kc50_pe: number | null   // 科创50市盈率（2026-08-24新增）
  bz50_pe: number | null   // 北证50市盈率（2026-08-24新增）
}

export type MarginRange = '6m' | '1y' | '3y' | '5y' | 'all'

export interface WindvaneResponse {
  updated_at: string
  margin: {
    latest_date: string
    balance: number
    net_buy: number
    series: MarginPoint[]
  } | null
  updown: {
    date: string
    up: number
    down: number
    flat: number
    limit_up: number
    limit_down: number
    natural_limit_up: number
    natural_limit_down: number
    up_buckets: number[]
    down_buckets: number[]
  } | null
  turnover: {
    today: number
    prev: number
    avg60: number
    series: { date: string; amount: number }[]
    intraday_date: string | null
    intraday_amount: number | null
    intraday_estimate: number | null
    is_trading: boolean
  } | null
  errors: string[]
}

export interface MarketHistoryPoint {
  date: string
  profit_effect_score: number
  loss_effect_score: number
  strong_pool_avg_pct: number | null
  profit_effect_groups: ProfitEffectGroup[] | null
  emotional_temperature: number
  suggested_position_level: number
  market_phase: string | null
}

// ─── Profit Effect ────────────────────────────────────────────────────────────

export interface ProfitEffectGroup {
  key: string    // "limit_up" | "oscillation" | "weakening" | "broken"
  label: string
  stock_count: number
  avg_pct: number
  up_count: number
  down_count: number
  flat_count: number
}

export interface SectorProfitEffect {
  sector_code: string
  sector_name: string
  stock_count: number
  up_count: number
  down_count: number
  avg_pct: number
  sector_pct_today: number
}

export interface ProfitEffectData {
  date: string
  has_data: boolean
  overall_avg_pct: number
  overall_up_count: number
  overall_down_count: number
  overall_flat_count: number
  overall_limit_up_count: number
  overall_limit_down_count: number
  groups: ProfitEffectGroup[]
  sectors: SectorProfitEffect[]
}

// ─── Market Effect（每日赚钱效应 / 亏钱效应，精简 MVP）────────────────────────

export type CohortType = 'limit_up' | 'first_board' | 'multi_board' | 'limit_down' | 'broken_board' | 'strong_proxy'

export interface CohortOutcome {
  cohort_type: CohortType
  label: string
  member_count: number
  valid_count: number
  median_pct_change: number | null
  red_ratio: number | null
  large_loss_ratio: number | null
  advance_ratio: number | null
  broken_ratio: number | null
}

export interface EvidenceItem {
  metric: string
  raw_value: unknown
  sample_size: number
  direction: 'positive' | 'negative'
}

export interface MarketEffectDailyResponse {
  trade_date: string
  profit_strength: number
  loss_strength: number
  quadrant: 'benign_spread' | 'strong_divergence' | 'quiet_chaos' | 'loss_spread'
  lifecycle_state: 'loss_spreading' | 'recovering' | 'profit_confirmed' | 'profit_spreading' | 'loss_warning'
  breadth_source: 'full_market' | 'tracked_pool'
  coverage_ratio: number
  cohorts: Record<CohortType, CohortOutcome>
  evidence: EvidenceItem[]
  summary: string
  formula_version: string
}

export interface MarketEffectHistoryPoint {
  trade_date: string
  profit_strength: number
  loss_strength: number
  quadrant: string
  lifecycle_state: string
  breadth_source: 'full_market' | 'tracked_pool'
}

export interface CohortMember {
  code: string | null
  name: string | null
  board_count_before: number | null
  outcome_pct_change: number | null
  outcome_board_count: number | null
  has_outcome: boolean
}

export interface TurnoverStock {
  code: string
  name: string
  rank: number
  amount: number             // 成交额（元）
  pct_change: number
  turnover_rate: number | null
  sector_name: string | null
  market: string | null
  is_new: boolean
}

export interface TurnoverSectorGroup {
  name: string
  count: number
  avg_pct_change: number
  up_count: number
  down_count: number
  flat_count: number
  total_amount: number
  new_count: number
  bias: 'bullish' | 'bearish' | 'mixed'
}

export interface TurnoverOverviewResponse {
  date: string | null
  stocks: TurnoverStock[]
  sector_groups: TurnoverSectorGroup[]
  total_amount: number
  new_count: number
  overall_avg_pct: number
  errors: string[]
}

// ─── 弱转强雷达 ───────────────────────────────────────────────────────────────

export type W2SState = 'WATCH' | 'READY' | 'REPAIRING' | 'CONFIRMING' | 'BUYABLE' | 'WAIT' | 'BLOCK'
// 底层结构态（w2s_state_machine.py 的 STRUCTURE_STATES），跟上面展示态 W2SState
// 是两套独立枚举——结构态没有 CONFIRMING/BUYABLE/WAIT/BLOCK，多了展示态没有的
// PULLBACK/CONFIRMED/FAILED。此前误共用 W2SState 类型，这里拆开（round4 review 指出）。
export type W2SStructuralState = 'WATCH' | 'READY' | 'REPAIRING' | 'PULLBACK' | 'CONFIRMED' | 'FAILED'
export type W2SLeaderType = 'core' | 'backup' | 'undetermined' | 'non_leader'
export type W2SSectorCategory =
  | 'NEW_START' | 'EXPANDING' | 'MAIN_UPTREND' | 'HEALTHY_DIVERGENCE'
  | 'HIGH_LEVEL_WARNING' | 'DECLINING' | 'DEAD'
export type W2SRegulatoryRisk = 'LOW' | 'MEDIUM' | 'HIGH' | 'EXTREME'

export interface W2SCandidate {
  stock_code: string
  stock_name: string
  first_seen_date: string
  last_seen_date: string
  consecutive_miss_days: number
  candidate_source: string
  is_active: boolean

  sector_id: number | null
  sector_name: string | null
  sector_category: W2SSectorCategory | null
  sector_strength_score: number | null
  sector_momentum_score: number | null
  sector_divergence_health: number | null  // 仅 phase=4（分歧阶段）有值，越低代表板块高位分歧越危险
  is_mainline_sector: boolean  // 是否在当前MAIN_UPTREND强度前N名，不在则结构确认封顶CONFIRMING

  leader_type: W2SLeaderType | null
  leader_rank: number | null
  leader_score: number | null

  current_state: W2SState
  structural_state: W2SStructuralState  // 底层结构态，不受闸门覆盖；跟 current_state 不同时代表"结构已推进但被临时闸门挡住"
  setup_substate: string | null
  setup_type: string  // GENERIC，弱转强分型占位字段
  refresh_sample_count: number

  price: number | null
  prev_close: number | null
  vwap: number | null
  ma5: number | null
  day_open: number | null
  day_high: number | null
  day_low: number | null
  day_amount: number | null
  turnover_rate: number | null

  auction_gap: number | null
  limit_price: number | null
  limit_room: number | null

  technical_stop: number | null
  standard_stop: number | null
  stress_stop: number | null
  stress_rr: number | null

  regulatory_risk_level: W2SRegulatoryRisk | null
  signal_enabled: boolean

  trigger_reasons: string | null
  block_reasons: string | null

  last_refreshed_at: string | null
  formula_version: string
}

export interface W2SChecklistGroup {
  group: 'MARKET' | 'SECTOR' | 'LEADER' | 'DIVERGENCE' | 'SETUP' | 'SPACE' | 'CHIPS' | 'RISK'
  // advisory：该组在真实闸门逻辑里从不硬拦截/软上限，仅供参考，不能显示成
  // fail（会跟候选实际已放行的展示态自相矛盾）——round4 review 指出的真实bug。
  status: 'pass' | 'fail' | 'phase2' | 'advisory'
  detail: string
}

export interface W2SCandidateDetail extends W2SCandidate {
  checklist: W2SChecklistGroup[]
}

export interface W2SRefreshResult {
  refreshed: number
  state_changed: number
  quote_missing: number
  duration_ms: number
  triggered_at: string
}

export interface W2SRefreshStatus {
  running: boolean
  last_result: W2SRefreshResult | null
  last_error: string | null
}

export interface W2SEvent {
  id: number
  timestamp: string
  stock_code: string
  old_state: W2SState | null
  new_state: W2SState
  trigger_reasons: string | null
  block_reasons: string | null
  sector_phase: string | null
  leader_type: string | null
  price: number | null
  structural_state: string | null
  recovery_high: number | null
  pullback_low: number | null
  formula_version: string
}

export interface W2SSnapshot {
  id: number
  trade_date: string
  timestamp: string
  stock_code: string
  price: number | null
  high: number | null
  low: number | null
  amount: number | null
  volume: number | null
  vwap: number | null
  structural_state: string | null
  recovery_high: number | null
  pullback_low: number | null
}

export interface W2SConfig {
  prompt1: string
  prompt2: string
  is_prompt1_custom: boolean
  is_prompt2_custom: boolean
  default_prompt1: string
  default_prompt2: string
  w2s_min_yesterday_amount: number
  w2s_leader_gap_threshold: number
  w2s_observation_window_days: number
  w2s_divergence_health_threshold: number
  w2s_auction_gap_min: number
  w2s_space_min_room_pct: number
  w2s_pullback_min_pct: number
  w2s_sector_gate_allowed: string
  w2s_regulatory_risk_cap: string
  w2s_market_gate_blocked: string
  w2s_formula_version: string
}

export type W2SMarketState = 'GREEN' | 'YELLOW' | 'ORANGE' | 'RED'

export interface W2SMarketGate {
  trend_score: number | null
  risk_score: number | null
  market_state: W2SMarketState
  index_scores: Record<string, number>
  market_effect_date: string | null        // 风险偏好分里"冻结群体反馈"取自哪个交易日
  market_effect_confidence: 'NORMAL' | 'LOW'  // LOW=当日市场效应退化为跟踪池近似广度
  market_effect_profit_strength: number | null
  market_effect_loss_strength: number | null
  market_negative_feedback: 'LOW' | 'MEDIUM' | 'HIGH' | 'UNKNOWN'  // loss_strength的显式分级，不再只藏在risk_score里
  as_of_date: string | null       // 沿用旧字段名，语义=breadth_as_of
  trend_as_of: string | null      // 指数趋势数据算出来是哪天（2026-08-24新增）
  breadth_as_of: string | null    // 涨跌家数广度数据算出来是哪天，跟as_of_date同一个值
}

// "今日主线"摘要（2026-08-24新增）：板块视角，不依赖任何W2S候选是否命中，
// 0~3个是上限不是配额——没有就是空数组，前端不该为了凑数硬显示。
export interface W2SMainlineSector {
  sector_id: number
  sector_code: string
  sector_name: string
  rank: number
  sector_category: W2SSectorCategory
  sector_strength_score: number
  sector_momentum_score: number | null
  sector_divergence_health: number | null
}

export interface W2SMainlines {
  mainlines: W2SMainlineSector[]
  data_as_of: string | null  // 板块数据实际计算自哪天，过期要显眼提示，不能包装成实时
}

// ─── 涨停板块雷达（2026-08-25新增）────────────────────────────────────────────
// 这个页面回答的不是"今天有哪些股票涨停"，而是"资金今天在哪些板块形成集团进攻，
// 板块核心是谁，老核心和新涨停有没有共振"。所以输出单位是板块，不是个股。

export type W2SCoreRole =
  | 'CURRENT_CORE'     // 近10日还在涨停 —— 当前正在起作用的核心
  | 'RECENT_CORE'      // 近20日活跃 / 打出过高连板
  | 'HISTORICAL_CORE'  // 只有60日窗口才够得着 —— 历史核心/情绪锚
  | 'SECTOR_LEADER'
  | 'SECTOR_CORE'

export interface LimitUpBoardLadderEntry {
  board: number
  count: number
}

/** 板块核心锚：今天**没有**涨停，但历史上有足够市场辨识度，不能从视野里消失 */
export interface LimitUpRadarCoreStock {
  code: string
  name: string
  core_roles: W2SCoreRole[]
  core_reasons: string[]        // 可解释理由，如"近60日涨停6次"
  primary_role: W2SCoreRole | null
  pct_change: number | null     // 今日涨跌幅 —— 判断老核心正/负反馈的关键
  limit_up_days_10d: number | null
  limit_up_days_20d: number | null
  limit_up_days_60d: number | null
  board_count_60d: number | null
  leader_score: number | null
  is_broken_today: boolean
}

/** 今日涨停股。core_roles 非空表示它同时也是板块核心（最强的共振信号） */
export interface LimitUpRadarTodayStock {
  code: string
  name: string
  board_count: number | null
  limit_stat_days: number | null
  limit_stat_count: number | null
  first_limit_time: string | null   // 首次封板
  last_limit_time: string | null    // 最终封板（≠首封时才有意义）
  seal_amount: number | null        // 封单额（元）；null=东财没给，不是0
  broken_times: number | null
  pct_change: number | null
  price: number | null
  turnover_rate: number | null
  limit_reason: string | null       // 催化剂，不是板块归属
  limit_content: string | null
  limit_up_days_10d: number | null
  core_roles: W2SCoreRole[]
  core_reasons: string[]
}

export interface LimitUpRadarSector {
  sector_id: number
  sector_name: string
  sector_phase: number | null
  today_limit_up_count: number
  continuation_count: number
  first_board_count: number
  board_height: number
  board_ladder: LimitUpBoardLadderEntry[]
  broken_count: number
  seal_rate: number | null
  earliest_limit_time: string | null
  total_seal_amount: number | null   // null=没有任何一只给了封单额
  seal_amount_known_count: number
  core_count: number            // 召回到的真实总数
  core_shown_count: number      // 实际返回条数（展示截断）
  core_pct_known_count: number  // 其中有当日涨跌幅的只数（0=当日数据还没更新）
  core_avg_pct_change: number | null // 核心锚今日平均涨跌幅
  core_stocks: LimitUpRadarCoreStock[]
  today_limit_up_stocks: LimitUpRadarTodayStock[]
}

export interface LimitUpRadarSummary {
  limit_up_count: number
  continuation_count: number
  first_board_count: number
  board_height: number
  broken_count: number
  seal_rate: number | null
  active_sector_count: number
}

export interface LimitUpRadarResponse {
  trade_date: string | null
  refreshed_at: string | null   // 数据新鲜度三件套，盘中不能让用户误以为是实时
  source: string | null
  summary: LimitUpRadarSummary
  sectors: LimitUpRadarSector[]
  warnings: string[]
}

export interface LimitUpRadarRefreshResponse {
  ok: boolean
  trade_date: string | null
  limit_up_written: number
  broken_written: number
  refreshed_at: string | null
  error: string | null
  last_success_at: string | null
}
