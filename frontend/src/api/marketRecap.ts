import client from './client'

// ─── 市场事实（按日期现算，每个模块都带数据状态）────────────────────────────

export type ModuleStatus = 'ok' | 'partial' | 'stale' | 'missing' | 'error'
export interface ModuleMeta {
  status: ModuleStatus
  trade_date: string | null
  source: string
  note: string | null
}

export interface IndexFact {
  code: string
  name: string
  status: ModuleStatus
  close?: number
  pct?: number | null
  ma5?: number | null
  ma10?: number | null
  ma20?: number | null
  ma60?: number | null
  arrangement?: '多头' | '空头' | '交织' | null
  ma20_dir?: 'up' | 'down' | 'flat' | null
  ma60_dir?: 'up' | 'down' | 'flat' | null
  above_ma20?: boolean | null
  above_ma60?: boolean | null
  r5?: number | null
  r20?: number | null
}

export interface CohortFact {
  cohort_type: string
  label: string
  member_count: number
  valid_count: number
  median_pct_change: number | null
  red_ratio: number | null
  advance_ratio: number | null
  broken_ratio: number | null
}

export interface LadderRow {
  from_board: number
  to_board: number
  observed_count: number
  advanced_count: number
  advance_ratio: number | null
}

export interface SectorFact {
  code: string
  name: string
  state: string
  state_label: string
  reason: string
  trail: { date: string; state: string }[]
  pct: number | null
  r5: number | null
  rs20: number | null
  lu: number | null
  height: number | null
  lu_trend: string | null
}

export interface Candidate {
  code: string
  name: string | null
  groups: ('board' | 'interval' | 'lifecycle' | 'turnover')[]
  watched: boolean
  pct: number | null
  is_limit_up: boolean | null
  board_count: number | null
  board_count_60d: number | null
  limit_stat: string | null
  limit_up_days_10d: number | null
  limit_up_days_20d: number | null
  pct_20d: number | null
  first_limit_time: string | null
  limit_reason: string | null
  lifecycle_state: string | null
  peak_board_count: number | null
  turnover_rank: number | null
  amount: number | null
  sector: string | null
  sector_basis: string | null
  regulatory: { status: string; reason: string | null; until: string | null } | null
  has_snapshot: boolean
}

export interface RecapContextOk {
  date: string
  prev_date: string | null
  status: 'ok'
  indices: { meta: ModuleMeta; items: IndexFact[] }
  breadth: {
    meta: ModuleMeta
    up_count?: number | null
    down_count?: number | null
    flat_count?: number | null
    limit_up_count?: number | null
    limit_down_count?: number | null
    broken_count?: number | null
    seal_rate?: number | null
  }
  amount: {
    meta: ModuleMeta
    amount?: number | null
    basis?: 'official' | 'intraday_last' | null
    prev_date?: string | null
    prev_amount?: number | null
    change_pct?: number | null
    avg60?: number | null
    avg60_n?: number
    vs_avg60_pct?: number | null
  }
  feedback: {
    meta: ModuleMeta
    prev_date?: string | null
    cohorts?: CohortFact[]
    ladder?: LadderRow[]
    ladder_notes?: string[]
    max_board?: number | null
    board_distribution?: Record<string, number> | null
  }
  sectors: { meta: ModuleMeta; state_date?: string | null; items?: SectorFact[] }
  candidates: { meta: ModuleMeta; items?: Candidate[] }
  data_status: Record<string, ModuleMeta>
}

export type RecapContext = RecapContextOk | { date: string; status: 'not_settled' | 'not_trading_day'; message: string }

// ─── 人工数据 ────────────────────────────────────────────────────────────────

export type Posture = 'attack' | 'probe' | 'defend' | 'unclear'
export type Scenario = 'A' | 'B' | 'C' | 'N'
export type SectorTier = 'core' | 'secondary' | 'drop'

export type ScenarioSet = Record<Scenario, Record<string, string | null>>
export interface Evidence { key: string; label: string; value: string }
export interface SectorPick { code: string; name: string; tier: SectorTier; state_label: string | null }

