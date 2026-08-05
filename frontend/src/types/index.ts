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

export interface Signal {
  id: number
  stock_id: number | null
  sector_id: number | null
  stock_code: string | null
  stock_name: string | null
  sector_name: string | null
  date: string
  signal_type: string
  confidence_score: number
  risk_level: RiskLevel
  explanation: string | null
  suggested_action: SuggestedAction
  is_active: boolean
  is_triggered: boolean
  created_at: string | null
}

export interface SignalListResponse {
  items: Signal[]
  total: number
  page: number
  page_size: number
}

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
}

export interface WindvaneResponse {
  updated_at: string
  margin: {
    latest_date: string
    balance: number
    net_buy: number
    series: MarginPoint[]
  } | null
  updown: {
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
