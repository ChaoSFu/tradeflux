import client from './client'
import type { MarketTrendResponse } from '@/types'

export const fetchMarketTrend = (refresh = false) =>
  client
    .get<MarketTrendResponse>('/market-trend/indices', { params: refresh ? { refresh: true } : undefined })
    .then((r) => r.data)
