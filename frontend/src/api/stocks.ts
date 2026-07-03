import client from './client'
import type { StockListResponse, StockSnapshot, Stock, LimitMoveTrendPoint } from '@/types'

export const fetchStocks = (params?: {
  page?: number
  page_size?: number
  in_strong_pool?: boolean
  sector_id?: number
  search?: string
}) => client.get<StockListResponse>('/stocks', { params }).then((r) => r.data)

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

export const fetchStock = (code: string) =>
  client.get<Stock>(`/stocks/${code}`).then((r) => r.data)

export const fetchStockSnapshots = (code: string, days = 30) =>
  client.get<StockSnapshot[]>(`/stocks/${code}/snapshots`, { params: { days } }).then((r) => r.data)
