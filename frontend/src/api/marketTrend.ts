import client from './client'
import type { MarketTrendResponse, WindvaneResponse, MarginRange } from '@/types'

export const fetchMarketTrend = (refresh = false) =>
  client
    .get<MarketTrendResponse>('/market-trend/indices', { params: refresh ? { refresh: true } : undefined })
    .then((r) => r.data)

export const fetchWindvane = (refresh = false, marginRange: MarginRange = '6m', updownDate?: string) =>
  client
    .get<WindvaneResponse>('/market-trend/windvane', {
      params: {
        ...(refresh ? { refresh: true } : {}),
        margin_range: marginRange,
        ...(updownDate ? { updown_date: updownDate } : {}),
      },
    })
    .then((r) => r.data)

export const fetchUpDownDates = () =>
  client.get<string[]>('/market-trend/updown-dates').then((r) => r.data)

export interface UpDownSeriesPoint {
  date: string
  up: number
  down: number
  // 分档序列。**刻意不含 0~1% / 平盘 / -0~1%**：那三档是中枢噪音，对"赚钱效应
  // 强不强"没有贡献，只会用一堆一千多的数字把别的曲线压平。
  limit_up: number      // 涨停
  up_gt5: number        // 涨>5%（不含涨停）
  up_1_5: number        // 涨1~5%
  down_1_5: number      // 跌1~5%
  down_gt5: number      // 跌>5%（不含跌停）
  limit_down: number    // 跌停
}

export const fetchUpDownSeries = (days = 120) =>
  client.get<UpDownSeriesPoint[]>('/market-trend/updown-series', { params: { days } }).then((r) => r.data)
