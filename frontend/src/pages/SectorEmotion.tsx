import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchStrongPool, fetchLimitMoves } from '@/api/stocks'
import { cn } from '@/utils/cn'
import type { Stock } from '@/types'
import { SectorGroupedView } from '@/components/common/SectorGroupedView'
import { PhaseLifecycleBar } from '@/components/common/PhaseLifecycleBar'

type Filter = 'all' | 'strong' | 'limit'

const FILTERS: { key: Filter; label: string; unit: string }[] = [
  { key: 'all',    label: '全部',     unit: '个股' },
  { key: 'strong', label: '强势股',   unit: '强势股' },
  { key: 'limit',  label: '涨跌停股', unit: '涨跌停股' },
]

/**
 * 情绪板块：板块分组卡片视图，成员个股可按 全部/强势股/涨跌停股 过滤。
 * - 全部   = 强势池 + 涨停池 + 跌停池（三路合并去重）
 * - 强势股 = 强势池（等价于原「板块强势分布 Sector Pool」）
 * - 涨跌停 = 涨停池 + 跌停池（等价于原「涨跌停板块分布 Limit Sectors」）
 * 复用 sector-analysis 的 queryKey，零额外请求。
 */
export default function SectorEmotion() {
  const [filter, setFilter] = useState<Filter>('all')
  const [phase, setPhase] = useState<number | null>(null)

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
    const strong: Stock[] = (strongData as any)?.items ?? []
    const up: Stock[]     = (upData as any)?.items ?? []
    const down: Stock[]   = (downData as any)?.items ?? []
    const src =
      filter === 'strong' ? strong
      : filter === 'limit' ? [...up, ...down]
      : [...strong, ...up, ...down]
    const seen = new Set<number>()
    const merged: Stock[] = []
    for (const s of src) {
      if (!seen.has(s.id)) { seen.add(s.id); merged.push(s) }
    }
    return merged
  }, [strongData, upData, downData, filter])

  const unit = FILTERS.find((f) => f.key === filter)!.unit

  const filterTabs = (
    <div className="flex items-center rounded border border-bg-border overflow-hidden text-xs">
      {FILTERS.map((f, i) => (
        <button
          key={f.key}
          onClick={() => setFilter(f.key)}
          className={cn(
            'px-2.5 py-1.5 transition-colors whitespace-nowrap',
            i > 0 && 'border-l border-bg-border',
            filter === f.key ? 'bg-accent/15 text-accent font-medium' : 'text-text-muted hover:text-text-secondary',
          )}
        >
          {f.label}
        </button>
      ))}
    </div>
  )

  return (
    <div className="space-y-3">
      <PhaseLifecycleBar selected={phase} onSelect={setPhase} />
      <SectorGroupedView
        stocks={stocks}
        isLoading={l1 || l2 || l3}
        minStorageKey="tradeflux:sector_emotion_min_stocks"
        unitLabel={unit}
        headerExtra={filterTabs}
        phaseFilter={phase}
      />
    </div>
  )
}
