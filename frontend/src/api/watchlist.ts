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

export interface ApproachingItem {
  security_code: string
  security_name: string | null
  direction: 'up' | 'down'
  window: string          // '10d' | '30d'
  cum_deviation: number   // 累计偏离值 %
  threshold: number       // 触发阈值 %
  approach: number        // 接近度 = 累计偏离值 / 阈值
  coverage: number        // 参与计算的交易日数
  full_window: boolean    // 是否取满窗口
  rule_label: string
  stock: Stock | null
}

export interface RegulatoryWatchlistResponse {
  as_of: string
  monitoring: RegulatoryItem[]
  ending_soon: RegulatoryItem[]
  recently_released: RegulatoryItem[]
  approaching: ApproachingItem[]
}

export const fetchRegulatoryWatchlist = () =>
  client.get<RegulatoryWatchlistResponse>('/watchlist/regulatory').then((r) => r.data)
