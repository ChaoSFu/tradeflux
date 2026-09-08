import client from './client'
import type {
  StockListResponse, StockSnapshot, Stock, LimitMoveTrendPoint,
  SectorLimitTrendPoint, SectorLimitTrendOption,
} from '@/types'

export const fetchStocks = (params?: {
  page?: number
  page_size?: number
  in_strong_pool?: boolean
  sector_id?: number
  search?: string
  codes?: string
}) => client.get<StockListResponse>('/stocks', { params }).then((r) => r.data)

// 按代码批量精确查询（忽略分页），用于用其他选股口径的成员补全强势股字段
export const fetchStocksByCodes = (codes: string[]) =>
  codes.length
    ? client.get<StockListResponse>('/stocks', { params: { codes: codes.join(',') } }).then((r) => r.data)
    : Promise.resolve({ items: [], total: 0, page: 1, page_size: 0 } as StockListResponse)

export const fetchStrongPool = (params?: {
  page?: number
  page_size?: number
  sector_id?: number
  phase?: string
  search?: string
  sort_by?: string
  sort_order?: string
}) => client.get<StockListResponse>('/stocks/strong-pool', { params }).then((r) => r.data)

export const fetchLimitMoves = (params?: {
  page?: number
  page_size?: number
  search?: string
  move_type?: 'limit_up' | 'limit_down'
  date?: string   // 历史交易日 YYYY-MM-DD，不传=最新
}) => client.get<StockListResponse>('/stocks/limit-moves', { params }).then((r) => r.data)

export const fetchLimitMovesTrend = (days = 20) =>
  client.get<LimitMoveTrendPoint[]>('/stocks/limit-moves/trend', { params: { days } }).then((r) => r.data)

export const fetchSectorLimitTrendOptions = (days = 30) =>
  client.get<SectorLimitTrendOption[]>('/stocks/limit-moves/trend/sector-options', { params: { days } }).then((r) => r.data)

export const fetchSectorLimitTrend = (sector: string, days = 30) =>
  client.get<SectorLimitTrendPoint[]>('/stocks/limit-moves/trend/sector', { params: { sector, days } }).then((r) => r.data)

export const fetchStock = (code: string) =>
  client.get<Stock>(`/stocks/${code}`).then((r) => r.data)

export const fetchStockSnapshots = (code: string, days = 30) =>
  client.get<StockSnapshot[]>(`/stocks/${code}/snapshots`, { params: { days } }).then((r) => r.data)

// ── 高标龙头生命周期（事实层，2026-09-04）────────────────────────────────────
export type LifecycleState =
  | 'STREAKING' | 'BROKEN' | 'REPAIRING' | 'CROSS_SUCCESS'
  | 'CROSS_WEAKENING' | 'CROSS_FAILED' | 'FADED'
  // 今天的价格事实不够判断
  | 'UNKNOWN'
  // 价格事实齐全，但本地重算不出 >=4 连板周期。跟 UNKNOWN 是两回事，
  // 看数的人要能知道去查哪个
  | 'NO_CYCLE'

export interface LeaderCycleItem {
  code: string
  name: string | null
  sector_name: string | null
  peak_board_count: number | null   // 本轮周期最高连板
  board_count_60d: number | null    // 60日最高（历史辨识度，跟本轮分开看）
  cycle_start_date: string | null
  cycle_peak_date: string | null
  break_date: string | null         // null = 仍在连板中
  days_since_break: number | null
  peak_price: number | null
  post_break_high: number | null
  post_break_low: number | null
  latest_close: number | null
  peak_drawdown: number | null
  ma5: number | null; ma10: number | null; ma20: number | null; ma30: number | null
  ma_window_complete: boolean | null
  rs_market_10: number | null; rs_market_20: number | null; rs_market_60: number | null
  rs_sector_10: number | null; rs_sector_20: number | null; rs_sector_60: number | null
  volume: number | null; amount: number | null; turnover_rate: number | null
  // 变化速度：截面数字答不了「正在变强」还是「已经强了很久」。
  // RS20=+12 是从 -5 爬上来还是从 +30 掉下来的，含义完全相反
  rs_market_20_delta_1d: number | null
  rs_market_20_delta_3d: number | null
  rs_sector_20_delta_1d: number | null
  dist_to_post_break_high: number | null   // 离断板后阶段高点还有几 %
  dist_to_cycle_peak: number | null        // 离原周期顶还有几 %
  // 三态：true=创了 / false=比过没创 / null=没有可比的历史（断板当天）
  new_post_break_high_today: boolean | null
  new_post_break_low_today: boolean | null
  volume_ratio_5d: number | null
  amount_ratio_5d: number | null
  // ── Price Lifecycle v1（后端 replay 出来的派生状态，不落库）──────────
  lifecycle_state: LifecycleState | null
  previous_lifecycle_state: LifecycleState | null
  // 最近一次判得出的状态。盘前更新时 lifecycle_state 是 UNKNOWN（不能用盘中价
  // 推动跨日状态），界面这时显示它，并标明截至哪天
  last_valid_state: LifecycleState | null
  last_valid_date: string | null
  // 在当前状态里待了几个交易日（转入当天 = 0）。按交易日历数，不数快照行数
  days_in_state: number | null
  state_since_date: string | null
  transitioned_today: boolean
  lifecycle_formula_version: string | null
  transition_reason_codes: string[]
  transition_reasons: string[]
  // 当初为什么进入当前状态。状态可能持续几十天，人想知道的是"它为什么在这儿"
  entry_reason_codes: string[]
  entry_reasons: string[]
  evaluation_status: string | null
  // CROSS_WEAKENING 要能跟 CROSS_FAILED 分开：前者曾经成功过，后者没有
  ever_cross_success: boolean
  first_cross_success_date: string | null

