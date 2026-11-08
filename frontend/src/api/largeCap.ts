import client from './client'

// 市值、成交额一律亿元；null = 没有这个数据，不是 0
export interface LargeCapBatch {
  id: number
  trade_date: string | null
  source: 'eastmoney_clist' | 'tencent_quote' | string
  started_at: string
  finished_at: string | null
  expected_count: number | null
  observed_count: number | null
  valid_market_cap_count: number | null
  pages_expected: number | null
  pages_ok: number | null
  is_complete: boolean
  is_settled: boolean | null
  applied: boolean
  status: 'ok' | 'incomplete' | 'failed' | 'intraday' | 'kept_existing' | string
  warnings: string[]
  error: string | null
}

export interface LargeCapGroupStats {
  count: number
  total_cap: number
  up: number
  flat: number
  down: number
  median_pct: number | null
  n_pct: number
  weighted_pct: number | null
  n_weighted: number
  /** 对加权涨幅贡献最大（按绝对值）的成员，单位百分点 */
  top_contributor: { code: string; name: string | null; pp: number } | null
  amount: number | null
  n_amount: number
  surge_multiple: number | null
  surge_volume_ratio: number
  median_5d: number | null
  n_5d: number
  median_10d: number | null
  n_10d: number
  median_20d: number | null
  n_20d: number
  median_60d: number | null
  n_60d: number
}

export interface LargeCapGroup extends LargeCapGroupStats {
  key: string
  name: string
  members: string[]
  entered: number | null
  exited: number | null
  watched?: boolean
}

export interface LargeCapStock {
  code: string
  name: string | null
  market: string | null
  /** 本系统股票库有没有这只（个股详情只收录进过候选池的股票） */
  has_detail: boolean
  listing_date: string | null
  total_cap: number | null
  float_cap: number | null
  prev_cap: number | null
  price: number | null
  pct: number | null
  pct_5d: number | null
  pct_10d: number | null
  pct_20d: number | null
  pct_60d: number | null
  amount: number | null
  turnover_rate: number | null
  volume_ratio: number | null
  amount_avg_20d: number | null
  amount_multiple: number | null
  industry_l1: string
  industry_l2: string
  concepts: string[]
}

export interface LargeCapCrossEvent {
  code: string
  name: string | null
  industry: string | null
  prev_cap: number
  cap: number
  cap_change_pct: number
  pct: number | null
  pct_5d: number | null
  pct_20d: number | null
  amount: number | null
  cap_price_mismatch: boolean
  has_detail: boolean
}

export interface LargeCapEvents {
  available: boolean
  /** 不可用时的原因（第一份快照 / 前一交易日缺快照 / 日历没覆盖） */
  reason: string | null
  entered: LargeCapCrossEvent[]
  exited: LargeCapCrossEvent[]
  first_observed: { code: string; name: string | null; industry: string | null; cap: number; pct: number | null; amount: number | null; reason: string; has_detail: boolean }[]
  missing_today: { code: string; name: string | null; industry: string | null; prev_cap: number; reason: string; has_detail: boolean }[]
  st_change: { code: string; name: string | null; change: string; has_detail: boolean }[]
}

export interface LargeCapConfig {
  threshold_yi: number
  surge_amount_multiple: number
  surge_volume_ratio: number
  history_days: 5 | 10 | 20 | 60
  include_st: boolean
  defaults: Omit<LargeCapConfig, 'defaults' | 'custom_keys' | 'threshold_presets'>
  custom_keys: string[]
  threshold_presets: number[]
}

interface OverviewBase {
  universe_scope: string
  threshold_yi: number
  config: LargeCapConfig
  latest_attempt: LargeCapBatch | null
  available_dates: string[]
}

export interface LargeCapOverviewOk extends OverviewBase {
  status: 'ok'
  date: string
  batch: LargeCapBatch | null
  is_stale: boolean
  pending_today: boolean
  expected_latest: string | null
  prev_date: string | null
  prev_available: boolean
  weight_basis: 'prev_cap' | 'current_cap'
  amount_history: { required: number; available: number }
  summary: LargeCapGroupStats & {
    entered: number | null
    exited: number | null
    first_observed: number | null
    unknown_cap: number
    universe_count: number
  }
  groups: { industry_l1: LargeCapGroup[]; industry_l2: LargeCapGroup[]; concept: LargeCapGroup[] }
  events: LargeCapEvents
  volume_surges: LargeCapStock[]
  stocks: LargeCapStock[]
  classification: {
    members: number
    with_concept: number
    concept_unknown: number
    with_industry_l1: number
    with_industry_l2: number
    note: string
  }
  /** 数据有问题、要人去看 */
  warnings: string[]
  /** 正常的状态说明（第一天、历史还在攒） */
  notes: string[]
}

export interface LargeCapOverviewEmpty extends OverviewBase {
  status: 'no_data'
  date: null
  message: string
}

export type LargeCapOverview = LargeCapOverviewOk | LargeCapOverviewEmpty

export const fetchLargeCapOverview = (params: { date?: string; threshold?: number }) =>
  client.get<LargeCapOverview>('/large-cap/overview', { params }).then((r) => r.data)

export const fetchLargeCapConfig = () =>
  client.get<LargeCapConfig>('/large-cap/config').then((r) => r.data)

export const updateLargeCapConfig = (payload: Partial<Pick<LargeCapConfig,
  'threshold_yi' | 'surge_amount_multiple' | 'surge_volume_ratio' | 'history_days' | 'include_st'>> & { reset?: boolean }) =>
  client.put<LargeCapConfig>('/large-cap/config', payload).then((r) => r.data)

export interface LargeCapSyncStatus {
  status: 'idle' | 'running' | 'done' | 'error'
  started_at: string | null
  finished_at: string | null
  exit_code: number | null
  log_lines: string[]
  recent_batches: LargeCapBatch[]
}

export const triggerLargeCapSync = () =>
  client.post<{ ok: boolean }>('/large-cap/sync').then((r) => r.data)

export const fetchLargeCapSyncStatus = () =>
  client.get<LargeCapSyncStatus>('/large-cap/sync/status').then((r) => r.data)
