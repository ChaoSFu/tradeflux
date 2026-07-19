import client from './client'
import type { MarketTrendResponse, WindvaneResponse } from '@/types'

export const fetchMarketTrend = (refresh = false) =>
  client
    .get<MarketTrendResponse>('/market-trend/indices', { params: refresh ? { refresh: true } : undefined })
    .then((r) => r.data)

export const fetchWindvane = (refresh = false) =>
  client
    .get<WindvaneResponse>('/market-trend/windvane', { params: refresh ? { refresh: true } : undefined })
    .then((r) => r.data)
