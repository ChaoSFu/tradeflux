import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchTurnoverOverview } from '@/api/turnover'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { SectorTag, SectorRankTags } from '@/components/common/SectorTags'
import { useSectorTags, type SectorTagData } from '@/hooks/useSectorTags'
import { cn } from '@/utils/cn'
import { Coins, Sparkles, TrendingUp, TrendingDown, Minus, ChevronDown, ChevronUp, Star } from 'lucide-react'
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
// 展开面板强调色（同 SectorSection 的 GROUP_META 配色惯例：红=看多/涨，绿=看空/跌）
const BIAS_ACCENT: Record<TurnoverSectorGroup['bias'], string> = {
  bullish: '#FF4560',
  bearish: '#26C281',
  mixed: '#94A3B8',
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

/** 板块行：展示逻辑同强势股概览 SectorEffectCard/SectorRow（板块名/涨跌分布条/涨幅/只数/效应，点击展开成员），
 *  额外插入成交额（本页核心指标）与看多/看空/分歧信号标签。 */
function SectorRow({
  group, sectorPctToday, active, onClick,
}: {
  group: TurnoverSectorGroup
  sectorPctToday?: number
  active?: boolean
  onClick?: () => void
}) {
  const BiasIcon = BIAS_ICON[group.bias]
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-3 px-2 py-1.5 rounded transition-colors text-left',
        active ? 'bg-accent/10 ring-1 ring-accent/30' : 'hover:bg-bg-elevated',
      )}
    >
      <div className="w-28 shrink-0 flex items-center gap-1">
        <SectorTag name={group.name} />
        {group.new_count > 0 && (
          <Badge variant="accent" className="gap-0.5 shrink-0">
            <Sparkles className="w-2.5 h-2.5" />{group.new_count}
          </Badge>
        )}
      </div>
      <div className={cn('flex items-center gap-1 text-xs font-medium px-1.5 py-0.5 rounded shrink-0', BIAS_COLOR[group.bias])}>
        <BiasIcon className="w-3 h-3" /> {BIAS_LABEL[group.bias]}
      </div>
      <div className="flex-1 min-w-[60px]">
        <UpDownBar up={group.up_count} flat={group.flat_count} down={group.down_count} />
      </div>
      <span className="text-sm font-mono font-medium w-20 text-right shrink-0 whitespace-nowrap text-text-primary">
        {yi(group.total_amount)}
      </span>
      <span className={cn('text-sm font-mono font-medium w-16 text-right shrink-0', pctColor(sectorPctToday ?? group.avg_pct_change))}>
        {pctSign(sectorPctToday ?? group.avg_pct_change)}
      </span>
      <span className="text-xs text-text-muted w-20 text-center shrink-0 font-mono">
        <span className="text-up">{group.up_count}</span>
        <span className="text-text-muted/60 mx-0.5">/</span>
        <span className="text-text-muted">{group.flat_count}</span>
        <span className="text-text-muted/60 mx-0.5">/</span>
        <span className="text-down">{group.down_count}</span>
      </span>
      <span className={cn('text-sm font-mono font-medium w-16 text-right shrink-0', pctColor(group.avg_pct_change))}>
        {pctSign(group.avg_pct_change)}
      </span>
      {active
        ? <ChevronUp className="w-3.5 h-3.5 text-accent shrink-0" />
        : <ChevronDown className="w-3.5 h-3.5 text-text-muted/40 shrink-0" />
      }
    </button>
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

/** 展开的板块成员详情面板：展示逻辑同强势股概览 SectorSection（点击板块后展开的卡片）——
 *  强调色左侧竖条 + 板块名/只数/排行标签 + 龙头（本页取成交额最高股）+ 板块级多周期涨幅/强股/连板速览
 *  （复用 useSectorTags 的板块级数据，跟 SectorSection 头部同一数据源）+ 赚钱效应/涨跌只数 + 成员表。 */
