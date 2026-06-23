/**
 * useStockByCode — 全局 code → Stock 映射
 *
 * 复用 sector-analysis 的 queryKey（与 useDragonStocks / useSectorLeaders 等共享缓存），
 * 供任意页面给个股补「昨涨停/昨跌停」等需要完整 Stock 字段的标签。
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchStrongPool, fetchLimitMoves } from '@/api/stocks'
import type { Stock } from '@/types'

export function useStockByCode(): Map<string, Stock> {
  const { data: strong } = useQuery({
    queryKey: ['strong-pool-sector-analysis'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
  } as any)
  const { data: up } = useQuery({
    queryKey: ['limit-up-sector-analysis'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up' }),
  } as any)
  const { data: down } = useQuery({
    queryKey: ['limit-down-sector-analysis'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down' }),
  } as any)

  return useMemo(() => {
    const m = new Map<string, Stock>()
    for (const s of [
      ...((strong as any)?.items ?? []),
      ...((up as any)?.items ?? []),
      ...((down as any)?.items ?? []),
    ] as Stock[]) {
      if (!m.has(s.code)) m.set(s.code, s)
    }
    return m
  }, [strong, up, down])
}
