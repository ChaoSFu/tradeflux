import client from './client'
import type { SectorListResponse, Sector, SectorSnapshot } from '@/types'

/**
 * 板块列表。
 *
 * **只读 rank/phase 那几个标签字段时传 false。** 实测完整载荷 2.1MB / 2.75s
 * （304 个板块，每个内嵌全部成员股），是全站最大的一个请求；而它挂在常驻顶栏的
 * useSectorTags 上，**每进任何一个页面都要付一遍**。
 *
 * 带不带成员股的结果形状不同，**必须用不同的 queryKey**——共用一个 key 会让
 * 两类调用方抢同一份缓存，谁先到谁说了算。用 SECTORS_LITE_KEY。
 */
export const fetchSectors = (includeStocks = true) =>
  client.get<SectorListResponse>('/sectors',
    { params: includeStocks ? {} : { include_stocks: false } }).then((r) => r.data)

/** 不带成员股那一份的共享 key。所有只要标签字段的调用方都用它，共享一次请求 */
export const SECTORS_LITE_KEY = ['sectors', 'no-stocks'] as const

export const fetchSector = (code: string) =>
  client.get<Sector>(`/sectors/${code}`).then((r) => r.data)

export const fetchSectorSnapshots = (code: string, days = 30) =>
  client.get<SectorSnapshot[]>(`/sectors/${code}/snapshots`, { params: { days } }).then((r) => r.data)

export interface TopStockItem {
  code: string
  name: string
  pct_today: number | null
  pct_5d: number | null
  pct_10d: number | null
  pct_20d: number | null
  pct_60d: number | null
}

export interface TopStocksResponse {
  bk_code: string
  period: string
  stocks: TopStockItem[]
}

export const fetchSectorTopStocks = (code: string, period = '20d', limit = 10) =>
  client.get<TopStocksResponse>(`/sectors/${code}/top-stocks`, {
    params: { period, limit },
  }).then((r) => r.data)