export interface Plan {
  id?: number
  stock_code: string
  stock_name: string | null
  leader_code: string | null
  sector_name: string | null
  scenario: Scenario | null
  reason: string | null
  relation_note?: string | null      // 关联依据（跟哪只高标、凭什么关联）
  watch_trigger?: string | null      // 观察触发：高标出现什么才去看它（不是买点）
  entry_trigger: string
  invalidation: string
  planned_stop: number | null
  risk_budget: number | null
  position_limit: number | null
  observe_until: string | null
  status: 'waiting' | 'triggered' | 'not_triggered' | 'invalid' | 'unverified'
}

export interface RecapDraft {
  market_posture: Posture | null
  market_evidence: Evidence[]
  evidence_note: string | null
  invalidation: string[]
  sector_picks: SectorPick[]
  scenarios: ScenarioSet & { by_leader?: Record<string, ScenarioSet> }
  flat_plan: boolean
  key_points?: string[]
  discovery: string | null
  mistake: string | null
  mistake_type: 'judgment' | 'execution' | 'strategy' | 'none' | null
  hypothesis: string | null
  deviations: unknown[]
  plans: Plan[]
}

export interface StockFacts {
  code: string
  name: string | null
  status: 'ok' | 'missing'
  sources: string[]
  pct: number | null
  pct_5d: number | null
  pct_20d: number | null
  close: number | null
  amount_yi: number | null
  is_limit_up: boolean | null
  board_count: number | null
  first_limit_time: string | null
  has_detail: boolean
}

export interface WatchItem {
  code: string
  name: string | null
  deep: boolean
  note: string | null
  added_date: string
  relations: number
  facts: StockFacts | null
}

export interface RecapSummary {
  key_points: string[]
  market: { posture: Posture | null; evidence: Evidence[]; evidence_note: string | null; invalidation: string[] }
  core: {
    core_sectors: SectorPick[]
    secondary_sectors: SectorPick[]
    watch_count: number
    deep_count: number
    relations: number
    responses_judged: number
    responses_pending: number
  }
  plan: { flat: boolean; count: number; complete: number }
  learning: { discovery: boolean; mistake: boolean; mistake_type: string | null; hypothesis: boolean }
}

export interface VersionMeta {
  version: number
  locked_at: string
  pre_open: boolean
  target_trade_date: string
  target_is_provisional: boolean
}

export interface VerificationPlanRow {
  stock_code: string
  stock_name: string | null
  scenario: Scenario | null
  entry_trigger: string
  invalidation: string
  planned_stop: number | null
  at_lock: { pct: number | null; close: number | null; is_limit_up: boolean | null; board_count: number | null } | null
  actual: { status: string; pct: number | null; close: number | null; is_limit_up: boolean | null; board_count: number | null; first_limit_time: string | null }
  below_stop: boolean | null
}

export interface Verification {
  status: 'no_lock' | 'pending' | 'ok'
  message?: string
  version?: { version: number; locked_at: string; pre_open: boolean }
  backfilled?: boolean
  target_trade_date?: string
  versions?: { version: number; locked_at: string; pre_open: boolean }[]
  market?: { posture: Posture | null; invalidation: string[]; indices: { code: string; name: string; status: string; pct: number | null; above_ma20: boolean | null; ma20_dir: string | null }[] }
  flat_plan?: boolean
  plans?: VerificationPlanRow[]
}

export interface RecapDay {
  recap: (Omit<RecapDraft, 'plans'> & { trade_date: string; status: 'draft' | 'locked'; updated_at: string }) | null
  plans: Plan[]
  watch_pool: WatchItem[]
  summary: RecapSummary
  locked_versions: VersionMeta[]
  prev_date: string | null
  prev_verification: Verification | null
}

export interface Relation {
  id: number
  leader_code: string
  leader_name: string | null
  attribute_type: AttributeType
  attribute_value: string
  related_code: string
  related_name: string | null
  relation_basis: 'A' | 'B' | 'C'
  evidence: string | null
  source: string
  confidence_status: 'unverified' | 'verified' | 'rejected'
  first_recorded: string
  last_verified: string | null
  note: string | null
  facts: StockFacts | null
  response: {
    response_status: ResponseStatus
    price_change: number | null
    limit_up: boolean | null
    leader_trigger_time: string | null
    first_trigger_time: string | null
    note: string | null
  } | null
}

