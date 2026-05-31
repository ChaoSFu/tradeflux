import client from './client'
import type { SectorListResponse, Sector, SectorSnapshot } from '@/types'

export const fetchSectors = () =>
  client.get<SectorListResponse>('/sectors').then((r) => r.data)

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
