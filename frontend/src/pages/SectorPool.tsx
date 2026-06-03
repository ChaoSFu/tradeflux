import { useQuery } from '@tanstack/react-query'
import { fetchStrongPool } from '@/api/stocks'
import type { Stock } from '@/types'
import { SectorGroupedView } from '@/components/common/SectorGroupedView'

// 板块强势分布：成员个股 = 强势股池
export default function SectorPool() {
  const { data, isLoading } = useQuery({
    queryKey: ['strong-pool-all-for-sector'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
  } as any)

  const stocks: Stock[] = (data as any)?.items ?? []

  return (
    <SectorGroupedView
      stocks={stocks}
      isLoading={isLoading}
      minStorageKey="tradeflux:sector_pool_min_stocks"
      unitLabel="强势股"
    />
  )
}