export type AttributeType = 'industry' | 'concept' | 'event' | 'name' | 'structure' | 'history'
export type ResponseStatus = 'leading' | 'sync' | 'lagging' | 'no_response' | 'unknown' | 'pending'

export interface Suggestion {
  related_code: string
  related_name: string | null
  attribute_type: AttributeType
  attribute_value: string
  source: string
  evidence: string
}

export type SystemResponse = 'limit_up' | 'up_response' | 'independent_strong' | 'not_met' | 'insufficient'

export interface LeaderRelations {
  leader: { code: string; name: string | null; facts: StockFacts | null; events: string[] }
  relations: (Relation & { system_response: SystemResponse })[]
  suggestions: (Suggestion & { gram_matches?: Record<string, number> })[]
  summary: {
    stocks: number
    by_type: Partial<Record<AttributeType, number>>
    by_response: Partial<Record<SystemResponse, number>>
    response_labels: Record<SystemResponse, string>
    manual_pending: number
  }
}

// ─── 人气核心池 / 活跃事件（V2）────────────────────────────────────────────

export interface CoreSource { source: string; rank: number | null; value: number | string | null; scope: string }
export interface SourceMeta { status: 'ok' | 'partial' | 'missing'; scope: string; n: number; note: string | null }

export interface PopularCore {
  stock_code: string
  stock_name: string | null
  board: '主板' | '创业板' | '科创板'
  is_st?: boolean | null
  selection_sources: CoreSource[]
  pct_change?: number | null
  rank_5d?: number | null
  rank_10d?: number | null
  rank_20d?: number | null
  rank_60d?: number | null
  pct_change_5d?: number | null
  pct_change_10d?: number | null
  pct_change_20d?: number | null
  pct_change_60d?: number | null
  limit_up_days_10d?: number | null
  limit_up_days_20d?: number | null
  limit_up_days_60d?: number | null
  current_board?: number | null
  historical_max_board?: number | null
  turnover_rank?: number | null
  amount?: number | null
  limit_up_next_avg_pct?: number | null
  limit_up_next_samples?: number | null
  lifecycle_state?: string | null
  sector?: string | null
  sector_basis?: string | null
  data_status?: { missing: string[] } | null
  watched: boolean
  deep: boolean
  relations: number
  events: string[]
  views: ('all' | 'active' | 'historical' | 'repair' | 'mine')[]
}

export interface PopularCores {
  date: string
  rule_version: string
  rules: Record<string, number>
  frozen: boolean
  built_at: string | null
  sources: Record<string, SourceMeta>
  source_labels: Record<string, string>
  premium_is_current: boolean
  items: PopularCore[]
}

export interface ActivationEvent {
  leader_code: string
  leader_name: string | null
  event_type: string
  event_time: string | null
  evidence: string | null
  source: string | null
  data_status: string
}

export interface Activations {
  date: string
  frozen: boolean
  labels: Record<string, string>
  events: ActivationEvent[]
  not_supported: string[]
}

export interface Prepare {
  status: 'ok' | 'no_lock'
  message?: string
  recap_date?: string
  version?: number
  locked_at?: string
  pre_open?: boolean
  target_trade_date?: string
  backfilled?: boolean
  posture?: Posture | null
  invalidation?: string[]
  key_points?: string[]
  flat_plan?: boolean
  focus_leaders?: { code: string; name: string | null; deep: boolean; note: string | null }[]
  relations?: Record<string, { related_code: string; related_name: string | null; attribute_type: AttributeType; attribute_value: string; relation_basis: string; response: string | null }[]>
  events?: { leader_code: string; leader_name: string | null; event_type: string; evidence: string | null }[]
  scenarios?: ScenarioSet & { by_leader?: Record<string, ScenarioSet> }
  plans?: Plan[]
}

export const fetchPopularCores = (date: string) =>
  client.get<PopularCores>('/market-recap/popular-cores', { params: { date } }).then((r) => r.data)
