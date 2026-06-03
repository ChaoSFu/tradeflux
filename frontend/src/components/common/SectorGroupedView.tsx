/**
 * SectorGroupedView — 板块分组卡片视图（搜索 + 个股数过滤 + 排序 + 折叠卡片）
 *
 * 由 SectorPool（强势股池）与 SectorEmotion（情绪板块）共用：
 * 传入不同的 stocks 全集即可，布局与交互完全一致。
 */
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { Search, ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/utils/cn'
import type { Stock } from '@/types'
import {
  type SectorGroup,
  buildSectorGroups, getGroup, getSectorAvgPct,
  SectorSection,
} from '@/components/common/SectorSection'

type SortKey = 'total' | 'limit_up' | 'limit_down' | 'up' | 'down' | 'oscillating' | 'weakening' | 'broken' | 'avg_pct'

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

function loadMinStocks(key: string): number {
  try {
    const v = parseInt(localStorage.getItem(key) ?? '', 10)
    return isNaN(v) || v < 1 ? 3 : v
  } catch { return 3 }
}

export function SectorGroupedView({
  stocks,
  isLoading,
  minStorageKey,
  unitLabel = '个股',
}: {
  stocks: Stock[]
  isLoading: boolean
  minStorageKey: string
  /** 头部计数 / 过滤器的单位文案，如「强势股」「个股」 */
  unitLabel?: string
}) {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [minStocks, setMinStocksState] = useState<number>(() => loadMinStocks(minStorageKey))
  const [sortKey, setSortKey] = useState<SortKey>('avg_pct')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const SORT_DEFS: { key: SortKey; label: string }[] = [
    { key: 'avg_pct',     label: '赚钱效应' },
    { key: 'total',       label: unitLabel },
    { key: 'limit_up',    label: '涨停' },
    { key: 'limit_down',  label: '跌停' },
    { key: 'up',          label: '上涨' },
    { key: 'down',        label: '下跌' },
    { key: 'oscillating', label: '震荡' },
    { key: 'weakening',   label: '走弱' },
    { key: 'broken',      label: '破位' },
  ]

  const setMinStocks = (v: number) => {
    setMinStocksState(v)
    try { localStorage.setItem(minStorageKey, String(v)) } catch { /* ignore */ }
  }

  const handleSortClick = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    else { setSortKey(key); setSortDir('desc') }
  }

  const groups = useMemo(() => {
    let g = buildSectorGroups(stocks)
    g = g.filter((sg) => sg.stocks.length >= minStocks)
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      g = g
        .map((sg) => ({ ...sg, stocks: sg.stocks.filter((s) => s.name.includes(q) || s.code.includes(q)) }))
        .filter((sg) => sg.stocks.length > 0 || sg.name.toLowerCase().includes(q))
    }
    g = [...g].sort((a, b) => {
      const av = getSectorStat(a, sortKey)
      const bv = getSectorStat(b, sortKey)
      if (av !== bv) return sortDir === 'desc' ? bv - av : av - bv
      return b.stocks.length - a.stocks.length
    })
    return g
  }, [stocks, minStocks, search, sortKey, sortDir])

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

        <div className="flex items-center rounded border border-bg-border overflow-hidden text-xs">
          <span className="px-2.5 py-1.5 text-text-muted whitespace-nowrap bg-bg-elevated/50">{unitLabel}≥</span>
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
          {groups.length} 个板块 · {displayedStockCount} 只{unitLabel}
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
