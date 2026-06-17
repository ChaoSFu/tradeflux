/**
 * useDragonStocks — 总龙头子集
 *
 * 与活跃股池「总龙头」tab 同口径：合并全集（强势池+涨停池+跌停池）中，
 * 持有全市场龙头标签（10/20/60龙·60高板龙·连板龙 的龙1/龙2）的股票。
 *
 * 复用 sector-analysis 的 queryKey（与 useSectorLeaders / useLeaderUniverseMaxes 共享缓存），
 * 在已加载这些数据的页面零额外请求。
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchStrongPool, fetchLimitMoves } from '@/api/stocks'
import { useLeaderUniverseMaxes, getLeaderTags } from '@/hooks/useLeaderUniverseMaxes'
import type { Stock } from '@/types'

export function useDragonStocks(): Stock[] {
  const maxes = useLeaderUniverseMaxes()
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
    const seen = new Set<number>()
    const merged: Stock[] = []
    for (const s of [
      ...((strong as any)?.items ?? []),
      ...((up as any)?.items ?? []),
      ...((down as any)?.items ?? []),
    ] as Stock[]) {
      if (!seen.has(s.id)) { seen.add(s.id); merged.push(s) }
    }
    return merged.filter((s) => getLeaderTags(s, maxes).length > 0)
  }, [strong, up, down, maxes])
}
