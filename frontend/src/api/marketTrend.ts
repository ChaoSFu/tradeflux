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