export const fetchActivations = (date: string) =>
  client.get<Activations>('/market-recap/leader-activations', { params: { date } }).then((r) => r.data)
export const fetchPrepare = (date: string) =>
  client.get<Prepare>(`/market-recap/${date}/prepare`).then((r) => r.data)

export const fetchRecapDates = () =>
  client.get<{ date: string; recap_status: string | null; locked_versions: number }[]>('/market-recap/dates').then((r) => r.data)
export const fetchRecapContext = (date: string) =>
  client.get<RecapContext>('/market-recap/context', { params: { date } }).then((r) => r.data)
export const fetchRecapDay = (date: string) =>
  client.get<RecapDay>(`/market-recap/${date}`).then((r) => r.data)
export const saveRecap = (date: string, draft: RecapDraft) =>
  client.put(`/market-recap/${date}`, draft).then((r) => r.data)
export const lockRecap = (date: string) =>
  client.post<VersionMeta & { payload: unknown }>(`/market-recap/${date}/lock`).then((r) => r.data)
export const fetchLockedVersions = (date: string) =>
  client.get<(VersionMeta & { payload: LockedPayload })[]>(`/market-recap/${date}/locked`).then((r) => r.data)
export const fetchVerification = (date: string) =>
  client.get<Verification>(`/market-recap/${date}/verification`).then((r) => r.data)
export const addWatch = (body: { code: string; date: string; name?: string | null; deep?: boolean; note?: string }) =>
  client.post('/market-recap/watch', body).then((r) => r.data)
export const removeWatch = (code: string, date: string) =>
  client.delete(`/market-recap/watch/${code}`, { params: { date } }).then((r) => r.data)
export const fetchLeaderRelations = (code: string, date: string, name?: string | null, includeRejected = false) =>
  client.get<LeaderRelations>(`/market-recap/leader-relations/${code}`, {
    params: { date, name: name ?? undefined, include_rejected: includeRejected || undefined },
  }).then((r) => r.data)
export const putRelation = (body: {
  id?: number; date: string; leader_code: string; leader_name?: string | null; attribute_type: AttributeType
  attribute_value: string; related_code: string; related_name?: string | null; evidence?: string | null
  source?: string; confidence_status?: string; note?: string | null
}) => client.put<Relation>('/market-recap/leader-relations', body).then((r) => r.data)
export const deleteRelation = (id: number) =>
  client.delete(`/market-recap/leader-relations/${id}`).then((r) => r.data)
export const putResponse = (body: { date: string; relation_id: number; response_status: ResponseStatus; note?: string }) =>
  client.put('/market-recap/responses', body).then((r) => r.data)

export interface LockedPayload {
  recap: Record<string, unknown> & { market_posture?: Posture | null; invalidation?: string[]; flat_plan?: boolean }
  plans: Plan[]
  watch_pool: { code: string; name: string | null; deep: boolean }[]
  relations: unknown[]
}

export interface SectorPeers {
  status: 'ok' | 'missing'
  note?: string
  leader?: StockFacts | null
  groups: {
    sector: { code: string; name: string; type: 'industry' | 'concept' | 'region' | string; primary: boolean }
    stocks: { code: string; active: ('strong' | 'limit_up' | 'limit_down')[]; also_in: string[]; facts: StockFacts | null; system_response: SystemResponse }[]
  }[]
  active_total?: number
  peers_total?: number
  notes?: string[]
  response_labels: Record<SystemResponse, string>
}

export const fetchSectorPeers = (code: string, date: string) =>
  client.get<SectorPeers>(`/market-recap/leader-sectors/${code}`, { params: { date } }).then((r) => r.data)

// ─── 盘中跟踪（只读；盘中事件全部是临时的，不是收盘结论）──────────────────────

export type LivePhase = 'pre_open' | 'auction' | 'auction_done' | 'trading' | 'lunch' | 'closing_auction'
  | 'closed' | 'non_trading' | 'stale' | 'unknown'
