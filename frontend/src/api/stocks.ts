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
  missing_days: number | null
  peak_board_confident: boolean | null
}

export interface LeaderCycleResponse {
  trade_date: string | null
  running: LeaderCycleItem[]
  broken: LeaderCycleItem[]
  coverage: Record<string, number>
  scope_note: string
}

export const fetchLeaderCycle = (tradeDate?: string) =>
  client.get<LeaderCycleResponse>('/leader-cycle',
    { params: tradeDate ? { trade_date: tradeDate } : {} }).then((r) => r.data)
