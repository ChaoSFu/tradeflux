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
  coverage: number        // 累计天数
  full_window: boolean
  target_rate: number | null  // 今日还需涨跌幅 % 即触发
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

export interface SevereTarget {
  target_rate: number | null  // 今日还需涨幅 % 触发
  approach: number            // 接近度
  days: number                // 累计天数
  threshold: number           // 阈值(+100/+200)
}

export const fetchSevereTargets = () =>
  client.get<Record<string, SevereTarget>>('/watchlist/severe-targets').then((r) => r.data)
