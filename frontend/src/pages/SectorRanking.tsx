/**
 * 板块涨幅排名 Sector Ranking
 * 7 列打标（5日/10日/20日/60日涨幅 + 涨停/连板/强势股），各取前5（值>0），可并列。
 * 默认排序：tag 数越多越靠前；tag 数相同时按列优先级 & 龙位优先级排序。
 */
import { useMemo, useState, Fragment, useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchSectors, fetchSectorTopStocks } from '@/api/sectors'
import type { TopStockItem } from '@/api/sectors'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { cn } from '@/utils/cn'
import type { Sector } from '@/types'

// ─── Tag columns (优先级从高到低) ─────────────────────────────────────────────

type TagKey = '5d' | '10d' | '20d' | '60d' | 'lu' | 'board' | 'strong'
type ViewMode = 'trend' | 'emotion' | 'all'

const TAG_COLS: Array<{ key: TagKey; label: string; rankField: keyof Sector; pctField?: keyof Sector; view: ViewMode }> = [
  { key: '5d',     label: '5日',  rankField: 'rank_5d',     pctField: 'pct_change_5d',  view: 'trend'   },
  { key: '10d',    label: '10日', rankField: 'rank_10d',    pctField: 'pct_change_10d', view: 'trend'   },
  { key: '20d',    label: '20日', rankField: 'rank_20d',    pctField: 'pct_change_20d', view: 'trend'   },
  { key: '60d',    label: '60日', rankField: 'rank_60d',    pctField: 'pct_change_60d', view: 'trend'   },
  { key: 'lu',     label: '涨停', rankField: 'rank_lu',                                  view: 'emotion' },
  { key: 'board',  label: '连板', rankField: 'rank_board',                               view: 'emotion' },
  { key: 'strong', label: '强势', rankField: 'rank_strong',                              view: 'emotion' },
]

// 仅用于渲染涨幅列（不含涨停/连板/强势）
const PCT_COLS = TAG_COLS.filter(c => c.pctField)

// ─── Sort ─────────────────────────────────────────────────────────────────────

type SortKey = 'tags' | 'name' | 'today' | '5d' | '10d' | '20d' | '60d'
             | 'limit_up' | 'limit_down' | 'strong' | 'board'

// ─── Color helpers ────────────────────────────────────────────────────────────

function pctColor(v: number) {
  if (v > 0) return 'text-up'
  if (v < 0) return 'text-down'
  return 'text-text-muted'
}

