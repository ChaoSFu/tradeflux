/**
 * Shared SectorSection component — used by both SectorPool page and Dashboard.
 * Renders a collapsible sector card with its full stock table.
 */
import { useState, useMemo, useCallback } from 'react'
import { Star, ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/utils/cn'
import type { Stock } from '@/types'
import { useSectorTags } from '@/hooks/useSectorTags'
import { SectorRankTags } from '@/components/common/SectorTags'

// ─── Group helpers ────────────────────────────────────────────────────────────

export type GroupKey = 'oscillating' | 'limit_up' | 'weakening' | 'broken'

export const GROUP_META: Record<GroupKey, { label: string; color: string; bg: string }> = {
  limit_up:    { label: '涨停龙头', color: '#FF4560', bg: 'rgba(255,69,96,0.12)'   },
  oscillating: { label: '震荡龙头', color: '#4F9CF9', bg: 'rgba(79,156,249,0.12)'  },
  weakening:   { label: '走弱龙头', color: '#F59E0B', bg: 'rgba(245,158,11,0.12)'  },
  broken:      { label: '破位龙头', color: '#26C281', bg: 'rgba(38,194,129,0.10)'  },
}

export const GROUP_ORDER: GroupKey[] = ['limit_up', 'oscillating', 'weakening', 'broken']
const GROUP_RANK: Record<GroupKey, number> = Object.fromEntries(
  GROUP_ORDER.map((k, i) => [k, i])
) as Record<GroupKey, number>

export function getGroup(stock: Stock): GroupKey {
  if (stock.today_is_limit_up) return 'limit_up'
  if (stock.phase === 'broken') return 'broken'
  if (stock.phase === 'weakening') return 'weakening'
  return 'oscillating'
}

export function sortByGroup(stocks: Stock[], pinned: GroupKey | null): Stock[] {
  return [...stocks].sort((a, b) => {
    const ga = getGroup(a), gb = getGroup(b)
    if (pinned) {
      if (ga === pinned && gb !== pinned) return -1
      if (gb === pinned && ga !== pinned) return  1
    }
    const rankDiff = GROUP_RANK[ga] - GROUP_RANK[gb]
    if (rankDiff !== 0) return rankDiff
    return b.leader_score - a.leader_score
  })
}

export function getSectorAvgPct(stocks: Stock[]): number {
  const vals = stocks
    .map((s) => s.today_pct_change)
    .filter((v): v is number => v != null)
  if (!vals.length) return 0
  return vals.reduce((a, b) => a + b, 0) / vals.length
}

export function GroupTag({ group }: { group: GroupKey }) {
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

// ─── SectorGroup ──────────────────────────────────────────────────────────────

export interface SectorGroup {
  name: string
  stocks: Stock[]
}

export function buildSectorGroups(stocks: Stock[]): SectorGroup[] {
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
  groups.sort((a, b) =>
    b.stocks.length !== a.stocks.length
      ? b.stocks.length - a.stocks.length
      : (b.stocks[0]?.leader_score ?? 0) - (a.stocks[0]?.leader_score ?? 0),
  )
  return groups
}

// ─── SectorSection component ──────────────────────────────────────────────────

export function SectorSection({
  group,
  collapsed,
  onToggle,
  onClickStock,
}: {
  group: SectorGroup
  collapsed: boolean
  onToggle: () => void
  onClickStock: (code: string) => void
}) {
  const { name, stocks } = group
  const [pinnedGroup, setPinnedGroup] = useState<GroupKey | null>(null)
  const { byName: sectorTagsByName } = useSectorTags()
  const tagData = sectorTagsByName.get(name)

  const sortedStocks = useMemo(() => sortByGroup(stocks, pinnedGroup), [stocks, pinnedGroup])
  const leader = sortedStocks[0]

  const groupCounts = stocks.reduce(
    (acc, s) => { acc[getGroup(s)] = (acc[getGroup(s)] ?? 0) + 1; return acc },
    {} as Record<GroupKey, number>,
  )
  const dominantGroup = (Object.entries(groupCounts).sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'oscillating') as GroupKey
  const accentColor = GROUP_META[dominantGroup].color

  const upCount      = stocks.filter((s) => (s.today_pct_change ?? 0) > 0).length
  const downCount    = stocks.filter((s) => (s.today_pct_change ?? 0) < 0).length
  const limitUpCount = stocks.filter((s) => s.today_is_limit_up).length
  const limitDnCount = stocks.filter((s) => (s.today_pct_change ?? 0) <= -9.8).length
  const avgPct       = getSectorAvgPct(stocks)

  const handleTagClick = useCallback((e: React.MouseEvent, key: GroupKey) => {
    e.stopPropagation()
    setPinnedGroup((prev) => (prev === key ? null : key))
  }, [])

  return (
    <div className="card overflow-hidden p-0" style={{ borderColor: `${accentColor}20` }}>
      {/* Sector header */}
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
        {tagData && (
          <div className="flex flex-wrap gap-1">
            <SectorRankTags tagData={tagData} />
          </div>
        )}
        {leader && (
          <span className="flex items-center gap-1 text-xs text-text-muted ml-1">
            <Star className="w-3 h-3 text-dragon fill-dragon shrink-0" />
            <span className="text-dragon font-medium">{leader.name}</span>
            <span className="text-text-muted/85 font-mono">{leader.code}</span>
          </span>
        )}
        <div className="ml-auto flex items-center gap-3">
          {/* Group distribution tags */}
          <span className="flex items-center gap-1.5 text-xs font-mono">
            {GROUP_ORDER.map((key) => {
              const cnt = groupCounts[key] ?? 0
              if (!cnt) return null
              const { label, color } = GROUP_META[key]
              const active = pinnedGroup === key
              return (
                <button
                  key={key}
                  onClick={(e) => handleTagClick(e, key)}
                  className="px-1.5 py-px rounded font-medium whitespace-nowrap transition-all"
                  style={{
                    color,
                    backgroundColor: active ? `${color}30` : `${color}18`,
                    border: `1px solid ${active ? color : `${color}30`}`,
                    boxShadow: active ? `0 0 0 1px ${color}40` : undefined,
                  }}
                >
                  {label.replace('龙头', '')} {cnt}
                </button>
              )
            })}
          </span>
          {/* avg pct */}
          <span className={cn(
            'text-xs font-mono font-semibold px-1.5 py-px rounded',
            avgPct > 0 ? 'text-up bg-up/10' : avgPct < 0 ? 'text-down bg-down/10' : 'text-text-muted',
          )}>
            {avgPct > 0 ? '+' : ''}{avgPct.toFixed(2)}%
          </span>
          {/* up/down */}
          <span className="flex items-center gap-1 text-xs font-mono">
            <span className="text-up">{upCount}涨</span>
            <span className="text-text-muted/70">/</span>
            <span className="text-down">{downCount}跌</span>
          </span>
          {/* limit up/down */}
          {(limitUpCount > 0 || limitDnCount > 0) && (
            <span className="flex items-center gap-1 text-xs font-mono">
              {limitUpCount > 0 && (
                <span className="px-1.5 py-px rounded bg-up/15 text-up font-semibold">
                  {limitUpCount}涨停
                </span>
              )}
              {limitDnCount > 0 && (
                <span className="px-1.5 py-px rounded bg-down/15 text-down font-semibold">
                  {limitDnCount}跌停
                </span>
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
                <th className="text-left  px-4 py-1.5 text-text-secondary/70 font-medium w-8">#</th>
                <th className="text-left  px-2 py-1.5 text-text-secondary/70 font-medium w-28">股票</th>
                <th className="text-left  px-2 py-1.5 text-text-secondary/70 font-medium w-20">分组</th>
                <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">连续连板</th>
                <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">10日涨停</th>
                <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">20日涨停</th>
                <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">60日涨停</th>
                <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">60日高板</th>
                <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">10日涨幅</th>
                <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">20日涨幅</th>
                <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">60日涨幅</th>
                <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium w-14">龙头分</th>
                <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium w-14">风险分</th>
                <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">今日涨幅</th>
              </tr>
            </thead>
            <tbody>
              {sortedStocks.map((stock, idx) => {
                const grp = getGroup(stock)
                const isLeader = idx === 0
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
                        const cnt = stock.today_board_count ?? 0
                        if (!cnt) return <span className="text-text-muted/70">—</span>
                        return <span className={cn('font-bold', cnt >= 3 ? 'text-dragon' : 'text-up')}>{cnt}板</span>
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
