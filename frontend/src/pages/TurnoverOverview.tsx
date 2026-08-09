import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchTurnoverOverview } from '@/api/turnover'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { SectorTag } from '@/components/common/SectorTags'
import { useSectorTags } from '@/hooks/useSectorTags'
import { cn } from '@/utils/cn'
import { Coins, Sparkles, TrendingUp, TrendingDown, Minus } from 'lucide-react'
import type { TurnoverSectorGroup, TurnoverStock } from '@/types'

// ─── 板块排序（赚钱效应 / 板块涨幅 / 成交额 / 只数）── 同强势股概览 SectorEffectCard 的排序约定
type TurnoverSortKey = 'effect' | 'sector_pct' | 'amount' | 'count'
const TURNOVER_SORTS: { key: TurnoverSortKey; label: string; title: string }[] = [
  { key: 'effect',     label: '效应', title: '按赚钱效应（成员均涨幅）排序' },
  { key: 'sector_pct', label: '涨幅', title: '按板块涨幅（板块指数今日涨幅）排序' },
  { key: 'amount',     label: '成交额', title: '按成交额排序' },
  { key: 'count',      label: '只数', title: '按板块只数排序' },
]

function sortTurnoverGroups(
  list: TurnoverSectorGroup[], key: TurnoverSortKey, sectorPctByName: Map<string, number>,
): TurnoverSectorGroup[] {
  return [...list].sort((a, b) => {
    switch (key) {
      case 'sector_pct':
        return (sectorPctByName.get(b.name) ?? b.avg_pct_change) - (sectorPctByName.get(a.name) ?? a.avg_pct_change)
      case 'amount':
        return b.total_amount - a.total_amount
      case 'count':
        return b.count - a.count
      default:
        return b.avg_pct_change - a.avg_pct_change
    }
  })
}