export interface LiveMeta { status: 'ok' | 'partial' | 'missing'; source: string; observed_at: string | null; note: string | null; provisional: true }
export interface LiveEvent { event_type: string; evidence: string; time: string | null; provisional: true }
export interface LiveFact {
  code: string
  name: string | null
  /** ok 有今天的行情 / missing 没拿到 / stale 行情不是今天的 / no_trade 今天还没成交 */
  status: 'ok' | 'missing' | 'stale' | 'no_trade'
  price: number | null
  pct: number | null
  open: number | null
  high: number | null
  low: number | null
  prev_close: number | null
  amount_yi: number | null
  is_limit_up: boolean | null
  /** sealed 此刻封涨停 / broken 摸过涨停没封住 / limit_down 此刻跌停 */
  limit: 'sealed' | 'broken' | 'limit_down' | null
  first_limit_time: string | null
  observed_at: string | null
  note: string | null
  events: LiveEvent[]
  prev: { pct: number | null; is_limit_up: boolean; board_count: number | null } | null
}
export interface LiveCohort {
  members: number; quoted: number; median_pct: number | null; red_ratio: number | null
  sealed: number; broken: number; limit_down: number
}
export interface LivePlanRow {
  stock_code: string; stock_name: string | null; scenario: Scenario | null; sector_name: string | null; reason: string | null
  entry_trigger: string; invalidation: string; planned_stop: number | null; risk_budget: number | null
  position_limit: number | null; leader_code: string | null; relation_note: string | null; watch_trigger: string | null
  live: LiveFact; below_stop: boolean | null; to_stop_pct: number | null
}
export interface LiveData {
  session: {
    phase: LivePhase; phase_zh: string; live: boolean; data_date: string | null; ref_date: string | null
    target_date: string | null; target_is_provisional: boolean; market_time: string | null; server_time: string
    poll_seconds: number | null; calendar_behind: boolean; calendar_last: string | null; note: string
  }
  provisional_note: string
  quotes: { requested: number; ok: number; missing: string[]; stale: string[]; no_trade: string[]; error: string | null; source: string }
  market: {
    indices: { code: string; name: string; price: number | null; pct: number | null; amount_yi: number | null; status: string }[]
    indices_meta: LiveMeta
    breadth?: { up?: number; down?: number; flat?: number; limit_up?: number; limit_down?: number; meta: LiveMeta }
    limit?: { sealed?: number; broken?: number; seal_rate?: number | null; max_board?: number | null; meta: LiveMeta }
    amount?: { amount?: number; estimate?: number | null; is_trading?: boolean; prev_date: string | null
      prev_amount: number | null; estimate_vs_prev_pct?: number | null; meta: LiveMeta }
    cohorts: Partial<Record<'limit_up' | 'multi_board' | 'broken_board', LiveCohort>>
  }
  plan: {
    source: 'locked' | 'draft' | null; ref_date: string | null; version?: number; locked_at?: string
    pre_open?: boolean; backfilled?: boolean; posture?: Posture | null; key_points?: string[]
    invalidation?: string[]; flat_plan?: boolean; rows: LivePlanRow[]
  }
  leaders: {
    code: string; name: string | null; live: LiveFact; by_response: Partial<Record<SystemResponse, number>>
    relations: { code: string; name: string | null; live: LiveFact; system_response: SystemResponse | null
      attributes: { type: AttributeType; value: string; basis: 'A' | 'B' | 'C' }[] }[]
  }[]
  watch: { code: string; name: string | null; deep: boolean; note: string | null; live: LiveFact }[]
  cores: {
    ref_date: string | null; frozen: boolean | null; built_at: string | null; note: string | null
    source_labels: Record<string, string>
    rows: { code: string; name: string | null; board: string; sources: string[]; lifecycle_state: string | null
      sector: string | null; ref_pct: number | null; ref_board: number | null; watched: boolean; live: LiveFact }[]
  }
  limit_pool: { code: string; name: string; board: number | null; first_time: string | null; last_time: string | null
    broken_times: number | null; industry: string | null; pct: number | null
    in_core: boolean; watched: boolean }[]
  labels: { events: Record<string, string>; responses: Record<SystemResponse, string>; phases: Record<LivePhase, string> }
}

export const fetchLive = () => client.get<LiveData>('/market-recap/live').then((r) => r.data)