  bar_count: number | null
  // data_fresh = 那根 bar 是不是今天的；bar_settled = 那根 bar 是不是收盘终值。
  // 盘中两者不同步，必须分开——腾讯盘中就发当日 bar
  data_fresh: boolean | null
  bar_settled: boolean | null
  latest_bar_date: string | null
  missing_days: number | null
  peak_board_confident: boolean | null
}

/** 在强势池里、但本地识别不出 >=4 连板周期的股票——不能让它们静默消失 */
export interface UnresolvedLeader {
  code: string
  name: string | null
  board_count_60d: number | null
  reason: string
}

export interface LeaderCycleResponse {
  trade_date: string | null
  running: LeaderCycleItem[]
  broken: LeaderCycleItem[]
  unresolved: UnresolvedLeader[]
  coverage: Record<string, number>
  scope_note: string
}

export const fetchLeaderCycle = (tradeDate?: string) =>
  client.get<LeaderCycleResponse>('/leader-cycle',
    { params: tradeDate ? { trade_date: tradeDate } : {} }).then((r) => r.data)

// ─── 生命周期口径的赚钱效应 ──────────────────────────────────────────────
// 强势股概览原来那四张卡按 Stock.phase 分组，那只是"收盘价在哪条均线下面"的
// 单日快照——一只刚断板正在修复的票和一只连跌十天的老龙都可能被叫"震荡龙头"。
export interface LifecycleCohort {
  state: LifecycleState
  count: number
  /**
   * 去掉一个最高、一个最低之后的均值。
   *
   * 中位数只看中间那一两只，其余涨跌不进结果；裸均值一只涨停就能把 5 只的组
   * 拽红。截尾均值两头都挡一下。
   *
   * **样本 <3 时为 null**——去掉两端就没剩下了。这时不退回裸均值：同一列里
   * 混两种口径，看的人分不出哪个是哪个。
   */
  trimmed_avg_pct_change: number | null
  red_ratio: number
}

export interface LifecycleForward {
  state: LifecycleState
  t1: number | null; t1_n: number; t1_win: number | null
  t3: number | null; t3_n: number; t3_win: number | null
  t5: number | null; t5_n: number; t5_win: number | null
}

/** 某一天、某个状态的逐日赚钱效应。**avg 是均值,不是中位数** */
export interface LifecycleSeriesPoint {
  trade_date: string
  /** state → { avg, n }。当天没有该状态的票时这个 key 就不存在（不是 0） */
  values: Record<string, { avg: number; n: number }>
}

export interface LifecycleEffectResponse {
  as_of: string | null
  prev: string | null
  formula_version: string
  cohorts: LifecycleCohort[]
  /** 逐日：昨天处于某状态的票，今天的平均涨幅 */
  series: LifecycleSeriesPoint[]
  /** 历史前瞻。**只是线索不是结论**——没做同日同池对照，也没有置信区间 */
  history: LifecycleForward[]
  notes: string[]
}

export const fetchLifecycleEffect = () =>
  client.get<LifecycleEffectResponse>('/leader-cycle/effect').then((r) => r.data)

