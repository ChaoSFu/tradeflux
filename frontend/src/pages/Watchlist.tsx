import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { ShieldAlert, Clock, Activity, Gauge } from 'lucide-react'
import { fetchRegulatoryWatchlist, type RegulatoryItem, type ApproachingItem } from '@/api/watchlist'
import { Card } from '@/components/ui/card'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { SectorTag, OverflowBadge, LeaderTag, YesterdayLimitTag } from '@/components/common/SectorTags'
import { useLeaderUniverseMaxes, getLeaderTags } from '@/hooks/useLeaderUniverseMaxes'
import { cn } from '@/utils/cn'

const md = (d: string | null) => (d ? d.slice(5).replace('-', '/') : '—')
const pctColor = (v: number | null | undefined) =>
  v == null ? 'text-text-muted' : v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-secondary'
const pctStr = (v: number | null | undefined) =>
  v == null ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`

function RemainBadge({ days }: { days: number | null }) {
  if (days == null) return <span className="text-text-muted text-xs">—</span>
  if (days < 0) {
    return <span className="inline-block px-1.5 py-0.5 rounded text-xs font-medium bg-down/12 text-down">已解除</span>
  }
  const urgent = days <= 3
  return (
    <span
      className={cn(
        'inline-block px-1.5 py-0.5 rounded text-xs font-mono font-medium',
        urgent ? 'bg-warn/15 text-warn' : 'bg-bg-elevated text-text-secondary',
      )}
    >
      {days} 天
    </span>
  )
}

function RegRow({ it, maxes, onClick }: {
  it: RegulatoryItem
  maxes: ReturnType<typeof useLeaderUniverseMaxes>
  onClick: () => void
}) {
  const s = it.stock
  const leaderTags = s ? getLeaderTags(s, maxes) : []
  const sectors = s?.sectors ?? []
  const shown = sectors.slice(0, 3)
  const hidden = sectors.slice(3)
  return (
    <tr
      className="border-b border-bg-border/20 last:border-0 hover:bg-bg-elevated transition-colors cursor-pointer"
      onClick={onClick}
    >
      {/* 股票 */}
      <td className="px-3 py-2.5">
        <div className="font-mono text-accent text-xs">{it.security_code}</div>
        <div className="text-text-primary font-medium whitespace-nowrap">{it.security_name ?? '—'}</div>
        {(leaderTags.length > 0 || s?.yesterday_is_limit_up || s?.yesterday_is_limit_down) && (
          <div className="flex flex-wrap gap-0.5 mt-0.5">
            {s?.yesterday_is_limit_up && <YesterdayLimitTag dir="up" />}
            {s?.yesterday_is_limit_down && <YesterdayLimitTag dir="down" />}
            {leaderTags.map((t) => <LeaderTag key={t} label={t} />)}
          </div>
        )}
      </td>
      {/* 板块 */}
      <td className="px-3 py-2.5 max-w-[220px]">
        {shown.length ? (
          <div className="flex flex-wrap gap-1">
            {shown.map((n) => <SectorTag key={n} name={n} />)}
            {hidden.length > 0 && <OverflowBadge count={hidden.length} hidden={hidden} />}
          </div>
        ) : <span className="text-text-muted text-xs">—</span>}
      </td>
      {/* 触发原因 */}
      <td className="px-3 py-2.5">
        <span className={cn('text-xs', it.direction === 'up' ? 'text-up' : it.direction === 'down' ? 'text-down' : 'text-text-secondary')}>
          {it.reason_type ?? '—'}
        </span>
      </td>
      {/* 交易所 */}
      <td className="px-3 py-2.5 text-xs text-text-muted whitespace-nowrap">{it.exchange ?? '—'}</td>
      {/* 监管期 */}
      <td className="px-3 py-2.5 text-xs font-mono text-text-secondary whitespace-nowrap text-center">
        {md(it.predict_start)} ~ {md(it.predict_end)}
      </td>
      {/* 剩余 */}
      <td className="px-3 py-2.5 text-center"><RemainBadge days={it.days_remaining} /></td>
      {/* 今日涨幅 */}
      <td className={cn('px-3 py-2.5 text-right font-mono text-xs', pctColor(s?.today_pct_change))}>
        {pctStr(s?.today_pct_change)}
      </td>
      {/* 连板 */}
      <td className="px-3 py-2.5 text-right font-mono text-xs">
        {s && (s.today_board_count ?? 0) > 0
          ? <span className="text-dragon font-bold">{s.today_board_count}板</span>
          : <span className="text-text-muted/70">—</span>}
      </td>
    </tr>
  )
}

function RegTable({ items, maxes, onClickStock, empty }: {
  items: RegulatoryItem[]
  maxes: ReturnType<typeof useLeaderUniverseMaxes>
  onClickStock: (code: string) => void
  empty: string
}) {
  if (items.length === 0) {
    return <div className="text-center text-text-muted text-sm py-6">{empty}</div>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-bg-border/40 text-xs text-text-secondary/70">
            <th className="px-3 py-2 text-left font-medium">代码 / 名称</th>
            <th className="px-3 py-2 text-left font-medium">板块</th>
            <th className="px-3 py-2 text-left font-medium">触发原因</th>
            <th className="px-3 py-2 text-left font-medium">交易所</th>
            <th className="px-3 py-2 text-center font-medium">监管期</th>
            <th className="px-3 py-2 text-center font-medium">剩余</th>
            <th className="px-3 py-2 text-right font-medium">今日涨幅</th>
            <th className="px-3 py-2 text-right font-medium">连续连板</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it) => (
            <RegRow key={it.info_code} it={it} maxes={maxes} onClick={() => onClickStock(it.security_code)} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ApproachBar({ approach, direction }: { approach: number; direction: 'up' | 'down' }) {
  const pct = Math.min(100, Math.round(approach * 100))
  const reached = approach >= 1
  const color = direction === 'up' ? 'var(--color-up, #FF4560)' : 'var(--color-down, #26C281)'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-2 rounded-full bg-bg-elevated overflow-hidden min-w-[60px]">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <span className={cn('font-mono text-xs w-10 text-right', reached ? 'font-bold' : '')} style={{ color }}>
        {pct}%
      </span>
    </div>
  )
}

function ApproachRow({ it, maxes, onClick }: {
  it: ApproachingItem
  maxes: ReturnType<typeof useLeaderUniverseMaxes>
  onClick: () => void
}) {
  const s = it.stock
  const leaderTags = s ? getLeaderTags(s, maxes) : []
  const sectors = s?.sectors ?? []
  const shown = sectors.slice(0, 3)
  const hidden = sectors.slice(3)
  const dirColor = it.direction === 'up' ? 'text-up' : 'text-down'
  return (
    <tr
      className="border-b border-bg-border/20 last:border-0 hover:bg-bg-elevated transition-colors cursor-pointer"
      onClick={onClick}
    >
      <td className="px-3 py-2.5">
        <div className="font-mono text-accent text-xs">{it.security_code}</div>
        <div className="text-text-primary font-medium whitespace-nowrap">{it.security_name ?? '—'}</div>
        {(leaderTags.length > 0 || s?.yesterday_is_limit_up || s?.yesterday_is_limit_down) && (
          <div className="flex flex-wrap gap-0.5 mt-0.5">
            {s?.yesterday_is_limit_up && <YesterdayLimitTag dir="up" />}
            {s?.yesterday_is_limit_down && <YesterdayLimitTag dir="down" />}
            {leaderTags.map((t) => <LeaderTag key={t} label={t} />)}
          </div>
        )}
      </td>
      <td className="px-3 py-2.5 max-w-[200px]">
        {shown.length ? (
          <div className="flex flex-wrap gap-1">
            {shown.map((n) => <SectorTag key={n} name={n} />)}
            {hidden.length > 0 && <OverflowBadge count={hidden.length} hidden={hidden} />}
          </div>
        ) : <span className="text-text-muted text-xs">—</span>}
      </td>
      <td className="px-3 py-2.5">
        <span className={cn('text-xs', dirColor)}>{it.rule_label}</span>
      </td>
      <td className={cn('px-3 py-2.5 text-right font-mono text-xs', dirColor)}>
        {it.cum_deviation > 0 ? '+' : ''}{it.cum_deviation}% <span className="text-text-muted/60">/ {it.threshold}%</span>
      </td>
      <td className="px-3 py-2.5 w-40"><ApproachBar approach={it.approach} direction={it.direction} /></td>
      <td className="px-3 py-2.5 text-right font-mono text-xs text-text-secondary">
        {it.target_rate == null ? '—' : `${it.target_rate > 0 ? '+' : ''}${it.target_rate}%`}
      </td>
      <td className={cn('px-3 py-2.5 text-right font-mono text-xs', pctColor(s?.today_pct_change))}>
        {pctStr(s?.today_pct_change)}
      </td>
    </tr>
  )
}

function ApproachTable({ items, maxes, onClickStock }: {
  items: ApproachingItem[]
  maxes: ReturnType<typeof useLeaderUniverseMaxes>
  onClickStock: (code: string) => void
}) {
  if (items.length === 0) {
    return <div className="text-center text-text-muted text-sm py-6">当前无个股逼近严重异常波动阈值</div>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-bg-border/40 text-xs text-text-secondary/70">
            <th className="px-3 py-2 text-left font-medium">代码 / 名称</th>
            <th className="px-3 py-2 text-left font-medium">板块</th>
            <th className="px-3 py-2 text-left font-medium">逼近规则</th>
            <th className="px-3 py-2 text-right font-medium">累计偏离 / 阈值</th>
            <th className="px-3 py-2 text-left font-medium">接近度</th>
            <th className="px-3 py-2 text-right font-medium">还需触发</th>
            <th className="px-3 py-2 text-right font-medium">今日涨幅</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it) => (
            <ApproachRow key={it.security_code} it={it} maxes={maxes} onClick={() => onClickStock(it.security_code)} />
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function Watchlist() {
  const navigate = useNavigate()
  const maxes = useLeaderUniverseMaxes()
  const { data, isLoading } = useQuery({
    queryKey: ['regulatory-watchlist'],
    queryFn: fetchRegulatoryWatchlist,
  })

  if (isLoading) return <LoadingSpinner />

  const monitoring = data?.monitoring ?? []
  const endingSoon = data?.ending_soon ?? []
  const recentlyReleased = data?.recently_released ?? []
  const approaching = data?.approaching ?? []
  const activeCount = monitoring.length + endingSoon.length
  const onClickStock = (code: string) => navigate(`/stocks/${code}`)

  return (
    <div className="space-y-5 animate-fade-in">
      {/* 统计条 */}
      <div className="card p-4 border-l-4" style={{ borderLeftColor: '#FBBF24' }}>
        <div className="flex flex-wrap items-center gap-x-8 gap-y-2">
          <div>
            <p className="label">监管中</p>
            <span className="font-mono text-2xl font-bold text-text-primary">{activeCount}</span>
          </div>
          <div>
            <p className="label">3 日内解除</p>
            <span className="font-mono text-2xl font-bold text-warn">{endingSoon.length}</span>
          </div>
          <div>
            <p className="label">近期解除</p>
            <span className="font-mono text-2xl font-bold text-down">{recentlyReleased.length}</span>
          </div>
          <div>
            <p className="label">即将进入</p>
            <span className="font-mono text-2xl font-bold text-accent">{approaching.length}</span>
          </div>
          <div className="text-xs text-text-muted max-w-[360px]">
            数据来源：交易所严重异常波动（重点监控）名单 · 截至 {data?.as_of ?? '—'}。
            「监管中」指今日仍处于监控期内的个股。
          </div>
        </div>
      </div>

      {/* 即将进入监管（偏离值预警，M3 核心） */}
      <Card title={`即将进入监管 · 偏离值预警 (${approaching.length})`} action={<Gauge className="w-3.5 h-3.5 text-accent" />}>
        <ApproachTable items={approaching} maxes={maxes} onClickStock={onClickStock} />
        <div className="text-xs text-text-muted/70 mt-2 px-1">
数据来自东财实时「严重异动预测」（官方累计偏离值，连续10/30日±100%/±200%/-50%/-70% 规则）。接近度 = 累计偏离值 / 阈值；「还需触发」= 今日还需涨跌幅即触发。仅列今日仍可触发的个股（已排除今日下跌+窗口滚动导致无法触发的消退股）。
        </div>
      </Card>

      {/* 即将解除 */}
      <Card title={`即将解除监管 (${endingSoon.length})`} action={<Clock className="w-3.5 h-3.5 text-warn" />}>
        <RegTable items={endingSoon} maxes={maxes} onClickStock={onClickStock} empty="近 3 个交易日内无监管到期个股" />
      </Card>

      {/* 监管中 */}
      <Card title={`监管中 (${monitoring.length})`} action={<ShieldAlert className="w-3.5 h-3.5 text-accent" />}>
        {activeCount > 0 ? (
          <RegTable items={monitoring} maxes={maxes} onClickStock={onClickStock} empty="监管中个股均在「即将解除」区" />
        ) : (
          <div className="flex flex-col items-center justify-center text-center py-8">
            <div className="w-10 h-10 rounded-full bg-bg-elevated flex items-center justify-center mb-2.5">
              <Activity className="w-5 h-5 text-text-muted" />
            </div>
            <div className="text-sm font-medium text-text-secondary">当前无个股处于活跃监控期</div>
            <div className="text-xs text-text-muted mt-1 max-w-[340px]">
              今日没有个股处于交易所重点监控期内（近期无新触发严重异常波动）。
              下方「近期解除」可查看刚结束监控期的个股。
            </div>
          </div>
        )}
      </Card>

      {/* 近期解除 */}
      <Card title={`近期解除监管 (${recentlyReleased.length})`} action={<Clock className="w-3.5 h-3.5 text-down" />}>
        <RegTable items={recentlyReleased} maxes={maxes} onClickStock={onClickStock} empty="近 15 日内无监管期结束个股" />
      </Card>
    </div>
  )
}