function pctStr(v: number) {
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

// ─── Rank tag (金/银/铜/蓝/绿) ────────────────────────────────────────────────

const RANK_STYLES = [
  { color: '#FFD700', bg: 'rgba(255,215,0,0.14)',   border: 'rgba(255,215,0,0.40)'   },
  { color: '#C8C8C8', bg: 'rgba(200,200,200,0.12)', border: 'rgba(200,200,200,0.35)' },
  { color: '#CD7F32', bg: 'rgba(205,127,50,0.14)',  border: 'rgba(205,127,50,0.40)'  },
  { color: '#5EA6FF', bg: 'rgba(94,166,255,0.12)',  border: 'rgba(94,166,255,0.35)'  },
  { color: '#4ADE80', bg: 'rgba(74,222,128,0.10)',  border: 'rgba(74,222,128,0.30)'  },
]

function RankTag({ label, rank }: { label: string; rank: number }) {
  const style = RANK_STYLES[rank - 1]
  if (!style) return null
  return (
    <span
      className="inline-flex items-center px-1 py-px text-[9px] font-bold rounded whitespace-nowrap leading-tight"
      style={{ color: style.color, backgroundColor: style.bg, border: `1px solid ${style.border}` }}
    >
      {label}龙{rank}
    </span>
  )
}

// ─── Risk tag：跌停负反馈警告（不参与排序，limit_down_count>0 均显示） ──────────

function RiskTag({ count }: { count: number }) {
  return (
    <span
      className="inline-flex items-center gap-0.5 px-1 py-px text-[9px] font-bold rounded whitespace-nowrap leading-tight"
      style={{
        color: '#FF4560',
        backgroundColor: 'rgba(255,69,96,0.15)',
        border: '1px solid rgba(255,69,96,0.45)',
      }}
      title={`跌停 ${count} 只，负反馈风险较大`}
    >
      ⚠ 跌停×{count}
    </span>
  )
}

// ─── Top Stocks Panel（板块内主板近20日前N名，参考 LimitMovesSectors 风格）──────

function fmt(v: number | null, digits = 2) {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}

function PctTd({ v }: { v: number | null }) {
  if (v == null) return <td className="px-2 py-1.5 font-mono text-right text-text-muted/50">—</td>
  const cls = v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-muted'
  return (
    <td className={cn('px-2 py-1.5 font-mono text-right font-medium', cls)}>
      {fmt(v)}
    </td>
  )
}

function TopStocksPanel({ bkCode, onClickStock }: { bkCode: string; onClickStock: (code: string) => void }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['sector-top-stocks', bkCode],
    queryFn: () => fetchSectorTopStocks(bkCode, '20d', 10),
    staleTime: 5 * 60 * 1000,  // 5分钟缓存
  })

  console.log('[TopStocksPanel]', { bkCode, isLoading, isError, error, stockCount: data?.stocks?.length })

  if (isLoading) return (
    <div className="px-4 py-3 border-t border-bg-border/30 text-xs text-text-muted">
      加载中… ({bkCode})
    </div>
  )
  if (isError) return (
    <div className="px-4 py-3 text-xs text-down border-t border-bg-border/30">
      请求失败: {String(error)}
    </div>
  )
  if (!data?.stocks.length) return (
    <div className="px-4 py-3 text-xs text-text-muted border-t border-bg-border/30">
      暂无主板数据 (bkCode={bkCode})
    </div>
  )

  return (
    <div className="border-t border-bg-border/30">
      <div className="px-4 py-1.5 text-[10px] text-text-muted/60 bg-bg-elevated/30">
        主板非ST · 近20日涨幅前{data.stocks.length}名
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-bg-border/20 bg-bg-elevated/40">
            <th className="text-left px-4 py-1.5 text-text-secondary/70 font-medium w-8">#</th>
            <th className="text-left px-2 py-1.5 text-text-secondary/70 font-medium">股票</th>
            <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">今日</th>
            <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">5日涨幅</th>
            <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">10日涨幅</th>
            <th className="text-right px-2 py-1.5 text-accent font-medium whitespace-nowrap">20日涨幅↓</th>
            <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium whitespace-nowrap">60日涨幅</th>
          </tr>
        </thead>
        <tbody>
          {data.stocks.map((s: TopStockItem, idx: number) => (
            <tr
              key={s.code}
              className="border-b border-bg-border/15 last:border-0 cursor-pointer hover:bg-bg-elevated transition-colors"
              onClick={() => onClickStock(s.code)}
            >
              <td className="px-4 py-1.5 text-text-muted/60 font-mono">{idx + 1}</td>
              <td className="px-2 py-1.5">
                <div className={cn('font-medium', idx === 0 ? 'text-text-primary' : 'text-text-secondary')}>
                  {s.name}
                </div>
                <div className="font-mono text-accent/70 text-[10px]">{s.code}</div>
              </td>
              <PctTd v={s.pct_today} />
              <PctTd v={s.pct_5d} />
              <PctTd v={s.pct_10d} />
              <td className={cn('px-2 py-1.5 font-mono text-right font-bold',
                s.pct_20d != null && s.pct_20d > 0 ? 'text-up' : s.pct_20d != null && s.pct_20d < 0 ? 'text-down' : 'text-text-muted'
              )}>
                {fmt(s.pct_20d)}
              </td>
              <PctTd v={s.pct_60d} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ─── Composite tag sort score ─────────────────────────────────────────────────

function getSectorRank(s: Sector, key: TagKey): number {
  const field = TAG_COLS.find(c => c.key === key)!.rankField
  return (s[field] as number | null) ?? 999
}

/**
 * 返回排序比较数组: [tagCount, ...TAG_COLS 按优先级, 龙位越小越好]
 * 用于 lexicographic 比较。直接读后端返回的 rank 字段。
 */
function getSortVector(s: Sector, cols: typeof TAG_COLS): number[] {
  const ranks = cols.map(c => getSectorRank(s, c.key))
  const tagCount = ranks.filter(r => r < 999).length
  return [tagCount, ...ranks.map(r => -r)]
}

// ─── Page ─────────────────────────────────────────────────────────────────────

interface SectorRankingProps {
  // 固定视图模式（传入时隐藏 Tab 切换和全局过滤控件）
  fixedView?: ViewMode
  // 仅显示指定生命周期阶段(0-6)的板块；null/undefined 不过滤
  phaseFilter?: number | null
}

export default function SectorRanking({ fixedView, phaseFilter }: SectorRankingProps = {}) {
  const navigate = useNavigate()
  const [sortKey, setSortKey] = useState<SortKey>('tags')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [expandedCode, setExpandedCode] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>(fixedView ?? 'trend')

  const { data, isLoading } = useQuery({
    queryKey: ['sectors-ranking'],
    queryFn: fetchSectors,
  })

  const allSectors: Sector[] = data?.items ?? []

  // 当前视图模式下参与排名和过滤的 tag 列
  const activeCols = TAG_COLS.filter(c => c.view === viewMode)

  // 后端已过滤 is_watched=true；按选中的生命周期阶段过滤（来自分布条点击）
  const sectors = phaseFilter != null ? allSectors.filter(s => s.phase === phaseFilter) : allSectors


  const sorted = useMemo(() => {
    const getNum = (s: Sector): number => {
      switch (sortKey) {
        case 'today':     return s.pct_change_30d
        case '5d':        return s.pct_change_5d
        case '10d':       return s.pct_change_10d
        case '20d':       return s.pct_change_20d
        case '60d':       return s.pct_change_60d
        case 'limit_up':  return s.limit_up_count
        case 'limit_down':return s.limit_down_count
        case 'strong':    return s.strong_stock_count
        case 'board':     return s.board_height
        default:          return 0
      }
    }

    return [...sectors].sort((a, b) => {
      if (sortKey === 'name') {
        const c = a.name.localeCompare(b.name)
        return sortDir === 'asc' ? c : -c
      }
      if (sortKey === 'tags') {
        const va = getSortVector(a, activeCols)
        const vb = getSortVector(b, activeCols)
        for (let i = 0; i < va.length; i++) {
          if (va[i] !== vb[i]) return vb[i] - va[i]
        }
        return 0
      }
      const diff = getNum(b) - getNum(a)
      return sortDir === 'desc' ? diff : -diff
    })
  }, [sectors, sortKey, sortDir, viewMode])

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(key); setSortDir('desc') }
  }

  function SortTh({ label, sk, right }: { label: string; sk: SortKey; right?: boolean }) {
    const active = sortKey === sk
    return (
      <th
        className={cn(
          'px-3 py-2 text-xs font-medium cursor-pointer select-none whitespace-nowrap transition-colors',
          right ? 'text-right' : 'text-left',
          active ? 'text-accent' : 'text-text-secondary/70 hover:text-text-secondary',
        )}
        onClick={() => handleSort(sk)}
      >
        <span className="inline-flex items-center gap-0.5">
          {label}
          {active && sortKey !== 'tags' && (sortDir === 'desc'
            ? <ChevronDown className="w-3 h-3 shrink-0" />
            : <ChevronUp   className="w-3 h-3 shrink-0" />
          )}
        </span>
      </th>
    )
  }

  return (
    <div className="space-y-3 animate-fade-in">

      {/* Top bar */}
      <div className="flex items-center gap-3 flex-wrap">
        {sortKey !== 'tags' && (
          <button
            onClick={() => { setSortKey('tags'); setSortDir('desc') }}
            className="text-xs px-2.5 py-1 rounded border border-border text-text-muted hover:text-text-secondary hover:bg-bg-elevated transition-colors"
          >
            恢复默认排序
          </button>
        )}
      </div>

      {/* Table */}
      <div className="card overflow-hidden p-0">
        {isLoading ? (
          <div className="p-4"><LoadingRows /></div>
        ) : sorted.length === 0 ? (
          <div className="py-16 text-center text-text-muted text-sm">暂无数据</div>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-bg-border/30 bg-bg-elevated/50">
                <th className="text-left px-3 py-2 text-text-secondary/70 font-medium w-6">#</th>
                <SortTh label="板块" sk="name" />
                <th className="text-left px-3 py-2 text-text-secondary/70 font-medium whitespace-nowrap">龙头股</th>
                <SortTh label="今日"     sk="today"     right />
                <SortTh label="5日涨幅"  sk="5d"        right />
                <SortTh label="10日涨幅" sk="10d"       right />
                <SortTh label="20日涨幅" sk="20d"       right />
                <SortTh label="60日涨幅" sk="60d"       right />
                <SortTh label="涨停"     sk="limit_up"  right />
                <SortTh label="跌停"     sk="limit_down" right />
                <SortTh label="强势股"   sk="strong"    right />
                <SortTh label="连板高度" sk="board"     right />
                <th className="w-8" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((s, idx) => {
                // 只展示当前视图模式下的 tag（趋势模式不显示涨停/连板/强势 tag）
                const sectorTags = activeCols
                  .map(c => {
                    const rank = s[c.rankField] as number | null
                    return rank != null ? { key: c.key, label: c.label, rank } : null
                  })
                  .filter((t): t is { key: TagKey; label: string; rank: number } => t !== null)

                const hasPositiveTags = sectorTags.length > 0
                const isExpanded = expandedCode === s.code

                return (
                  <Fragment key={s.id}>
                  <tr
                    className={cn(
                      'hover:bg-bg-elevated/60 transition-colors',
                      isExpanded ? 'bg-accent/5 border-b-0' : 'border-b border-bg-border/15',
                    )}
                  >
                    <td className="px-3 py-2 text-text-muted/60 font-mono">{idx + 1}</td>

                    {/* 板块名 + 正向强势标签 + 跌停风险警告 */}
                    <td className="px-3 py-2">
                      <div className="font-semibold text-text-primary leading-tight">{s.name}</div>
                      {(sectorTags.length > 0 || s.limit_down_count > 0) && (
                        <div className="flex flex-wrap gap-0.5 mt-0.5">
                          {sectorTags.map(t => (
                            <RankTag key={t.key} label={t.label} rank={t.rank} />
                          ))}
                          {s.limit_down_count > 0 && (
                            <RiskTag count={s.limit_down_count} />
                          )}
                        </div>
                      )}
                    </td>

                    {/* 龙头股 */}
                    <td className="px-3 py-2">
                      {s.leader_stock_name
                        ? <span className="text-dragon font-medium">{s.leader_stock_name}</span>
                        : <span className="text-text-muted/40">—</span>
                      }
                    </td>

                    {/* 今日涨幅 */}
                    <td className="px-3 py-2 text-right">
                      <span className={cn('font-mono font-medium', pctColor(s.pct_change_30d))}>
                        {pctStr(s.pct_change_30d)}
                      </span>
                    </td>

                    {/* 5/10/20/60 日涨幅（纯数字） */}
                    {PCT_COLS.map(({ key, pctField }) => (
                      <td key={key} className="px-3 py-2 text-right">
                        <span className={cn('font-mono font-medium', pctColor(s[pctField!] as number))}>
                          {pctStr(s[pctField!] as number)}
                        </span>
                      </td>
                    ))}

                    {/* 涨停 */}
                    <td className="px-3 py-2 text-right font-mono">
                      {s.limit_up_count > 0
                        ? <span className="text-up font-semibold">{s.limit_up_count}</span>
                        : <span className="text-text-muted/40">—</span>
                      }
                    </td>

                    {/* 跌停 */}
                    <td className="px-3 py-2 text-right font-mono">
                      {s.limit_down_count > 0
                        ? <span className="text-down font-semibold">{s.limit_down_count}</span>
                        : <span className="text-text-muted/40">—</span>
                      }
                    </td>

                    {/* 强势股 */}
                    <td className="px-3 py-2 text-right font-mono">
                      {s.strong_stock_count > 0
                        ? <span className="text-text-primary">{s.strong_stock_count}</span>
                        : <span className="text-text-muted/40">—</span>
                      }
                    </td>

                    {/* 连板高度 */}
                    <td className="px-3 py-2 text-right font-mono">
                      {s.board_height > 0 ? (
                        <span className={cn(s.board_height >= 5 ? 'text-dragon font-bold' : 'text-up')}>
                          {s.board_height}板
                        </span>
                      ) : <span className="text-text-muted/40">—</span>}
                    </td>

                    {/* 展开按钮（只有正向 tag 的板块才有） */}
                    <td className="px-2 py-2 text-center">
                      {hasPositiveTags && (
                        <button
                          onClick={() => { console.log('[expand click]', s.code, isExpanded); setExpandedCode(isExpanded ? null : s.code) }}
                          className={cn(
                            'p-0.5 rounded transition-colors',
                            isExpanded
                              ? 'text-accent bg-accent/15 hover:bg-accent/25'
                              : 'text-text-muted/50 hover:text-text-secondary hover:bg-bg-elevated',
                          )}
                          title={isExpanded ? '收起' : '展开主板个股20日涨幅'}
                        >
                          {isExpanded
                            ? <ChevronUp   className="w-3.5 h-3.5" />
                            : <ChevronDown className="w-3.5 h-3.5" />
                          }
                        </button>
                      )}
                    </td>
                  </tr>

                  {/* 展开面板：内嵌在行下方，colSpan 撑满所有列 */}
                  {isExpanded && (
                    <tr className="border-b border-accent/20">
                      <td colSpan={13} className="p-0 bg-accent/5">
                        <TopStocksPanel
                          bkCode={s.code}
                          onClickStock={(code) => navigate(`/stocks/${code}`)}
                        />
                      </td>
                    </tr>
                  )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