function TurnoverSortControl({ value, onChange }: { value: TurnoverSortKey; onChange: (k: TurnoverSortKey) => void }) {
  return (
    <div className="flex items-center gap-0.5">
      {TURNOVER_SORTS.map((o) => (
        <button
          key={o.key}
          title={o.title}
          onClick={() => onChange(o.key)}
          className={cn(
            'px-1.5 py-0.5 rounded text-xs transition-colors',
            value === o.key ? 'bg-accent/15 text-accent font-medium' : 'text-text-muted hover:text-text-secondary',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

const yi = (v: number) => `${(v / 1e8).toFixed(1)}亿`
const pctColor = (v: number) => (v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-secondary')
const pctSign = (v: number) => (v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`)

const BIAS_LABEL: Record<TurnoverSectorGroup['bias'], string> = {
  bullish: '看多',
  bearish: '看空',
  mixed: '分歧',
}
const BIAS_ICON: Record<TurnoverSectorGroup['bias'], typeof TrendingUp> = {
  bullish: TrendingUp,
  bearish: TrendingDown,
  mixed: Minus,
}
const BIAS_COLOR: Record<TurnoverSectorGroup['bias'], string> = {
  bullish: 'text-up bg-up-dim',
  bearish: 'text-down bg-down-dim',
  mixed: 'text-text-muted bg-bg-elevated',
}

/** Horizontal stacked bar: up (green) | flat (muted) | down (red) */
function UpDownBar({ up, flat, down }: { up: number; flat: number; down: number }) {
  const total = up + flat + down
  if (total === 0) return <div className="h-2 rounded-full bg-bg-elevated w-full" />
  return (
    <div className="flex h-2 rounded-full overflow-hidden w-full gap-px">
      {up > 0 && <div className="bg-up rounded-l-full" style={{ width: `${(up / total) * 100}%` }} />}
      {flat > 0 && <div className="bg-text-secondary/50" style={{ width: `${(flat / total) * 100}%` }} />}
      {down > 0 && <div className="bg-down rounded-r-full" style={{ width: `${(down / total) * 100}%` }} />}
    </div>
  )
}

function SectorCard({ group, sectorPctToday }: { group: TurnoverSectorGroup; sectorPctToday?: number }) {
  const BiasIcon = BIAS_ICON[group.bias]
  return (
    <Card>
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <SectorTag name={group.name} />
          <span className="text-xs text-text-muted">{group.count} 只</span>
          {group.new_count > 0 && (
            <Badge variant="accent" className="gap-0.5">
              <Sparkles className="w-2.5 h-2.5" /> 新进{group.new_count}
            </Badge>
          )}
        </div>
        <div className={cn('flex items-center gap-1 text-xs font-medium px-1.5 py-0.5 rounded', BIAS_COLOR[group.bias])}>
          <BiasIcon className="w-3 h-3" /> {BIAS_LABEL[group.bias]}
        </div>
      </div>
      <div className="flex items-baseline justify-between mb-2">
        <span className="text-lg font-bold text-text-primary font-mono">{yi(group.total_amount)}</span>
        <div className="text-right">
          <span className={cn('text-sm font-mono font-medium', pctColor(group.avg_pct_change))}>
            效应{pctSign(group.avg_pct_change)}
          </span>
          {sectorPctToday != null && (
            <div className={cn('text-[11px] font-mono', pctColor(sectorPctToday))}>
              板块{pctSign(sectorPctToday)}
            </div>
          )}
        </div>
      </div>
      <UpDownBar up={group.up_count} flat={group.flat_count} down={group.down_count} />
      <div className="flex justify-between text-[11px] text-text-muted mt-1">
        <span>{group.up_count}涨</span>
        <span>{group.flat_count}平</span>
        <span>{group.down_count}跌</span>
      </div>
    </Card>
  )
}

function StockRow({ s, onClick }: { s: TurnoverStock; onClick: () => void }) {
  return (
    <tr onClick={onClick} className="border-b border-bg-border/25 hover:bg-bg-elevated cursor-pointer transition-colors last:border-0">
      <td className="px-3 py-2 text-text-muted font-mono text-xs">{s.rank}</td>
      <td className="px-3 py-2 whitespace-nowrap">
        <div className="flex items-center gap-1.5">
          <div>
            <div className="font-mono text-accent text-xs">{s.code}</div>
            <div className="text-text-primary font-medium">{s.name}</div>
          </div>
          {s.is_new && (
            <Badge variant="accent" className="gap-0.5">
              <Sparkles className="w-2.5 h-2.5" /> NEW
            </Badge>
          )}
        </div>
      </td>
      <td className="px-3 py-2">{s.sector_name ? <SectorTag name={s.sector_name} /> : <span className="text-xs text-text-muted">未分类</span>}</td>
      <td className="px-3 py-2 text-right font-mono text-xs">{yi(s.amount)}</td>
      <td className="px-3 py-2 text-right font-mono text-xs">
        <span className={pctColor(s.pct_change)}>{pctSign(s.pct_change)}</span>
      </td>
      <td className="px-3 py-2 text-right font-mono text-xs text-text-secondary">
        {s.turnover_rate != null ? `${s.turnover_rate.toFixed(2)}%` : '—'}
      </td>
    </tr>
  )
}

export default function TurnoverOverview() {
  const navigate = useNavigate()
  const { data, isLoading } = useQuery({ queryKey: ['turnover-overview'], queryFn: () => fetchTurnoverOverview() })
  const { byName: sectorTagsByName } = useSectorTags()
  const [sortKey, setSortKey] = useState<TurnoverSortKey>('effect')

  const sectorPctByName = useMemo(() => {
    const m = new Map<string, number>()
    sectorTagsByName.forEach((tag, name) => m.set(name, tag.pct_today))
    return m
  }, [sectorTagsByName])

  const sortedGroups = useMemo(
    () => sortTurnoverGroups(data?.sector_groups ?? [], sortKey, sectorPctByName),
    [data, sortKey, sectorPctByName],
  )

  if (isLoading || !data) return <LoadingSpinner />

  if (!data.date) {
    return (
      <div className="card p-8 text-center text-text-muted text-sm">
        {data.errors[0] || '暂无成交额数据'}
      </div>
    )
  }

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Card>
          <p className="text-xs text-text-muted mb-1">数据日期</p>
          <p className="text-lg font-bold text-text-primary">{data.date}</p>
        </Card>
        <Card>
          <p className="text-xs text-text-muted mb-1">上榜只数</p>
          <p className="text-lg font-bold text-text-primary">{data.stocks.length}</p>
        </Card>
        <Card>
          <p className="text-xs text-text-muted mb-1 flex items-center gap-1"><Coins className="w-3 h-3" />总成交额</p>
          <p className="text-lg font-bold text-text-primary font-mono">{yi(data.total_amount)}</p>
        </Card>
        <Card>
          <p className="text-xs text-text-muted mb-1 flex items-center gap-1"><Sparkles className="w-3 h-3" />今日新进</p>
          <p className="text-lg font-bold text-accent">{data.new_count} 只</p>
        </Card>
      </div>

      {data.errors.length > 0 && (
        <div className="card p-3 text-sm text-warn border border-warn/30">
          {data.errors.join('；')}
        </div>
      )}

      <div>
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-text-primary">板块赚钱效应 ({sortedGroups.length})</h3>
          <TurnoverSortControl value={sortKey} onChange={setSortKey} />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {sortedGroups.map((g) => (
            <SectorCard key={g.name} group={g} sectorPctToday={sectorPctByName.get(g.name)} />
          ))}
        </div>
      </div>

      <Card title="成交额明细">
        <div className="overflow-x-auto max-h-[560px] overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-bg-card">
              <tr className="border-b border-bg-border/40">
                <th className="text-left px-3 py-2 text-xs text-text-secondary/70 font-medium">#</th>
                <th className="text-left px-3 py-2 text-xs text-text-secondary/70 font-medium">代码 / 名称</th>
                <th className="text-left px-3 py-2 text-xs text-text-secondary/70 font-medium">板块</th>
                <th className="text-right px-3 py-2 text-xs text-text-secondary/70 font-medium whitespace-nowrap">成交额</th>
                <th className="text-right px-3 py-2 text-xs text-text-secondary/70 font-medium whitespace-nowrap">涨跌幅</th>
                <th className="text-right px-3 py-2 text-xs text-text-secondary/70 font-medium whitespace-nowrap">换手率</th>
              </tr>
            </thead>
            <tbody>
              {data.stocks.map((s) => (
                <StockRow key={s.code} s={s} onClick={() => navigate(`/stocks/${s.code}`)} />
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
