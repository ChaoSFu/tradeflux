/**
 * 板块分析 Sector Analysis
 * 综合强股池 + 涨停池 + 跌停池数据，板块视角汇总
 */
import { useMemo, useState, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchStrongPool, fetchLimitMoves } from '@/api/stocks'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { Search, Star, ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/utils/cn'
import { SortTh, type StockSortKey } from '@/components/common/SectorSection'
import type { Stock } from '@/types'

// ─── Group classification ──────────────────────────────────────────────────────

type GroupKey = 'limit_up' | 'oscillating' | 'weakening' | 'broken' | 'limit_down'

const GROUP_META: Record<GroupKey, { label: string; color: string; bg: string }> = {
  limit_up:    { label: '涨停', color: '#FF4560', bg: 'rgba(255,69,96,0.12)'   },
  oscillating: { label: '震荡', color: '#4F9CF9', bg: 'rgba(79,156,249,0.12)'  },
  weakening:   { label: '走弱', color: '#F59E0B', bg: 'rgba(245,158,11,0.12)'  },
  broken:      { label: '破位', color: '#26C281', bg: 'rgba(38,194,129,0.08)'  },
  limit_down:  { label: '跌停', color: '#34D399', bg: 'rgba(52,211,153,0.10)'  },
}

const GROUP_ORDER: GroupKey[] = ['limit_up', 'oscillating', 'weakening', 'broken', 'limit_down']
const GROUP_RANK = Object.fromEntries(GROUP_ORDER.map((k, i) => [k, i])) as Record<GroupKey, number>

function getGroup(stock: Stock, limitDownIds: Set<number>): GroupKey {
  if (limitDownIds.has(stock.id)) return 'limit_down'
  if (stock.today_is_limit_up)    return 'limit_up'
  if (stock.phase === 'broken')   return 'broken'
  if (stock.phase === 'weakening') return 'weakening'
  return 'oscillating'
}

// ─── Sector sort options ──────────────────────────────────────────────────────

type SortKey = 'total' | 'limit_up' | 'limit_down' | 'up' | 'down' | 'oscillating' | 'weakening' | 'broken'

const SORT_DEFS: { key: SortKey; label: string }[] = [
  { key: 'total',       label: '综合股' },
  { key: 'limit_up',    label: '涨停' },
  { key: 'limit_down',  label: '跌停' },
  { key: 'up',          label: '上涨' },
  { key: 'down',        label: '下跌' },
  { key: 'oscillating', label: '震荡' },
  { key: 'weakening',   label: '走弱' },
  { key: 'broken',      label: '破位' },
]

function getSectorStat(stocks: Stock[], key: SortKey, limitDownIds: Set<number>): number {
  switch (key) {
    case 'total':       return stocks.length
    case 'limit_up':    return stocks.filter(s => s.today_is_limit_up).length
    case 'limit_down':  return stocks.filter(s => limitDownIds.has(s.id)).length
    case 'up':          return stocks.filter(s => (s.today_pct_change ?? 0) > 0).length
    case 'down':        return stocks.filter(s => (s.today_pct_change ?? 0) < 0).length
    case 'oscillating': return stocks.filter(s => getGroup(s, limitDownIds) === 'oscillating').length
    case 'weakening':   return stocks.filter(s => getGroup(s, limitDownIds) === 'weakening').length
    case 'broken':      return stocks.filter(s => getGroup(s, limitDownIds) === 'broken').length
  }
}

// ─── Sector group builder ─────────────────────────────────────────────────────

interface SectorGroup { name: string; stocks: Stock[] }

function buildSectorGroups(stocks: Stock[]): Map<string, Stock[]> {
  const map = new Map<string, Stock[]>()
  for (const stock of stocks) {
    for (const sector of stock.sectors ?? []) {
      if (!map.has(sector)) map.set(sector, [])
      map.get(sector)!.push(stock)
    }
  }
  return map
}

// ─── Min stock filter (persisted) ────────────────────────────────────────────

const LS_MIN_KEY = 'tradeflux:sector_analysis_min_stocks'
const DEFAULT_MIN = 3

function useMinStocks() {
  const [min, setMin] = useState<number>(() => {
    try { const v = parseInt(localStorage.getItem(LS_MIN_KEY) ?? '', 10); return isNaN(v) || v < 1 ? DEFAULT_MIN : v }
    catch { return DEFAULT_MIN }
  })
  const update = (v: number) => {
    setMin(v)
    try { localStorage.setItem(LS_MIN_KEY, String(v)) } catch { /* ignore */ }
  }
  return [min, update] as const
}

// ─── Group tag chip ───────────────────────────────────────────────────────────

function GroupTag({ group }: { group: GroupKey }) {
  const { label, color, bg } = GROUP_META[group]
  return (
    <span
      className="inline-block text-xs px-1.5 py-0.5 rounded font-medium whitespace-nowrap shrink-0"
      style={{ color, backgroundColor: bg, border: `1px solid ${color}30` }}
    >
      {label}
    </span>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function SectorAnalysis() {
  const navigate = useNavigate()
  const [search, setSearch]         = useState('')
  const [collapsed, setCollapsed]   = useState<Set<string>>(new Set())
  const [minStocks, setMinStocks]   = useMinStocks()
  const [sortKey, setSortKey]       = useState<SortKey>('total')
  const [sortDir, setSortDir]       = useState<'asc' | 'desc'>('desc')

  // ── Data fetching ────────────────────────────────────────────────────────

  const { data: strongData, isLoading: loadingStrong } = useQuery({
    queryKey: ['strong-pool-sector-analysis'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
  } as any)

  const { data: upData, isLoading: loadingUp } = useQuery({
    queryKey: ['limit-up-sector-analysis'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up' }),
  } as any)

  const { data: downData, isLoading: loadingDown } = useQuery({
    queryKey: ['limit-down-sector-analysis'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down' }),
  } as any)

  const isLoading = loadingStrong || loadingUp || loadingDown

  // ── Merge + deduplicate stocks, track跌停 IDs ─────────────────────────────
  const { mergedStocks, limitDownIds } = useMemo(() => {
    const strong:    Stock[] = (strongData as any)?.items ?? []
    const limitUp:   Stock[] = (upData    as any)?.items ?? []
    const limitDown: Stock[] = (downData  as any)?.items ?? []

    const seenIds = new Set<number>()
    const merged: Stock[] = []
    const ldIds = new Set(limitDown.map(s => s.id))

    for (const s of strong)    { seenIds.add(s.id); merged.push(s) }
    for (const s of limitUp)   { if (!seenIds.has(s.id)) { seenIds.add(s.id); merged.push(s) } }
    for (const s of limitDown) { if (!seenIds.has(s.id)) { seenIds.add(s.id); merged.push(s) } }

    return { mergedStocks: merged, limitDownIds: ldIds }
  }, [strongData, upData, downData])

  // ── Build, filter, sort groups ────────────────────────────────────────────
  const groups = useMemo((): SectorGroup[] => {
    const map = buildSectorGroups(mergedStocks)
    let result: SectorGroup[] = []

    for (const [name, stocks] of map) {
      if (stocks.length < minStocks) continue
      result.push({ name, stocks })
    }

    if (search.trim()) {
      const q = search.trim().toLowerCase()
      result = result
        .map(sg => ({ ...sg, stocks: sg.stocks.filter(s => s.name.includes(q) || s.code.includes(q)) }))
        .filter(sg => sg.stocks.length > 0 || sg.name.toLowerCase().includes(q))
    }

    result.sort((a, b) => {
      const av = getSectorStat(a.stocks, sortKey, limitDownIds)
      const bv = getSectorStat(b.stocks, sortKey, limitDownIds)
      if (av !== bv) return sortDir === 'desc' ? bv - av : av - bv
      return b.stocks.length - a.stocks.length
    })

    return result
  }, [mergedStocks, limitDownIds, minStocks, search, sortKey, sortDir])

  const totalDisplayed = useMemo(() => {
    const ids = new Set<number>()
    for (const sg of groups) for (const s of sg.stocks) ids.add(s.id)
    return ids.size
  }, [groups])

  const handleSortClick = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }

  const toggleCollapse = (name: string) =>
    setCollapsed(prev => { const next = new Set(prev); next.has(name) ? next.delete(name) : next.add(name); return next })

  return (
    <div className="space-y-3 animate-fade-in">

      {/* 板块生命周期 */}
      {/* Filter bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-muted" />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="搜索股票或板块..."
            className="bg-bg-card border border-bg-border rounded pl-8 pr-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent/50 w-52"
          />
        </div>

        <div className="flex items-center rounded border border-bg-border overflow-hidden text-xs">
          <span className="px-2.5 py-1.5 text-text-muted whitespace-nowrap bg-bg-elevated/50">综合股≥</span>
          <input
            type="number" min={1} value={minStocks}
            onChange={e => { const v = parseInt(e.target.value, 10); if (!isNaN(v) && v >= 1) setMinStocks(v) }}
            className="w-12 py-1.5 text-center bg-transparent border-l border-bg-border/60 font-mono text-accent focus:outline-none"
          />
          <span className="px-1.5 py-1.5 text-text-muted/85">只</span>
        </div>

        <div className="text-xs text-text-muted ml-auto">
          {groups.length} 个板块 · {totalDisplayed} 只
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

      {/* Sector groups */}
      {isLoading ? (
        <div className="card p-4"><LoadingRows /></div>
      ) : groups.length === 0 ? (
        <div className="text-center text-text-muted py-16 text-sm">暂无数据</div>
      ) : (
        <div className="space-y-2">
          {groups.map(sg => (
            <SectorSection
              key={sg.name}
              group={sg}
              limitDownIds={limitDownIds}
              collapsed={collapsed.has(sg.name)}
              onToggle={() => toggleCollapse(sg.name)}
              onClickStock={code => navigate(`/stocks/${code}`)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Sector section ───────────────────────────────────────────────────────────

function SectorSection({
  group, limitDownIds, collapsed, onToggle, onClickStock,
}: {
  group: SectorGroup
  limitDownIds: Set<number>
  collapsed: boolean
  onToggle: () => void
  onClickStock: (code: string) => void
}) {
  const { name, stocks } = group
  const [pinnedGroup, setPinnedGroup] = useState<GroupKey | null>(null)
  const [sortKey, setSortKey] = useState<StockSortKey | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const defaultSorted = useMemo(() => (
    [...stocks].sort((a, b) => {
      const ga = getGroup(a, limitDownIds), gb = getGroup(b, limitDownIds)
      if (pinnedGroup) {
        if (ga === pinnedGroup && gb !== pinnedGroup) return -1
        if (gb === pinnedGroup && ga !== pinnedGroup) return  1
      }
      const rankDiff = GROUP_RANK[ga] - GROUP_RANK[gb]
      if (rankDiff !== 0) return rankDiff
      return b.leader_score - a.leader_score
    })
  ), [stocks, pinnedGroup, limitDownIds])

  const sortedStocks = useMemo(() => {
    if (sortKey) {
      return [...stocks].sort((a, b) => {
        const av = (a[sortKey] as number | null | undefined) ?? -Infinity
        const bv = (b[sortKey] as number | null | undefined) ?? -Infinity
        return sortDir === 'desc' ? bv - av : av - bv
      })
    }
    return defaultSorted
  }, [stocks, defaultSorted, sortKey, sortDir])

  const handleSort = (k: StockSortKey) => {
    if (sortKey === k) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    else { setSortKey(k); setSortDir('desc') }
  }

  const leader = defaultSorted[0]

  const groupCounts = useMemo(() => (
    stocks.reduce((acc, s) => {
      const g = getGroup(s, limitDownIds)
      acc[g] = (acc[g] ?? 0) + 1
      return acc
    }, {} as Partial<Record<GroupKey, number>>)
  ), [stocks, limitDownIds])

  // Accent color from most dominant non-跌停 group
  const dominantGroup = (
    (Object.entries(groupCounts) as [GroupKey, number][])
      .filter(([k]) => k !== 'limit_down')
      .sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'oscillating'
  ) as GroupKey
  const accentColor = GROUP_META[dominantGroup].color

  const upCount   = stocks.filter(s => (s.today_pct_change ?? 0) > 0).length
  const downCount = stocks.filter(s => (s.today_pct_change ?? 0) < 0).length
  const luCount   = stocks.filter(s => s.today_is_limit_up).length
  const ldCount   = stocks.filter(s => limitDownIds.has(s.id)).length

  const handleTagClick = useCallback((e: React.MouseEvent, key: GroupKey) => {
    e.stopPropagation()
    setPinnedGroup(prev => prev === key ? null : key)
  }, [])

  return (
    <div className="card overflow-hidden p-0" style={{ borderColor: `${accentColor}20` }}>

      {/* Header */}
      <button
        className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-bg-elevated transition-colors text-left"
        onClick={onToggle}
      >
        <div className="w-1 h-5 rounded-full shrink-0" style={{ backgroundColor: accentColor }} />
        <span className="font-semibold text-sm text-text-primary">{name}</span>
        <span
          className="text-xs font-mono px-1.5 py-0.5 rounded"
          style={{ color: accentColor, backgroundColor: `${accentColor}18` }}
        >
          {stocks.length} 只
        </span>
        {leader && (
          <span className="flex items-center gap-1 text-xs ml-1">
            <Star className="w-3 h-3 text-dragon fill-dragon shrink-0" />
            <span className="text-dragon font-medium">{leader.name}</span>
            <span className="text-text-muted/85 font-mono">{leader.code}</span>
          </span>
        )}

        <div className="ml-auto flex items-center gap-3">
          {/* 分组分布（可点击置顶） */}
          <span className="flex items-center gap-1.5 text-xs font-mono">
            {GROUP_ORDER.map(key => {
              const cnt = groupCounts[key] ?? 0
              if (!cnt) return null
              const { label, color } = GROUP_META[key]
              const active = pinnedGroup === key
              return (
                <button
                  key={key}
                  onClick={e => handleTagClick(e, key)}
                  className="px-1.5 py-px rounded font-medium whitespace-nowrap transition-all"
                  style={{
                    color,
                    backgroundColor: active ? `${color}30` : `${color}18`,
                    border: `1px solid ${active ? color : `${color}30`}`,
                    boxShadow: active ? `0 0 0 1px ${color}40` : undefined,
                  }}
                >
                  {label} {cnt}
                </button>
              )
            })}
          </span>

          {/* 上涨/下跌 */}
          <span className="flex items-center gap-1 text-xs font-mono">
            <span className="text-up">{upCount}涨</span>
            <span className="text-text-muted/70">/</span>
            <span className="text-down">{downCount}跌</span>
          </span>

          {/* 涨停/跌停（有才显示） */}
          {(luCount > 0 || ldCount > 0) && (
            <span className="flex items-center gap-1 text-xs font-mono">
              {luCount > 0 && (
                <span className="px-1.5 py-px rounded bg-up/15 text-up font-semibold">{luCount}涨停</span>
              )}
              {ldCount > 0 && (
                <span className="px-1.5 py-px rounded bg-down/15 text-down font-semibold">{ldCount}跌停</span>
              )}
            </span>
          )}

          {collapsed
            ? <ChevronDown className="w-3.5 h-3.5 text-text-muted shrink-0" />
            : <ChevronUp   className="w-3.5 h-3.5 text-text-muted shrink-0" />
          }
        </div>
      </button>

      {/* Stock rows */}
      {!collapsed && (
        <div className="border-t border-bg-border/30">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-bg-border/20 bg-bg-elevated/40">
                <th className="text-left  px-4 py-1.5 text-text-secondary font-semibold w-8">#</th>
                <th className="text-left  px-2 py-1.5 text-text-secondary font-semibold w-28">股票</th>
                <th className="text-left  px-2 py-1.5 text-text-secondary font-semibold w-20">分组</th>
                <SortTh label="连续连板" col="today_board_count" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortTh label="10日涨停" col="limit_up_days_10d" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortTh label="20日涨停" col="limit_up_days_20d" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortTh label="60日涨停" col="limit_up_days_60d" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortTh label="60日高板" col="board_count_60d" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortTh label="10日涨幅" col="pct_change_10d" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortTh label="20日涨幅" col="pct_change_20d" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortTh label="60日涨幅" col="pct_change_60d" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
                <SortTh label="龙头分" col="leader_score" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} className="w-14" />
                <SortTh label="风险分" col="risk_score" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} className="w-14" />
                <SortTh label="今日涨幅" col="today_pct_change" sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              </tr>
            </thead>
            <tbody>
              {sortedStocks.map((stock, idx) => {
                const grp = getGroup(stock, limitDownIds)
                const isLeader = stock.id === leader?.id
                return (
                  <tr
                    key={stock.id}
                    className="border-b border-bg-border/15 last:border-0 cursor-pointer hover:bg-bg-elevated transition-colors"
                    onClick={() => onClickStock(stock.code)}
                  >
                    <td className="px-4 py-2">
                      {isLeader
                        ? <Star className="w-3 h-3 text-dragon fill-dragon" />
                        : <span className="text-text-muted/80 font-mono">{idx + 1}</span>
                      }
                    </td>
                    <td className="px-2 py-2">
                      <div className={cn('font-medium', isLeader ? 'text-text-primary' : 'text-text-secondary')}>
                        {stock.name}
                      </div>
                      <div className="font-mono text-accent/90">{stock.code}</div>
                    </td>
                    <td className="px-2 py-2"><GroupTag group={grp} /></td>
                    {/* 连续连板 */}
                    <td className="px-2 py-2 font-mono text-right">
                      {(() => {
                        const isDown = limitDownIds.has(stock.id)
                        const cnt = isDown ? (stock.today_limit_down_count ?? 0) : (stock.today_board_count ?? 0)
                        if (!cnt) return <span className="text-text-muted/70">—</span>
                        return (
                          <span className={cn('font-bold px-1 py-px rounded',
                            cnt >= 3 ? (isDown ? 'bg-down/20 text-down' : 'text-dragon') : (isDown ? 'text-down/70' : 'text-up'),
                          )}>{cnt}板</span>
                        )
                      })()}
                    </td>
                    {/* 10日涨停 */}
                    <td className="px-2 py-2 font-mono text-right">
                      <span className={cn(
                        stock.limit_up_days_10d >= 3 ? 'text-dragon font-bold' :
                        stock.limit_up_days_10d >= 2 ? 'text-up' : 'text-text-secondary',
                      )}>{stock.limit_up_days_10d || '—'}</span>
                    </td>
                    {/* 20日涨停 */}
                    <td className="px-2 py-2 font-mono text-right">
                      <span className={cn(
                        stock.limit_up_days_20d >= 5 ? 'text-dragon font-bold' :
                        stock.limit_up_days_20d >= 3 ? 'text-up' : 'text-text-secondary',
                      )}>{stock.limit_up_days_20d || '—'}</span>
                    </td>
                    {/* 60日涨停 */}
                    <td className="px-2 py-2 font-mono text-right">
                      {stock.limit_up_days_60d > 0 ? (
                        <span className={cn(
                          stock.limit_up_days_60d >= 5 ? 'text-dragon font-bold' :
                          stock.limit_up_days_60d >= 3 ? 'text-up' : 'text-text-secondary',
                        )}>{stock.limit_up_days_60d}</span>
                      ) : <span className="text-text-muted/70">—</span>}
                    </td>
                    {/* 60日高板 */}
                    <td className="px-2 py-2 font-mono text-right">
                      <span className={cn(stock.board_count_60d >= 5 ? 'text-dragon font-bold' : 'text-text-secondary')}>
                        {stock.board_count_60d || '—'}
                      </span>
                    </td>
                    {/* 10日涨幅 */}
                    <td className="px-2 py-2 font-mono text-right">
                      {stock.pct_change_10d != null ? (
                        <span className={cn('font-medium', stock.pct_change_10d > 0 ? 'text-up' : stock.pct_change_10d < 0 ? 'text-down' : 'text-text-muted')}>
                          {stock.pct_change_10d > 0 ? '+' : ''}{stock.pct_change_10d.toFixed(1)}%
                        </span>
                      ) : <span className="text-text-muted">—</span>}
                    </td>
                    {/* 20日涨幅 */}
                    <td className="px-2 py-2 font-mono text-right">
                      {stock.pct_change_20d != null ? (
                        <span className={cn('font-medium', stock.pct_change_20d > 0 ? 'text-up' : stock.pct_change_20d < 0 ? 'text-down' : 'text-text-muted')}>
                          {stock.pct_change_20d > 0 ? '+' : ''}{stock.pct_change_20d.toFixed(1)}%
                        </span>
                      ) : <span className="text-text-muted">—</span>}
                    </td>
                    {/* 60日涨幅 */}
                    <td className="px-2 py-2 font-mono text-right">
                      {stock.pct_change_60d != null ? (
                        <span className={cn('font-medium', stock.pct_change_60d > 0 ? 'text-up' : stock.pct_change_60d < 0 ? 'text-down' : 'text-text-muted')}>
                          {stock.pct_change_60d > 0 ? '+' : ''}{stock.pct_change_60d.toFixed(1)}%
                        </span>
                      ) : <span className="text-text-muted">—</span>}
                    </td>
                    {/* 龙头分 */}
                    <td className="px-2 py-2 font-mono text-right">
                      <span className={cn(isLeader ? 'text-accent font-semibold' : 'text-text-secondary')}>
                        {stock.leader_score.toFixed(0)}
                      </span>
                    </td>
                    {/* 风险分 */}
                    <td className="px-2 py-2 font-mono text-right">
                      <span className={cn(stock.risk_score >= 50 ? 'text-down' : 'text-text-secondary')}>
                        {stock.risk_score.toFixed(0)}
                      </span>
                    </td>
                    {/* 今日涨幅 */}
                    <td className="px-2 py-2 font-mono text-right">
                      {stock.today_pct_change != null ? (
                        <span className={cn('font-bold', stock.today_pct_change > 0 ? 'text-up' : stock.today_pct_change < 0 ? 'text-down' : 'text-text-muted')}>
                          {stock.today_pct_change > 0 ? '+' : ''}{stock.today_pct_change.toFixed(2)}%
                        </span>
                      ) : <span className="text-text-muted">—</span>}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
