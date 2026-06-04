/**
 * 涨跌停分析 — 板块分布
 * 两个独立请求：涨停（非ST + is_limit_up）/ 跌停（非ST + pct_change≤-9.8）
 * 合并去重后按板块分组展示。
 */
import { useMemo, useState, useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchLimitMoves } from '@/api/stocks'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { Search, Star, ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/utils/cn'
import type { Stock } from '@/types'
import { SortTh, type StockSortKey } from '@/components/common/SectorSection'

// ─── Group ────────────────────────────────────────────────────────────────────

type GroupKey = 'limit_up' | 'limit_down'

const GROUP_META: Record<GroupKey, { label: string; color: string; bg: string }> = {
  limit_up:   { label: '涨停', color: '#FF4560', bg: 'rgba(255,69,96,0.12)'  },
  limit_down: { label: '跌停', color: '#26C281', bg: 'rgba(38,194,129,0.10)' },
}

const GROUP_ORDER: GroupKey[] = ['limit_up', 'limit_down']
const GROUP_RANK: Record<GroupKey, number> = { limit_up: 0, limit_down: 1 }

function getGroup(stock: Stock): GroupKey {
  return stock.today_is_limit_up ? 'limit_up' : 'limit_down'
}

function sortByGroup(stocks: Stock[], pinned: GroupKey | null): Stock[] {
  return [...stocks].sort((a, b) => {
    const ga = getGroup(a), gb = getGroup(b)
    if (pinned) {
      if (ga === pinned && gb !== pinned) return -1
      if (gb === pinned && ga !== pinned) return  1
    }
    const rd = GROUP_RANK[ga] - GROUP_RANK[gb]
    return rd !== 0 ? rd : b.board_count_60d - a.board_count_60d
  })
}

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

// ─── Sector group ─────────────────────────────────────────────────────────────

interface SectorGroup { name: string; stocks: Stock[] }

function buildSectorGroups(stocks: Stock[]): SectorGroup[] {
  const map = new Map<string, Stock[]>()
  for (const stock of stocks) {
    for (const sector of stock.sectors ?? []) {
      if (!map.has(sector)) map.set(sector, [])
      map.get(sector)!.push(stock)
    }
  }
  const groups: SectorGroup[] = []
  for (const [name, s] of map) {
    groups.push({ name, stocks: sortByGroup(s, null) })
  }
  groups.sort((a, b) => {
    const aUp = a.stocks.filter((s) => s.today_is_limit_up).length
    const bUp = b.stocks.filter((s) => s.today_is_limit_up).length
    return bUp !== aUp ? bUp - aUp : b.stocks.length - a.stocks.length
  })
  return groups
}

// ─── Sector sort ──────────────────────────────────────────────────────────────

type SortKey = 'total' | 'limit_up' | 'limit_down'

const SORT_DEFS: { key: SortKey; label: string }[] = [
  { key: 'total',      label: '总数' },
  { key: 'limit_up',   label: '涨停' },
  { key: 'limit_down', label: '跌停' },
]

function getSectorStat(sg: SectorGroup, key: SortKey): number {
  switch (key) {
    case 'total':      return sg.stocks.length
    case 'limit_up':   return sg.stocks.filter((s) => s.today_is_limit_up).length
    case 'limit_down': return sg.stocks.filter((s) => !s.today_is_limit_up).length
  }
}

// ─── Min filter (persisted) ───────────────────────────────────────────────────

const LS_MIN_KEY = 'tradeflux:limit_moves_min_stocks'
const DEFAULT_MIN = 2

function useMinStocks() {
  const [min, setMin] = useState<number>(() => {
    try {
      const v = parseInt(localStorage.getItem(LS_MIN_KEY) ?? '', 10)
      return isNaN(v) || v < 1 ? DEFAULT_MIN : v
    } catch { return DEFAULT_MIN }
  })
  const update = (v: number) => {
    setMin(v)
    try { localStorage.setItem(LS_MIN_KEY, String(v)) } catch { /* ignore */ }
  }
  return [min, update] as const
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function LimitMovesSectors() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [minStocks, setMinStocks] = useMinStocks()
  const [sortKey, setSortKey] = useState<SortKey>('limit_up')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const handleSortClick = (key: SortKey) => {
    if (sortKey === key) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    else { setSortKey(key); setSortDir('desc') }
  }

  // ── 两个独立请求 ──────────────────────────────────────────────────────────
  const { data: upData, isLoading: upLoading } = useQuery({
    queryKey: ['limit-moves-sectors', 'limit_up'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up' }),
  } as any)

  const { data: downData, isLoading: downLoading } = useQuery({
    queryKey: ['limit-moves-sectors', 'limit_down'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down' }),
  } as any)

  const isLoading = upLoading || downLoading

  // 合并去重（同一只股票可能同时出现在两个结果中的边界情况）
  const allStocks: Stock[] = useMemo(() => {
    const seen = new Set<number>()
    const merged: Stock[] = []
    for (const s of [
      ...((upData as any)?.items ?? []),
      ...((downData as any)?.items ?? []),
    ]) {
      if (!seen.has(s.id)) { seen.add(s.id); merged.push(s) }
    }
    return merged
  }, [upData, downData])

  const groups = useMemo(() => {
    let g = buildSectorGroups(allStocks)
    g = g.filter((sg) => sg.stocks.length >= minStocks)
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      g = g
        .map((sg) => ({
          ...sg,
          stocks: sg.stocks.filter((s) => s.name.includes(q) || s.code.includes(q)),
        }))
        .filter((sg) => sg.stocks.length > 0 || sg.name.toLowerCase().includes(q))
    }
    g = [...g].sort((a, b) => {
      const av = getSectorStat(a, sortKey), bv = getSectorStat(b, sortKey)
      if (av !== bv) return sortDir === 'desc' ? bv - av : av - bv
      return b.stocks.length - a.stocks.length
    })
    return g
  }, [allStocks, minStocks, search, sortKey, sortDir])

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
          <span className="px-2.5 py-1.5 text-text-muted whitespace-nowrap bg-bg-elevated/50">涨跌停股≥</span>
          <input
            type="number" min={1} value={minStocks}
            onChange={(e) => { const v = parseInt(e.target.value, 10); if (!isNaN(v) && v >= 1) setMinStocks(v) }}
            className="w-12 py-1.5 text-center bg-transparent border-l border-bg-border/60 font-mono text-accent focus:outline-none"
          />
          <span className="px-1.5 py-1.5 text-text-muted/85 whitespace-nowrap">只</span>
        </div>
        <div className="text-xs text-text-muted ml-auto">
          {isLoading ? '加载中…' : `${groups.length} 个板块 · ${displayedStockCount} 只`}
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
                : <ChevronUp   className="w-3 h-3 shrink-0" />
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

// ─── Sector section ───────────────────────────────────────────────────────────

function SectorSection({ group, collapsed, onToggle, onClickStock }: {
  group: SectorGroup; collapsed: boolean; onToggle: () => void; onClickStock: (code: string) => void
}) {
  const { name, stocks } = group
  const [pinnedGroup, setPinnedGroup] = useState<GroupKey | null>(null)
  const [sortKey, setSortKey] = useState<StockSortKey | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const sortedStocks = useMemo(() => {
    if (sortKey) {
      return [...stocks].sort((a, b) => {
        const av = (a[sortKey] as number | null | undefined) ?? -Infinity
        const bv = (b[sortKey] as number | null | undefined) ?? -Infinity
        return sortDir === 'desc' ? bv - av : av - bv
      })
    }
    return sortByGroup(stocks, pinnedGroup)
  }, [stocks, pinnedGroup, sortKey, sortDir])

  const leader = useMemo(() => sortByGroup(stocks, null)[0], [stocks])

  const handleSort = (k: StockSortKey) => {
    if (sortKey === k) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    else { setSortKey(k); setSortDir('desc') }
  }

  const groupCounts = stocks.reduce(
    (acc, s) => { acc[getGroup(s)] = (acc[getGroup(s)] ?? 0) + 1; return acc },
    {} as Record<GroupKey, number>,
  )

  const limitUpCnt = groupCounts['limit_up']   ?? 0
  const limitDnCnt = groupCounts['limit_down']  ?? 0
  const accentColor = limitUpCnt >= limitDnCnt ? GROUP_META.limit_up.color : GROUP_META.limit_down.color

  const handleTagClick = useCallback((e: React.MouseEvent, key: GroupKey) => {
    e.stopPropagation()
    setPinnedGroup((prev) => (prev === key ? null : key))
  }, [])

  return (
    <div className="card overflow-hidden p-0" style={{ borderColor: `${accentColor}20` }}>
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
          <span className="flex items-center gap-1 text-xs text-text-muted ml-1">
            <Star className="w-3 h-3 text-dragon fill-dragon shrink-0" />
            <span className="text-dragon font-medium">{leader.name}</span>
            <span className="text-text-muted/85 font-mono">{leader.code}</span>
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {GROUP_ORDER.map((key) => {
            const cnt = groupCounts[key] ?? 0
            if (!cnt) return null
            const { label, color } = GROUP_META[key]
            const active = pinnedGroup === key
            return (
              <button
                key={key}
                onClick={(e) => handleTagClick(e, key)}
                className="px-1.5 py-px rounded text-xs font-medium whitespace-nowrap transition-all"
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
          {collapsed
            ? <ChevronDown className="w-3.5 h-3.5 text-text-muted shrink-0" />
            : <ChevronUp   className="w-3.5 h-3.5 text-text-muted shrink-0" />
          }
        </div>
      </button>

      {!collapsed && (
        <div className="border-t border-bg-border/30">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-bg-border/20 bg-bg-elevated/40">
                <th className="text-left  px-4 py-1.5 text-text-secondary/70 font-medium w-8">#</th>
                <th className="text-left  px-2 py-1.5 text-text-secondary/70 font-medium">股票</th>
                <th className="text-left  px-2 py-1.5 text-text-secondary/70 font-medium w-14">类型</th>
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
              {sortedStocks.map((stock, idx) => (
                <tr
                  key={stock.id}
                  className="border-b border-bg-border/15 last:border-0 cursor-pointer hover:bg-bg-elevated transition-colors"
                  onClick={() => onClickStock(stock.code)}
                >
                  <td className="px-4 py-2">
                    {stock.id === leader?.id
                      ? <Star className="w-3 h-3 text-dragon fill-dragon" />
                      : <span className="text-text-muted/80 font-mono">{idx + 1}</span>
                    }
                  </td>
                  <td className="px-2 py-2">
                    <div className={cn('font-medium', stock.id === leader?.id ? 'text-text-primary' : 'text-text-secondary')}>
                      {stock.name}
                    </div>
                    <div className="font-mono text-accent/70">{stock.code}</div>
                  </td>
                  <td className="px-2 py-2"><GroupTag group={getGroup(stock)} /></td>
                  {/* 连续连板 */}
                  <td className="px-2 py-2 font-mono text-right">
                    {(() => {
                      const isUp = stock.today_is_limit_up
                      const cnt = isUp ? (stock.today_board_count ?? 0) : (stock.today_limit_down_count ?? 0)
                      if (!cnt) return <span className="text-text-muted/70">—</span>
                      return (
                        <span className={cn('font-bold px-1 py-px rounded',
                          cnt >= 3 ? (isUp ? 'text-dragon' : 'bg-down/20 text-down') : (isUp ? 'text-up' : 'text-down/70'),
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
                    <span className={cn(idx === 0 ? 'text-accent font-semibold' : 'text-text-secondary')}>
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
                      <span className={cn('font-bold', stock.today_pct_change > 0 ? 'text-up' : 'text-down')}>
                        {stock.today_pct_change > 0 ? '+' : ''}{stock.today_pct_change.toFixed(2)}%
                      </span>
                    ) : <span className="text-text-muted">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
