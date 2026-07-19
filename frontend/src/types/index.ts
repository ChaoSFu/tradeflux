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
