/**
 * 涨跌停分析 — 涨跌停池
 * 两个独立请求：涨停（非ST + is_limit_up）/ 跌停（非ST + pct_change≤-9.8）
 */
import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchLimitMoves } from '@/api/stocks'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { SectorTagList, LeaderTag, NegativeTag, SectorLeaderTag } from '@/components/common/SectorTags'
import { useSectorLeaders } from '@/hooks/useSectorLeaders'
import { Search, ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react'
import { cn } from '@/utils/cn'
import type { Stock } from '@/types'

// ─── Sort ─────────────────────────────────────────────────────────────────────

type SortKey = 'today_board_count' | 'today_pct_change' | 'limit_up_days_60d' | 'limit_up_days_20d' | 'limit_up_days_10d' | 'board_count_60d' | 'pct_change_10d' | 'pct_change_20d' | 'pct_change_60d' | 'leader_score' | 'risk_score'
type SortDir = 'asc' | 'desc'

function SortTh({ col, label, sortKey, sortDir, onSort }: {
  col: SortKey; label: string; sortKey: SortKey; sortDir: SortDir; onSort: (k: SortKey) => void
}) {
  const active = sortKey === col
  return (
    <th
      onClick={() => onSort(col)}
      className={cn(
        'px-3 py-2 text-xs font-medium cursor-pointer select-none group whitespace-nowrap text-right',
        active ? 'text-accent' : 'text-text-secondary/55 hover:text-text-secondary',
      )}
    >
      <span className="inline-flex items-center justify-end gap-0.5">
        {label}
        {active
          ? (sortDir === 'desc' ? <ChevronDown className="w-3 h-3 shrink-0" /> : <ChevronUp className="w-3 h-3 shrink-0" />)
          : <ChevronsUpDown className="w-3 h-3 shrink-0 opacity-0 group-hover:opacity-40 transition-opacity" />
        }
      </span>
    </th>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

type Tab = 'limit_up' | 'limit_down'

export default function LimitMovesPool() {
  const navigate  = useNavigate()
  const [search, setSearch]   = useState('')
  const [tab, setTab]         = useState<Tab>('limit_up')
  const [sortKey, setSortKey] = useState<SortKey>('today_board_count')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  // ── 两个独立请求 ──────────────────────────────────────────────────────────
  const { data: upData, isLoading: upLoading } = useQuery({
    queryKey: ['limit-moves-pool', 'limit_up', search],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up', search }),
    keepPreviousData: true,
  } as any)

  const { data: downData, isLoading: downLoading } = useQuery({
    queryKey: ['limit-moves-pool', 'limit_down', search],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down', search }),
    keepPreviousData: true,
  } as any)

  const limitUps:   Stock[] = (upData   as any)?.items ?? []
  const limitDowns: Stock[] = (downData as any)?.items ?? []

  const isLoading = tab === 'limit_up' ? upLoading : downLoading
  const baseList  = tab === 'limit_up' ? limitUps  : limitDowns

  // ── 板块龙头（完全支配，由板块分析页数据决定）────────────────────────────
  const sectorLeaders = useSectorLeaders()  // Map<stockId, primarySector>

  // ── Per-list leader maxes (0 if list is empty) ──────────────────────────────
  const leaderMaxes = useMemo(() => ({
    board: Math.max(0, ...baseList.map(s => (tab === 'limit_up' ? (s.today_board_count ?? 0) : (s.today_limit_down_count ?? 0)))),
    d10:   Math.max(0, ...baseList.map(s => s.limit_up_days_10d ?? 0)),
    d20:   Math.max(0, ...baseList.map(s => s.limit_up_days_20d ?? 0)),
    d60:   Math.max(0, ...baseList.map(s => s.limit_up_days_60d ?? 0)),
    high:  Math.max(0, ...baseList.map(s => (tab === 'limit_down' ? (s.board_down_count_60d ?? 0) : (s.board_count_60d ?? 0)))),
  }), [baseList, tab])

  const sorted = useMemo(() => [...baseList].sort((a, b) => {
    let av: number, bv: number
    if (sortKey === 'today_board_count' && tab === 'limit_down') {
      av = a.today_limit_down_count ?? -Infinity
      bv = b.today_limit_down_count ?? -Infinity
    } else if (sortKey === 'board_count_60d' && tab === 'limit_down') {
      av = a.board_down_count_60d ?? -Infinity
      bv = b.board_down_count_60d ?? -Infinity
    } else {
      av = (a[sortKey] ?? -Infinity) as number
      bv = (b[sortKey] ?? -Infinity) as number
    }
    return sortDir === 'desc' ? bv - av : av - bv
  }), [baseList, sortKey, sortDir, tab])

  const handleSort = (k: SortKey) => {
    if (sortKey === k) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'))
    else { setSortKey(k); setSortDir('desc') }
  }

  return (
    <div className="space-y-3 animate-fade-in">
      {/* Toolbar */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索股票..."
            className="bg-bg-card border border-bg-border rounded pl-8 pr-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent/50 w-44"
          />
        </div>

        {/* Tabs */}
        <div className="flex rounded border border-bg-border overflow-hidden text-xs">
          <button
            onClick={() => setTab('limit_up')}
            className={cn(
              'flex items-center gap-1 px-3 py-1.5 transition-colors',
              tab === 'limit_up' ? 'bg-up/15 text-up' : 'text-text-muted hover:text-text-secondary hover:bg-bg-elevated',
            )}
          >
            涨停
            <span className={cn('font-mono text-[10px]', tab === 'limit_up' ? 'text-up/70' : 'text-text-muted/50')}>
              {upLoading ? '…' : limitUps.length}
            </span>
          </button>
          <button
            onClick={() => setTab('limit_down')}
            className={cn(
              'flex items-center gap-1 px-3 py-1.5 transition-colors border-l border-bg-border',
              tab === 'limit_down' ? 'bg-down/15 text-down' : 'text-text-muted hover:text-text-secondary hover:bg-bg-elevated',
            )}
          >
            跌停
            <span className={cn('font-mono text-[10px]', tab === 'limit_down' ? 'text-down/70' : 'text-text-muted/50')}>
              {downLoading ? '…' : limitDowns.length}
            </span>
          </button>
        </div>

        <div className="ml-auto text-xs text-text-muted">
          {isLoading ? '加载中…' : `${sorted.length} 只`}
        </div>
      </div>

      {/* Table */}
      <div className="card overflow-hidden p-0">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10 bg-bg-card border-b border-bg-border/60">
            <tr>
              <th className="text-left px-3 py-2 text-xs text-text-muted font-medium w-8">#</th>
              <th className="text-left px-3 py-2 text-xs text-text-muted font-medium">股票</th>
              <th className="text-left px-3 py-2 text-xs text-text-muted font-medium">板块</th>
              <SortTh col="today_board_count" label="连续连板"  sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="limit_up_days_10d" label="10日涨停"  sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="limit_up_days_20d" label="20日涨停"  sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="limit_up_days_60d" label="60日涨停"  sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="board_count_60d"   label={tab === 'limit_down' ? '60日高跌' : '60日高板'}  sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="pct_change_10d"    label="10日涨幅"  sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="pct_change_20d"    label="20日涨幅"  sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="pct_change_60d"    label="60日涨幅"  sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="leader_score"      label="龙头分"    sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="risk_score"        label="风险分"    sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
              <SortTh col="today_pct_change"  label="今日涨幅"  sortKey={sortKey} sortDir={sortDir} onSort={handleSort} />
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={14} className="py-12 text-center text-text-muted text-sm"><LoadingRows /></td></tr>
            ) : sorted.length === 0 ? (
              <tr><td colSpan={14} className="py-12 text-center text-text-muted text-sm">暂无数据</td></tr>
            ) : sorted.map((stock, idx) => {
                const boardVal = tab === 'limit_up' ? (stock.today_board_count ?? 0) : (stock.today_limit_down_count ?? 0)
                const leaderTags: string[] = []
                const negTags:    string[] = []
                if (tab === 'limit_down') {
                  // 跌停 tab — consecutive limit-down means negative feedback
                  if (boardVal > 0 && boardVal === leaderMaxes.board) negTags.push('负反馈')
                  const highDown = stock.board_down_count_60d ?? 0
                  if (highDown > 0 && highDown === leaderMaxes.high) negTags.push('60连跌')
                } else {
                  // 涨停 tab — normal amber leader tags
                  if (boardVal > 0 && boardVal === leaderMaxes.board) leaderTags.push('连板龙')
                  if ((stock.board_count_60d ?? 0) > 0 && (stock.board_count_60d ?? 0) === leaderMaxes.high) leaderTags.push('60高板龙')
                }
                if ((stock.limit_up_days_10d ?? 0) > 0 && (stock.limit_up_days_10d ?? 0) === leaderMaxes.d10) leaderTags.push('10龙')
                if ((stock.limit_up_days_20d ?? 0) > 0 && (stock.limit_up_days_20d ?? 0) === leaderMaxes.d20) leaderTags.push('20龙')
                if ((stock.limit_up_days_60d ?? 0) > 0 && (stock.limit_up_days_60d ?? 0) === leaderMaxes.d60) leaderTags.push('60龙')
                const sectorName = sectorLeaders.get(stock.id)
                const leadSectors: string[] = sectorName ? [sectorName] : []
                return (
              <tr
                key={stock.id}
                className="border-b border-bg-border/25 last:border-0 cursor-pointer hover:bg-bg-elevated transition-colors"
                onClick={() => navigate(`/stocks/${stock.code}`)}
              >
                <td className="px-3 py-2.5 text-xs font-mono text-text-muted/50">{idx + 1}</td>
                <td className="px-3 py-2.5">
                  <div className="font-medium text-text-primary text-sm">{stock.name}</div>
                  <div className="text-xs font-mono text-accent/70">{stock.code}</div>
                  {(leaderTags.length > 0 || negTags.length > 0 || leadSectors.length > 0) && (
                    <div className="flex flex-wrap gap-0.5 mt-0.5">
                      {leaderTags.map(t => <LeaderTag key={t} label={t} />)}
                      {negTags.map(t => <NegativeTag key={t} label={t} />)}
                      {leadSectors.map(n => <SectorLeaderTag key={n} name={n} />)}
                    </div>
                  )}
                </td>
                <td className="px-3 py-2.5 max-w-[280px]">
                  <SectorTagList sectors={stock.sectors ?? []} max={4} />
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs">
                  {/* 涨停tab → 连续涨停数；跌停tab → 连续跌停数 */}
                  {(() => {
                    const cnt = tab === 'limit_up'
                      ? (stock.today_board_count ?? 0)
                      : (stock.today_limit_down_count ?? 0)
                    if (!cnt) return <span className="text-text-muted/40">—</span>
                    return (
                      <span className={cn(
                        'font-bold px-1 py-px rounded',
                        tab === 'limit_up'
                          ? cnt >= 3 ? 'text-dragon' : 'text-up'
                          : cnt >= 3 ? 'bg-down/20 text-down' : 'text-down/70',
                      )}>
                        {cnt}板
                      </span>
                    )
                  })()}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs">
                  <span className={cn(
                    stock.limit_up_days_10d >= 3 ? 'text-dragon font-bold' :
                    stock.limit_up_days_10d >= 2 ? 'text-up' : 'text-text-secondary',
                  )}>
                    {stock.limit_up_days_10d || '—'}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs">
                  <span className={cn(
                    stock.limit_up_days_20d >= 5 ? 'text-dragon font-bold' :
                    stock.limit_up_days_20d >= 3 ? 'text-up' : 'text-text-secondary',
                  )}>
                    {stock.limit_up_days_20d || '—'}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs">
                  <span className={cn(
                    stock.limit_up_days_60d >= 5 ? 'text-dragon font-bold' :
                    stock.limit_up_days_60d >= 3 ? 'text-up' : 'text-text-secondary',
                  )}>
                    {stock.limit_up_days_60d || '—'}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs">
                  {(() => {
                    const val = tab === 'limit_down' ? (stock.board_down_count_60d ?? 0) : stock.board_count_60d
                    if (!val) return <span className="text-text-muted/40">—</span>
                    return (
                      <span className={cn(
                        val >= 5
                          ? (tab === 'limit_down' ? 'text-down font-bold' : 'text-dragon font-bold')
                          : 'text-text-secondary',
                      )}>{val}</span>
                    )
                  })()}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs">
                  {stock.pct_change_10d != null ? (
                    <span className={cn('font-medium', stock.pct_change_10d > 0 ? 'text-up' : stock.pct_change_10d < 0 ? 'text-down' : 'text-text-muted')}>
                      {stock.pct_change_10d > 0 ? '+' : ''}{stock.pct_change_10d.toFixed(1)}%
                    </span>
                  ) : <span className="text-text-muted">—</span>}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs">
                  {stock.pct_change_20d != null ? (
                    <span className={cn('font-medium', stock.pct_change_20d > 0 ? 'text-up' : stock.pct_change_20d < 0 ? 'text-down' : 'text-text-muted')}>
                      {stock.pct_change_20d > 0 ? '+' : ''}{stock.pct_change_20d.toFixed(1)}%
                    </span>
                  ) : <span className="text-text-muted">—</span>}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs">
                  {stock.pct_change_60d != null ? (
                    <span className={cn('font-medium', stock.pct_change_60d > 0 ? 'text-up' : stock.pct_change_60d < 0 ? 'text-down' : 'text-text-muted')}>
                      {stock.pct_change_60d > 0 ? '+' : ''}{stock.pct_change_60d.toFixed(1)}%
                    </span>
                  ) : <span className="text-text-muted">—</span>}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs text-text-secondary">
                  {stock.leader_score.toFixed(0)}
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs">
                  <span className={cn(stock.risk_score >= 50 ? 'text-down' : 'text-text-secondary')}>
                    {stock.risk_score.toFixed(0)}
                  </span>
                </td>
                <td className="px-3 py-2.5 text-right font-mono text-xs">
                  {stock.today_pct_change != null ? (
                    <span className={cn('font-bold', stock.today_pct_change > 0 ? 'text-up' : 'text-down')}>
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
    </div>
  )
}
