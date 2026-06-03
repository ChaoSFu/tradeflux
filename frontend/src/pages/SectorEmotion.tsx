import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchStrongPool, fetchLimitMoves } from '@/api/stocks'
import type { Stock } from '@/types'
import { SectorGroupedView } from '@/components/common/SectorGroupedView'

/**
 * 情绪板块：复用板块强势分布的分组卡片布局，
 * 但成员个股 = 强势股池 + 涨停池 + 跌停池（三路合并去重）。
 * 复用 sector-analysis 的 queryKey，零额外请求。
 */
export default function SectorEmotion() {
  const { data: strongData, isLoading: l1 } = useQuery({
    queryKey: ['strong-pool-sector-analysis'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
  } as any)
  const { data: upData, isLoading: l2 } = useQuery({
    queryKey: ['limit-up-sector-analysis'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up' }),
  } as any)
  const { data: downData, isLoading: l3 } = useQuery({
    queryKey: ['limit-down-sector-analysis'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down' }),
  } as any)

  const stocks: Stock[] = useMemo(() => {
    const seen = new Set<number>()
    const merged: Stock[] = []
    for (const s of [
      ...((strongData as any)?.items ?? []),
      ...((upData as any)?.items ?? []),
      ...((downData as any)?.items ?? []),
    ] as Stock[]) {
      if (!seen.has(s.id)) { seen.add(s.id); merged.push(s) }
    }
    return merged
  }, [strongData, upData, downData])

  return (
    <SectorGroupedView
      stocks={stocks}
      isLoading={l1 || l2 || l3}
      minStorageKey="tradeflux:sector_emotion_min_stocks"
      unitLabel="个股"
    />
  )
}
