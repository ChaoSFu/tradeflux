import client from './client'
import type { MarketEffectDailyResponse, MarketEffectHistoryPoint, CohortMember, CohortType } from '@/types'

export const fetchMarketEffectLatest = () =>
  client.get<MarketEffectDailyResponse>('/market-effects/latest').then((r) => r.data)

export const fetchMarketEffectByDate = (tradeDate: string) =>
  client.get<MarketEffectDailyResponse>(`/market-effects/${tradeDate}`).then((r) => r.data)

export const fetchMarketEffectHistory = (days = 60) =>
  client.get<MarketEffectHistoryPoint[]>('/market-effects/history', { params: { days } }).then((r) => r.data)

export const fetchCohortMembers = (tradeDate: string, cohortType: CohortType) =>
  client
    .get<CohortMember[]>(`/market-effects/${tradeDate}/cohorts/${cohortType}/members`)
    .then((r) => r.data)
