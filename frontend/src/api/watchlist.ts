import client from './client'
import type { Stock } from '@/types'

export interface RegulatoryItem {
  info_code: string
  security_code: string
  security_name: string | null
  exchange: string | null
  reason_type: string | null
  reason: string | null
  direction: 'up' | 'down' | null
  start_date: string | null
  end_date: string | null
  predict_start: string | null
  predict_end: string | null
  notice_date: string | null
  days_remaining: number | null
  status: 'monitoring' | 'ending_soon' | 'released'
  stock: Stock | null
}

export interface RegulatoryWatchlistResponse {
  as_of: string
  monitoring: RegulatoryItem[]
  ending_soon: RegulatoryItem[]
  recently_released: RegulatoryItem[]
}

export const fetchRegulatoryWatchlist = () =>
  client.get<RegulatoryWatchlistResponse>('/watchlist/regulatory').then((r) => r.data)
