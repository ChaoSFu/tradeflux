import client from './client'

/**
 * 买入检查 Pre-Trade Check。规则全在后端（pre_trade_rules.py），这里只有类型和请求。
 */
export type Level = 'PASS' | 'WARN' | 'FAIL' | 'UNKNOWN' | 'INFO'
export type Group = 'positive' | 'caution' | 'unmet' | 'unknown' | 'info'
export type Verdict = 'READY' | 'WAIT' | 'BLOCKED'
export type Quality = 'EXACT' | 'APPROX' | 'STALE' | 'UNKNOWN'

export interface CheckItem {
  key: string
  level: Level
  group: Group
  text: string
  evidence?: Record<string, unknown>
  module?: string | null
}

export interface CheckModule {
  key: string
  title: string
  status: 'PASS' | 'WARN' | 'FAIL' | 'UNKNOWN' | 'NEUTRAL'
  items: CheckItem[]
  known: number
  unknown: number
}

export interface Decision {
  verdict: Verdict
  summary: string
  rule_version: string
  vetoes: CheckItem[]
  unmet: CheckItem[]
  cautions: CheckItem[]
  unknowns: CheckItem[]
  positives: CheckItem[]
}

export interface DataQualityRow {
  module: string
  source: string
  observed_at: string | null
  quality: Quality | string
  notes: string[]
}

export interface SectorOption {
  id: number
  name: string
  is_watched: boolean
  stock_count: number
  is_primary: boolean
  prev_limit_up: number | null
}

export interface JournalRow {
  id: number
  stock_code: string | null
  stock_name: string | null
  action: string
  trade_time: string
  price: number
}

export interface PreTradeContext {
  mode: 'LIVE' | 'HISTORICAL'
  as_of: string
  trade_date: string
  prev_trade_date: string | null
  is_trading_day: boolean | null
  /** 交易日历（只到拉取当天）。behind = 日历最后一天和 as_of 之间还夹着工作日 */
  calendar: { last: string | null; behind: boolean | null }
  rule_version: string
  manual_questions: { key: string; text: string }[]
  defaults: {
    account_risk_budget_pct: number
    stress_loss_pct: number
    earliest_normal_entry: string
    reentry_trading_days: number
    max_trades_per_day: number
  }
  stock: { code: string; name: string | null; in_db: boolean; is_st: boolean
           limit_up_price: number | null; limit_down_price: number | null }
  intraday: {
    price: number | null; pct: number | null; prev_close: number | null; open: number | null
    high: number | null; low: number | null; vwap: number | null; amount: number | null
    source: string; quality: Quality | string; observed_at: string | null; notes: string[]
    structure?: { status: string; state: string | null; h1: number | null; l1: number | null; anchor: number | null }
  }
  market: { indexes: { code: string; name: string; pct: number | null; quality: string }[] }
  sector: { thesis: { id: number; name: string } | null; options: SectorOption[] }
  leader: { lifecycle?: { date: string; state: string; last_valid_state?: string | null } | null
            board_count_60d?: number | null }
  discipline: { available: boolean; reason?: string; today_buys?: JournalRow[]
                recent_same_stock?: JournalRow[]; consecutive_losses?: number }
  data_quality: DataQualityRow[]
}

export interface ManualAnswers {
  q1: boolean | null; q2: boolean | null; q3: boolean | null; q4: boolean | null
  q5: boolean | null; q6: boolean | null; q7: boolean | null; q8: boolean | null
  q9: boolean | null
  a_plus: boolean | null
  new_market_fact: string
  second_trade_note: string
}

export const EMPTY_ANSWERS: ManualAnswers = {
  q1: null, q2: null, q3: null, q4: null, q5: null, q6: null, q7: null, q8: null, q9: null,
  a_plus: null, new_market_fact: '', second_trade_note: '',
}

export interface EvaluatePayload {
  stock_code: string
  as_of?: string | null
  intended_price?: number | null
  position_pct?: number | null
  planned_stop?: number | null
  reason?: string
  thesis_sector_id?: number | null
  manual_answers: ManualAnswers
  account_risk_budget_pct?: number
  stress_loss_pct?: number
}

export interface EvaluateResult {
  id: number | null
  mode: 'LIVE' | 'HISTORICAL'
  as_of: string
  stock_code: string
  stock_name: string | null
  modules: CheckModule[]
  decision: Decision
  context: PreTradeContext
  data_quality: DataQualityRow[]
}

export interface PreTradeHistoryItem {
  id: number
  stock_code: string
  stock_name: string | null
  as_of: string
  mode: string
  verdict: Verdict
  rule_version: string
  intended_price: number | null
  created_at: string | null
  has_outcome: boolean
}

export interface PriceRet { price: number | null; ret: number | null; date?: string }

export interface PreTradeOutcome {
  base_price: number | null
  computed_at: string
  notes: string[]
  after_30m?: PriceRet
  day_close?: PriceRet
  t1?: PriceRet
  t3?: PriceRet
  mfe?: number | null
  mae?: number | null
}

// 一次检查要取十几路外部行情（个股/三大指数/昨日高标的分钟数据、板块成分行情……）
const SLOW = { timeout: 45_000 }

const clean = (o: Record<string, unknown>) =>
  Object.fromEntries(Object.entries(o).filter(([, v]) => v !== null && v !== undefined && v !== ''))

export const fetchPreTradeContext = (p: { stock_code: string; as_of?: string | null; sector_id?: number | null }) =>
  client.get<PreTradeContext>('/pre-trade-check/context', { params: clean(p), ...SLOW }).then((r) => r.data)

export const evaluatePreTrade = (body: EvaluatePayload) =>
  client.post<EvaluateResult>('/pre-trade-check/evaluate', body, SLOW).then((r) => r.data)

export const fetchPreTradeHistory = (limit = 20) =>
  client.get<PreTradeHistoryItem[]>('/pre-trade-check/history', { params: { limit } }).then((r) => r.data)

export const fetchPreTradeCheck = (id: number) =>
  client.get<EvaluateResult>(`/pre-trade-check/${id}`).then((r) => r.data)

export const fetchPreTradeOutcome = (id: number) =>
  client.get<PreTradeOutcome>(`/pre-trade-check/${id}/outcome`, SLOW).then((r) => r.data)
