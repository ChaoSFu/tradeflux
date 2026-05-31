/**
 * 涨跌停分析 — 仪表盘
 * 功能：近期涨停/跌停趋势曲线 + 板块集中度（饼图 + 排名列表 + 跨板块分析）
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchLimitMoves, fetchLimitMovesTrend } from '@/api/stocks'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { getSectorColor } from '@/components/common/SectorTags'
import { ArrowUp, ArrowDown, TrendingUp } from 'lucide-react'
import { cn } from '@/utils/cn'
import { format } from 'date-fns'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, PieChart, Pie, Cell,
} from 'recharts'
import type { Stock } from '@/types'

// ─── Palette ──────────────────────────────────────────────────────────────────
const C_UP   = '#FF4560'
const C_DOWN = '#26C281'
const C_OTHER = '#505570'

// ─── Sector stat with stock tracking ─────────────────────────────────────────

interface SectorStat {
  name: string
  limit_up: number
  limit_down: number
  total: number
  up_stock_ids: number[]    // stock IDs in this sector among today's up list
  down_stock_ids: number[]  // stock IDs in this sector among today's down list
}

function buildSectorStats(upStocks: Stock[], downStocks: Stock[]): SectorStat[] {
  const map = new Map<string, {
    limit_up: number; limit_down: number
    up_ids: number[]; down_ids: number[]
  }>()

  for (const s of upStocks) {
    for (const sec of s.sectors ?? []) {
      const e = map.get(sec) ?? { limit_up: 0, limit_down: 0, up_ids: [], down_ids: [] }
      e.limit_up++; e.up_ids.push(s.id)
      map.set(sec, e)
    }
  }
  for (const s of downStocks) {
    for (const sec of s.sectors ?? []) {
      const e = map.get(sec) ?? { limit_up: 0, limit_down: 0, up_ids: [], down_ids: [] }
      e.limit_down++; e.down_ids.push(s.id)
      map.set(sec, e)
    }
  }

  const result: SectorStat[] = []
  for (const [name, v] of map) {
    result.push({
      name, limit_up: v.limit_up, limit_down: v.limit_down,
      total: v.limit_up + v.limit_down,
      up_stock_ids: v.up_ids, down_stock_ids: v.down_ids,
    })
  }
  return result
}

// ─── Chart tooltip ────────────────────────────────────────────────────────────

function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="card p-2.5 text-xs space-y-1 shadow-xl border border-bg-border/60">
      <div className="text-text-muted mb-1">{label}</div>
      {payload.map((p: any) => (
        <div key={p.name} className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: p.color }} />
          <span className="text-text-secondary">{p.name}</span>
          <span className="font-mono ml-auto font-semibold" style={{ color: p.color }}>{p.value}</span>
        </div>
      ))}
    </div>
  )
}

function PieCustomTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0]
  return (
    <div className="card px-2.5 py-1.5 text-xs shadow-xl border border-bg-border/60">
      <span className="font-medium" style={{ color: p.payload.fill }}>{p.name}</span>
      <span className="text-text-muted ml-2">{p.value} 只</span>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function LimitMovesDashboard() {
  const { data: upData,   isLoading: upLoading }   = useQuery({
    queryKey: ['limit-moves', 'limit_up'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up' }),
  } as any)

  const { data: downData, isLoading: downLoading } = useQuery({
    queryKey: ['limit-moves', 'limit_down'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down' }),
  } as any)

  const { data: trendData, isLoading: trendLoading } = useQuery({
    queryKey: ['limit-moves-trend', 30],
    queryFn: () => fetchLimitMovesTrend(30),
  } as any)

  const limitUps:   Stock[] = (upData   as any)?.items ?? []
  const limitDowns: Stock[] = (downData as any)?.items ?? []

  // ── Trend chart data ──────────────────────────────────────────────────────
  const chartData = useMemo(() => {
    const raw: any[] = (trendData as any) ?? []
    return raw.map((p) => ({
      date: format(new Date(p.date), 'MM/dd'),
      '涨停': p.limit_up_count,
      '跌停': p.limit_down_count,
    }))
  }, [trendData])

  const avg = useMemo(() => {
    const raw: any[] = (trendData as any) ?? []
    const last10 = raw.slice(-10)
    if (!last10.length) return { up: 0, down: 0 }
    return {
      up:   Math.round(last10.reduce((s, p) => s + p.limit_up_count,   0) / last10.length),
      down: Math.round(last10.reduce((s, p) => s + p.limit_down_count, 0) / last10.length),
    }
  }, [trendData])

  // ── Sector hotspot ────────────────────────────────────────────────────────
  const sectorStats = useMemo(() => buildSectorStats(limitUps, limitDowns), [limitUps, limitDowns])

  const topUpSectors = useMemo(
    () =>
      [...sectorStats]
        .filter(s => s.limit_up > 0)
        .sort((a, b) =>
          // 赚钱效应排序：涨停数从大到小；相同则跌停数从小到大
          b.limit_up !== a.limit_up
            ? b.limit_up - a.limit_up
            : a.limit_down - b.limit_down,
        )
        .slice(0, 10),
    [sectorStats],
  )
  const topDownSectors = useMemo(
    () =>
      [...sectorStats]
        .filter(s => s.limit_down > 0)
        .sort((a, b) =>
          // 亏钱效应排序：跌停数从大到小；相同则涨停数从小到大
          b.limit_down !== a.limit_down
            ? b.limit_down - a.limit_down
            : a.limit_up - b.limit_up,
        )
        .slice(0, 10),
    [sectorStats],
  )

  const dataLoading = upLoading || downLoading

  return (
    <div className="space-y-4 animate-fade-in">

      {/* ── Stat cards ─────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 flex-wrap">
        <StatCard
          icon={<ArrowUp className="w-4 h-4" />}
          label="今日涨停"
          value={dataLoading ? null : limitUps.length}
          sub={dataLoading ? null : `近10日均 ${avg.up}`}
          color="up"
        />
        <StatCard
          icon={<ArrowDown className="w-4 h-4" />}
          label="今日跌停"
          value={dataLoading ? null : limitDowns.length}
          sub={dataLoading ? null : `近10日均 ${avg.down}`}
          color="down"
        />
        {!dataLoading && limitDowns.length > 0 && (
          <div className="card px-4 py-3 flex items-center gap-3">
            <TrendingUp className="w-4 h-4 text-accent/60" />
            <div>
              <div className="text-xs text-text-muted">涨跌比</div>
              <div className="text-xl font-bold font-mono text-accent leading-none mt-0.5">
                {limitUps.length}:{limitDowns.length}
                <span className="text-sm font-normal text-text-muted ml-1.5">
                  ({(limitUps.length / limitDowns.length).toFixed(1)}x)
                </span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Trend chart ────────────────────────────────────────────────── */}
      <div className="card p-4 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-sm font-semibold text-text-primary">近期涨停 / 跌停走势</span>
          <span className="text-xs text-text-muted">近30个交易日</span>
        </div>
        <div className="h-52">
          {trendLoading ? (
            <div className="h-full flex items-center justify-center"><LoadingRows /></div>
          ) : chartData.length === 0 ? (
            <div className="h-full flex items-center justify-center text-text-muted text-sm">暂无历史数据</div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: '#737A96', fontSize: 11 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
                <YAxis tick={{ fill: '#737A96', fontSize: 11 }} axisLine={false} tickLine={false} width={32} />
                <Tooltip content={<CustomTooltip />} />
                <Legend wrapperStyle={{ fontSize: 12, color: '#A2A9C4', paddingTop: 4 }} iconSize={8} />
                <Line type="monotone" dataKey="涨停" stroke={C_UP}   strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                <Line type="monotone" dataKey="跌停" stroke={C_DOWN} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* ── Sector hotspot ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4">
        <SectorHotspot
          title="涨停集中板块"
          sectors={topUpSectors}
          allSectorStats={sectorStats}
          field="limit_up"
          stocks={limitUps}
          color={C_UP}
          isLoading={dataLoading}
        />
        <SectorHotspot
          title="跌停集中板块"
          sectors={topDownSectors}
          allSectorStats={sectorStats}
          field="limit_down"
          stocks={limitDowns}
          color={C_DOWN}
          isLoading={dataLoading}
        />
      </div>

    </div>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function StatCard({ icon, label, value, sub, color }: {
  icon: React.ReactNode; label: string; value: number | null; sub: string | null; color: 'up' | 'down'
}) {
  return (
    <div className={cn(
      'card flex items-center gap-3 px-4 py-3 border',
      color === 'up' ? 'bg-up/6 border-up/20' : 'bg-down/6 border-down/20',
    )}>
      <span className={cn('p-1.5 rounded-lg', color === 'up' ? 'bg-up/15 text-up' : 'bg-down/15 text-down')}>
        {icon}
      </span>
      <div>
        <div className="text-xs text-text-muted">{label}</div>
        <div className={cn('text-2xl font-bold font-mono leading-none mt-0.5', color === 'up' ? 'text-up' : 'text-down')}>
          {value === null ? '…' : value}
        </div>
        {sub && <div className="text-xs text-text-muted/70 mt-0.5">{sub}</div>}
      </div>
    </div>
  )
}

// ─── Sector hotspot (donut + ranked list + cross-sector analysis) ─────────────

function SectorHotspot({ title, sectors, allSectorStats, field, stocks, color, isLoading }: {
  title: string
  sectors: SectorStat[]
  allSectorStats: SectorStat[]
  field: 'limit_up' | 'limit_down'
  stocks: Stock[]             // full list of today's up/down stocks
  color: string
  isLoading: boolean
}) {
  // ── Cross-sector (overlap) stocks ─────────────────────────────────────────
  // A stock is "cross-sector" if it appears in 2+ of the top-N shown sectors
  const topSectorNames = useMemo(() => new Set(sectors.map(s => s.name)), [sectors])

  const overlapStocks = useMemo(() => (
    stocks.filter(s => {
      const matched = (s.sectors ?? []).filter(sec => topSectorNames.has(sec))
      return matched.length >= 2
    })
  ), [stocks, topSectorNames])

  // ── Pie data: top-8 sectors + "其他" remainder ────────────────────────────
  const pieData = useMemo(() => {
    const top8 = sectors.slice(0, 8)
    const rest  = sectors.slice(8)
    const data = top8.map(s => ({
      name:  s.name,
      value: s[field],
      fill:  getSectorColor(s.name),
    }))
    if (rest.length > 0) {
      data.push({
        name:  '其他',
        value: rest.reduce((sum, s) => sum + s[field], 0),
        fill:  C_OTHER,
      })
    }
    return data
  }, [sectors, field])

  const totalCount = stocks.length

  return (
    <div className="card overflow-hidden p-0">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div
        className="px-4 py-2.5 border-b border-bg-border/40 flex items-center gap-2 flex-wrap"
        style={{ backgroundColor: `${color}0d` }}
      >
        <span className="font-semibold text-sm" style={{ color }}>{title}</span>
        {!isLoading && (
          <>
            <span className="text-xs font-mono px-1.5 py-0.5 rounded" style={{ color, backgroundColor: `${color}18` }}>
              {sectors.length} 个板块
            </span>
            {overlapStocks.length > 0 && (
              <span
                className="text-xs font-mono px-1.5 py-0.5 rounded"
                style={{ color: '#8A90A8', backgroundColor: 'rgba(138,144,168,0.12)', border: '1px solid rgba(138,144,168,0.25)' }}
                title={`${overlapStocks.map(s => s.name).join('、')} 同属多个集中板块`}
              >
                {overlapStocks.length}只跨板块
              </span>
            )}
          </>
        )}
      </div>

      {isLoading ? (
        <div className="p-4"><LoadingRows /></div>
      ) : sectors.length === 0 ? (
        <div className="py-10 text-center text-text-muted text-sm">暂无数据</div>
      ) : (
        <>
          {/* ── Body: donut chart (left) + ranked list (right) ─────────── */}
          <div className="flex gap-0 min-h-0">

            {/* Donut chart */}
            <div className="flex-none flex flex-col items-center justify-center py-3 px-2 border-r border-bg-border/20"
              style={{ width: 130 }}>
              <div className="relative" style={{ width: 110, height: 110 }}>
                <PieChart width={110} height={110}>
                  <Pie
                    data={pieData}
                    cx="50%" cy="50%"
                    innerRadius={32} outerRadius={50}
                    paddingAngle={1.5}
                    dataKey="value"
                    strokeWidth={0}
                  >
                    {pieData.map((entry, i) => (
                      <Cell key={i} fill={entry.fill} fillOpacity={0.9} />
                    ))}
                  </Pie>
                  <Tooltip content={<PieCustomTooltip />} />
                </PieChart>
                {/* Center label */}
                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                  <span className="text-lg font-bold font-mono leading-none" style={{ color }}>
                    {totalCount}
                  </span>
                  <span className="text-[11px] text-text-muted/70 leading-tight">只</span>
                </div>
              </div>
              {/* Legend dots — top 5 */}
              <div className="mt-1 space-y-0.5 w-full px-1">
                {pieData.slice(0, 5).map((entry) => (
                  <div key={entry.name} className="flex items-center gap-1 min-w-0">
                    <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: entry.fill }} />
                    <span className="text-[11px] text-text-muted truncate leading-tight">{entry.name}</span>
                  </div>
                ))}
                {pieData.length > 5 && (
                  <div className="flex items-center gap-1">
                    <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: C_OTHER }} />
                    <span className="text-[11px] text-text-muted leading-tight">其他 {pieData.length - 5} 个</span>
                  </div>
                )}
              </div>
            </div>

            {/* Ranked list */}
            <div className="flex-1 min-w-0 divide-y divide-bg-border/20">
              {sectors.map((s, idx) => {
                const val    = s[field]
                const maxVal = sectors[0]?.[field] ?? 1
                const pct    = (val / maxVal) * 100
                return (
                  <div key={s.name} className="px-3 py-2 flex items-center gap-2.5 hover:bg-bg-elevated transition-colors">
                    <span className={cn(
                      'w-4 text-center text-xs font-mono shrink-0',
                      idx === 0 ? 'text-dragon font-bold' : 'text-text-muted/80',
                    )}>
                      {idx + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-0.5">
                        <span className="text-sm text-text-primary truncate pr-1">{s.name}</span>
                        <span className="font-mono font-bold text-xs shrink-0" style={{ color }}>
                          {val}只
                        </span>
                      </div>
                      <div className="h-1 rounded-full bg-bg-border/40 overflow-hidden">
                        <div
                          className="h-full rounded-full"
                          style={{ width: `${pct}%`, backgroundColor: `${color}90` }}
                        />
                      </div>
                    </div>
                    {/* Cross-side badge */}
                    {field === 'limit_up'   && s.limit_down > 0 && (
                      <span className="text-xs font-mono shrink-0 text-down/70">跌{s.limit_down}</span>
                    )}
                    {field === 'limit_down' && s.limit_up > 0 && (
                      <span className="text-xs font-mono shrink-0 text-up/70">涨{s.limit_up}</span>
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* ── Cross-sector stocks section ─────────────────────────────── */}
          {overlapStocks.length > 0 && (
            <div className="border-t border-bg-border/30 px-4 py-2.5">
              <div className="text-[12px] text-text-muted/85 uppercase tracking-wider mb-1.5">
                跨板块股票 · {overlapStocks.length} 只
              </div>
              <div className="flex flex-wrap gap-1.5">
                {overlapStocks.map(s => {
                  const secs = (s.sectors ?? []).filter(sec => topSectorNames.has(sec))
                  return (
                    <div
                      key={s.id}
                      className="flex items-center gap-1 text-xs bg-bg-elevated rounded px-2 py-0.5 border border-bg-border/40"
                      title={`板块：${secs.join('、')}`}
                    >
                      <span className="text-text-primary font-medium">{s.name}</span>
                      <span className="text-text-muted/85 font-mono text-[12px]">{s.code}</span>
                      <span
                        className="text-[11px] px-1 py-px rounded font-mono"
                        style={{ color, backgroundColor: `${color}18` }}
                      >
                        {secs.length}板块
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