// ─── 转移时点的前瞻证据（离线产物）─────────────────────────────────────────
// 上面那份 history 测的是「**处于**某状态期间怎么走」，这份测的是「**转入**某
// 状态那一天之后怎么走」，而且做了同日同池对照和 bootstrap 区间。两者可以方向
// 相反，那不是矛盾——问的本来就不是同一件事。
//
// 由 scripts/evaluate_lifecycle.py --json 离线生成。不实时算：那套评估要把每只
// 票的每个交易日 replay 一遍，再整段重抽 1000 次。
export interface EvidenceCell {
  median: number
  n: number
  /** 超过同日同池中位数的比例。50% 附近 = 跟随机没区别 */
  pos_rate?: number
  /** 按「股票×周期」整段重抽的 95% 区间。**算不出来就是 null，不给假区间** */
  ci?: [number, number] | null
  /** 跨 0 = 方向可能是噪声，不管中位数看起来多好看 */
  crosses_zero?: boolean | null
}

export interface EvidenceEvent {
  /** 形如 "BROKEN→REPAIRING" */
  event: string
  from: LifecycleState | null
  to: LifecycleState | null
  n_events: number
  /** key 是 horizon 的字符串形式（"1"/"3"/"5"/"10"）。**没样本是 null，不是 0** */
  excess: Record<string, EvidenceCell | null>
  excess_balanced: Record<string, EvidenceCell | null> | null
  /** 次日开盘买入。T+1 那格 = 开盘买、当天收盘卖，最贴近实际操作 */
  exec_excess: Record<string, EvidenceCell | null>
  mfe5: EvidenceCell | null
  mae5: EvidenceCell | null
}

export interface LifecycleEvidence {
  /** false = 还没跑过 / 产物读不出来。**这跟「跑过了但没有证据」是两件事** */
  available: boolean
  reason?: string
  path?: string
  formula_version?: string
  current_formula_version?: string
  /** 产物的口径跟当前代码对不上——旧证据不再对应现在的规则 */
  stale_formula?: boolean
  generated_at?: string
  file_mtime?: string
  as_of?: string
  horizons?: number[]
  skipped_incomplete?: number
  events?: EvidenceEvent[]
  baseline?: EvidenceEvent | null
  /** 免责声明跟数字一起走——数字会被复制到界面上，注意事项不会 */
  caveats?: string[]
}

export const fetchLifecycleEvidence = () =>
  client.get<LifecycleEvidence>('/leader-cycle/evidence').then((r) => r.data)

// ─── 涨跌停分析 Phase 2：两个跨日统计 ───────────────────────────────────────
// **只有计数和比率，没有接力分/延续分。** 多个事实加权成一个总分就是又一个
// 说不清口径的黑箱，这一层刻意不做。
export interface AdvanceLadderRow {
  from_board: number
  to_board: number
  /** 昨天该板位的涨停股总数 */
  previous_count: number
  /** 其中今天有快照的（= advanced + broken）。比率的分母是它 */
  observed_count: number
  advanced_count: number
  broken_count: number
  /** 今天没有这只票的行（停牌 / 退市 / 未抓到）。**既不是晋级也不是断板** */
  unknown_count: number
  /** 一只都没观测到时是 null —— 算不出就是算不出，不是 0% */
  advance_ratio: number | null
}

export interface AdvanceLadderResponse {
  trade_date: string | null
  /** 交易日历上的前一个交易日。null = 拿不到日历，整份结果为空 */
  prev_date: string | null
  rows: AdvanceLadderRow[]
  notes: string[]
}

export interface SectorContinuationRow {
  sector_id: number
  sector_name: string
  yesterday_limit_up_count: number
  today_continued_limit_up_count: number
  today_new_limit_up_count: number
  today_broken_count: number
  today_unknown_count: number
  today_limit_down_count: number
  continuation_ratio: number | null
}

export interface SectorContinuationResponse {
  trade_date: string | null
  prev_date: string | null
  rows: SectorContinuationRow[]
  notes: string[]
}

export const fetchAdvanceLadder = (date?: string) =>
  client.get<AdvanceLadderResponse>('/stocks/limit-moves/advance-ladder',
    { params: date ? { date } : {} }).then((r) => r.data)

export const fetchSectorContinuation = (date?: string) =>
  client.get<SectorContinuationResponse>('/stocks/limit-moves/sector-continuation',
    { params: date ? { date } : {} }).then((r) => r.data)