function SectorDetailPanel({
  group, stocks, tagData, onClose, onClickStock,
}: {
  group: TurnoverSectorGroup
  stocks: TurnoverStock[]
  tagData?: SectorTagData
  onClose: () => void
  onClickStock: (code: string) => void
}) {
  const accentColor = BIAS_ACCENT[group.bias]
  const leader = stocks[0]  // stocks 保留原始成交额排名顺序，首位即该板块成交额最高股

  return (
    <div className="card overflow-hidden p-0" style={{ borderColor: `${accentColor}20` }}>
      <button
        className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-bg-elevated transition-colors text-left"
        onClick={onClose}
      >
        <div className="w-1 h-5 rounded-full shrink-0" style={{ backgroundColor: accentColor }} />
        <span className="font-semibold text-sm text-text-primary">{group.name}</span>
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
          {tagData && (
            <span className="hidden lg:flex items-center gap-2 text-xs font-mono">
              {([['今', tagData.pct_today], ['10日', tagData.pct_10d], ['20日', tagData.pct_20d], ['60日', tagData.pct_60d]] as const).map(([lab, v]) => (
                <span key={lab} className="text-text-muted/70">
                  {lab}
                  <span className={cn('ml-0.5', v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-muted')}>
                    {v > 0 ? '+' : ''}{v.toFixed(1)}%
                  </span>
                </span>
              ))}
              <span className="text-text-secondary">强股 <span className="text-text-primary">{tagData.strong_stock_count}</span></span>
              <span className="text-text-secondary">连板 <span className="text-text-primary">{tagData.board_height}</span></span>
            </span>
          )}
          <span className="flex items-center gap-1">
            <span className="text-text-muted/70 text-xs">赚钱效应</span>
            <span className={cn(
              'text-xs font-mono font-semibold px-1.5 py-px rounded',
              pctColor(group.avg_pct_change), group.avg_pct_change > 0 ? 'bg-up/10' : group.avg_pct_change < 0 ? 'bg-down/10' : '',
            )}>
              {pctSign(group.avg_pct_change)}
            </span>
          </span>
          <span className="flex items-center gap-1 text-xs font-mono">
            <span className="text-up">{group.up_count}涨</span>
            <span className="text-text-muted/70">/</span>
            <span className="text-down">{group.down_count}跌</span>
          </span>
          <ChevronUp className="w-3.5 h-3.5 text-text-muted shrink-0" />
        </div>
      </button>

      <div className="border-t border-bg-border/30">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-bg-border/20 bg-bg-elevated/40">
              <th className="text-left px-3 py-1.5 text-xs text-text-secondary/70 font-medium">#</th>
              <th className="text-left px-3 py-1.5 text-xs text-text-secondary/70 font-medium">代码 / 名称</th>
              <th className="text-right px-3 py-1.5 text-xs text-text-secondary/70 font-medium whitespace-nowrap">成交额</th>
              <th className="text-right px-3 py-1.5 text-xs text-text-secondary/70 font-medium whitespace-nowrap">涨跌幅</th>
              <th className="text-right px-3 py-1.5 text-xs text-text-secondary/70 font-medium whitespace-nowrap">换手率</th>
            </tr>
          </thead>
          <tbody>
            {stocks.map((s) => (
              <tr
                key={s.code}
                onClick={() => onClickStock(s.code)}
                className="border-b border-bg-border/15 last:border-0 cursor-pointer hover:bg-bg-elevated transition-colors"
              >
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
                <td className="px-3 py-2 text-right font-mono text-xs">{yi(s.amount)}</td>
                <td className="px-3 py-2 text-right font-mono text-xs">
                  <span className={pctColor(s.pct_change)}>{pctSign(s.pct_change)}</span>
                </td>
                <td className="px-3 py-2 text-right font-mono text-xs text-text-secondary">
                  {s.turnover_rate != null ? `${s.turnover_rate.toFixed(2)}%` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export default function TurnoverOverview() {
  const navigate = useNavigate()
  const { data, isLoading } = useQuery({ queryKey: ['turnover-overview'], queryFn: () => fetchTurnoverOverview() })
  const { byName: sectorTagsByName } = useSectorTags()
  const [sortKey, setSortKey] = useState<TurnoverSortKey>('effect')
  const [expandedSector, setExpandedSector] = useState<string | null>(null)

  const sectorPctByName = useMemo(() => {
    const m = new Map<string, number>()
    sectorTagsByName.forEach((tag, name) => m.set(name, tag.pct_today))
    return m
  }, [sectorTagsByName])

  const sortedGroups = useMemo(
    () => sortTurnoverGroups(data?.sector_groups ?? [], sortKey, sectorPctByName),
    [data, sortKey, sectorPctByName],
  )

  // 板块名 → 成员股（用于点击板块行展开成员列表，同强势股概览点击展开逻辑）
  const stocksBySectorName = useMemo(() => {
    const m = new Map<string, TurnoverStock[]>()
    for (const s of data?.stocks ?? []) {
      if (!s.sector_name) continue
      if (!m.has(s.sector_name)) m.set(s.sector_name, [])
      m.get(s.sector_name)!.push(s)
    }
    return m
  }, [data])

  const toggleSector = (name: string) =>
    setExpandedSector((prev) => (prev === name ? null : name))

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

      <Card
        title={`成交额板块效应 (${sortedGroups.length})`}
        action={sortedGroups.length > 0 ? <TurnoverSortControl value={sortKey} onChange={setSortKey} /> : undefined}
      >
        {sortedGroups.length > 0 ? (
          <div className="space-y-1 max-h-72 overflow-y-auto pr-1">
            {sortedGroups.map((g) => (
              <SectorRow
                key={g.name}
                group={g}
                sectorPctToday={sectorPctByName.get(g.name)}
                active={expandedSector === g.name}
                onClick={() => toggleSector(g.name)}
              />
            ))}
          </div>
        ) : (
          <div className="text-center text-text-muted text-sm py-6">暂无板块数据</div>
        )}
      </Card>

      {/* ── 展开的板块成员详情（点击板块行展开，展示逻辑同强势股概览 SectorSection）── */}
      {expandedSector && (() => {
        const groupData = sortedGroups.find((g) => g.name === expandedSector)
        const stocks = stocksBySectorName.get(expandedSector)
        if (!groupData || !stocks) return null
        return (
          <SectorDetailPanel
            group={groupData}
            stocks={stocks}
            tagData={sectorTagsByName.get(expandedSector)}
            onClose={() => setExpandedSector(null)}
            onClickStock={(code) => navigate(`/stocks/${code}`)}
          />
        )
      })()}

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
