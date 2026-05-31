import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchStrongPool } from '@/api/stocks'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { Search, ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/utils/cn'
import type { Stock } from '@/types'
import {
  type GroupKey, type SectorGroup,
  buildSectorGroups, getGroup, getSectorAvgPct,
  SectorSection,
} from '@/components/common/SectorSection'

// ─── Sector sort ──────────────────────────────────────────────────────────────

type SortKey = 'total' | 'limit_up' | 'limit_down' | 'up' | 'down' | 'oscillating' | 'weakening' | 'broken' | 'avg_pct'

const SORT_DEFS: { key: SortKey; label: string }[] = [
  { key: 'avg_pct',     label: '赚钱效应' },
  { key: 'total',       label: '强势股' },
  { key: 'limit_up',    label: '涨停' },
  { key: 'limit_down',  label: '跌停' },
  { key: 'up',          label: '上涨' },
  { key: 'down',        label: '下跌' },
  { key: 'oscillating', label: '震荡' },
  { key: 'weakening',   label: '走弱' },
  { key: 'broken',      label: '破位' },
]

function getSectorStat(sg: SectorGroup, key: SortKey): number {
  switch (key) {
    case 'total':       return sg.stocks.length
    case 'avg_pct':     return getSectorAvgPct(sg.stocks)
    case 'limit_up':    return sg.stocks.filter((s) => s.today_is_limit_up).length
    case 'limit_down':  return sg.stocks.filter((s) => (s.today_pct_change ?? 0) <= -9.8).length
    case 'up':          return sg.stocks.filter((s) => (s.today_pct_change ?? 0) > 0).length
    case 'down':        return sg.stocks.filter((s) => (s.today_pct_change ?? 0) < 0).length
    case 'oscillating': return sg.stocks.filter((s) => getGroup(s) === 'oscillating').length
    case 'weakening':   return sg.stocks.filter((s) => getGroup(s) === 'weakening').length
    case 'broken':      return sg.stocks.filter((s) => getGroup(s) === 'broken').length
  }
}

// ─── Min strong-stock count filter (persisted) ───────────────────────────────

const LS_MIN_KEY = 'tradeflux:sector_pool_min_stocks'
const DEFAULT_MIN = 3

function loadMinStocks(): number {
  try {
    const v = parseInt(localStorage.getItem(LS_MIN_KEY) ?? '', 10)
    return isNaN(v) || v < 1 ? DEFAULT_MIN : v
  } catch { return DEFAULT_MIN }
}

function useMinStocks() {
  const [min, setMin] = useState<number>(loadMinStocks)
  const update = (v: number) => {
    setMin(v)
    try { localStorage.setItem(LS_MIN_KEY, String(v)) } catch { /* ignore */ }
  }
  return [min, update] as const
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function SectorPool() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [minStocks, setMinStocks] = useMinStocks()
  const [sortKey, setSortKey] = useState<SortKey>('avg_pct')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const handleSortClick = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  const { data, isLoading } = useQuery({
    queryKey: ['strong-pool-all-for-sector'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
  } as any)

  const allStocks: Stock[] = (data as any)?.items ?? []

  const groups = useMemo(() => {
    let g = buildSectorGroups(allStocks)
    // Apply min strong-stock filter
    g = g.filter((sg) => sg.stocks.length >= minStocks)
    // Apply search
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      g = g
        .map((sg) => ({
          ...sg,
          stocks: sg.stocks.filter((s) => s.name.includes(q) || s.code.includes(q)),
        }))
        .filter((sg) => sg.stocks.length > 0 || sg.name.toLowerCase().includes(q))
    }
    // Apply sort
    g = [...g].sort((a, b) => {
      const av = getSectorStat(a, sortKey)
      const bv = getSectorStat(b, sortKey)
      if (av !== bv) return sortDir === 'desc' ? bv - av : av - bv
      // tiebreak: total stocks desc
      return b.stocks.length - a.stocks.length
    })
    return g
  }, [allStocks, minStocks, search, sortKey, sortDir])

  // Real-time unique stock count across all currently displayed groups
  const displayedStockCount = useMemo(() => {
    const ids = new Set<number>()
    for (const sg of groups) for (const s of sg.stocks) ids.add(s.id)
    return ids.size
  }, [groups])

  const toggleCollapse = (name: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.has(name) ? next.delete(name) : next.add(name)
      return next
    })

  return (
    <div className="space-y-3 animate-fade-in">
      {/* Top bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索股票或板块..."
            className="bg-bg-card border border-bg-border rounded pl-8 pr-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent/50 w-52"
          />
        </div>

        {/* Min strong-stock count filter */}
        <div className="flex items-center rounded border border-bg-border overflow-hidden text-xs">
          <span className="px-2.5 py-1.5 text-text-muted whitespace-nowrap bg-bg-elevated/50">
            强势股≥
          </span>
          <input
            type="number"
            min={1}
            value={minStocks}
            onChange={(e) => {
              const v = parseInt(e.target.value, 10)
              if (!isNaN(v) && v >= 1) setMinStocks(v)
            }}
            className="w-12 py-1.5 text-center bg-transparent border-l border-bg-border/60 font-mono text-accent focus:outline-none"
          />
          <span className="px-1.5 py-1.5 text-text-muted/85 whitespace-nowrap">只</span>
        </div>

        <div className="text-xs text-text-muted ml-auto">
          {groups.length} 个板块 · {displayedStockCount} 只强势股
        </div>
      </div>

      {/* Sort bar */}
      <div className="flex items-center gap-1 flex-wrap">
        <span className="text-xs text-text-muted/80 whitespace-nowrap mr-0.5">排序:</span>
        {SORT_DEFS.map(({ key, label }) => {
          const active = sortKey === key
          return (
            <button
              key={key}
              onClick={() => handleSortClick(key)}
              className={cn(
                'flex items-center gap-0.5 px-2 py-1 rounded text-xs transition-colors whitespace-nowrap border',
                active
                  ? 'bg-accent/12 text-accent border-accent/30'
                  : 'text-text-muted hover:text-text-secondary hover:bg-bg-elevated border-transparent',
              )}
            >
              {label}
              {active && (sortDir === 'desc'
                ? <ChevronDown className="w-3 h-3 shrink-0" />
                : <ChevronUp className="w-3 h-3 shrink-0" />
              )}
            </button>
          )
        })}
      </div>

      {/* Groups */}
      {isLoading ? (
        <div className="card p-4"><LoadingRows /></div>
      ) : groups.length === 0 ? (
        <div className="text-center text-text-muted py-16 text-sm">暂无数据</div>
      ) : (
        <div className="space-y-2">
          {groups.map((sg) => (
            <SectorSection
              key={sg.name}
              group={sg}
              collapsed={collapsed.has(sg.name)}
              onToggle={() => toggleCollapse(sg.name)}
              onClickStock={(code) => navigate(`/stocks/${code}`)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
