import client from './client'
import type { MarketState, MarketHistoryPoint, ProfitEffectData, StrongPoolLimitPoint } from '@/types'

export const fetchMarketState = () =>
  client.get<MarketState>('/market-state').then((r) => r.data)

export const fetchMarketHistory = (days = 30) =>
  client.get<MarketHistoryPoint[]>('/market-state/history', { params: { days } }).then((r) => r.data)

/** 强势池每天的涨停 / 跌停只数（只三列，30 天约 1KB；顶栏每个页面都取，别换成 /history） */
export const fetchStrongPoolLimits = (days = 30) =>
  client.get<StrongPoolLimitPoint[]>('/market-state/strong-pool-limits', { params: { days } }).then((r) => r.data)

const LS_MIN_KEY = 'tradeflux:sector_pool_min_stocks'
function getSectorMinStocks(): number {
  try {
    const v = parseInt(localStorage.getItem(LS_MIN_KEY) ?? '', 10)
    return isNaN(v) || v < 1 ? 3 : v
  } catch { return 3 }
}

export const fetchProfitEffect = () =>
  client
    .get<ProfitEffectData>('/market-state/profit-effect', {
      params: { min_stocks: getSectorMinStocks() },
    })
    .then((r) => r.data)
