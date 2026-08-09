import client from './client'
import type { MarketTrendResponse, WindvaneResponse, MarginRange } from '@/types'

export const fetchMarketTrend = (refresh = false) =>
  client
    .get<MarketTrendResponse>('/market-trend/indices', { params: refresh ? { refresh: true } : undefined })
    .then((r) => r.data)

export const fetchWindvane = (refresh = false, marginRange: MarginRange = '6m') =>
  client
    .get<WindvaneResponse>('/market-trend/windvane', {
      params: { ...(refresh ? { refresh: true } : {}), margin_range: marginRange },
    })
    .then((r) => r.data)
