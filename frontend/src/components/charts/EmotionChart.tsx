import { useState, useCallback } from 'react'
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import type { MarketHistoryPoint } from '@/types'
import { format } from 'date-fns'
import { cn } from '@/utils/cn'

interface EmotionChartProps {
  data: MarketHistoryPoint[]
}

// Group key → display config  (keys match backend: "oscillation" not "oscillating")
const GROUP_CONFIG: Record<string, { label: string; color: string; dash?: string }> = {
  limit_up:    { label: '昨日涨停龙头', color: '#FF4560' },
  oscillation: { label: '昨日震荡龙头', color: '#5EA6FF', dash: '4 2' },
  weakening:   { label: '昨日走弱龙头', color: '#F59E0B', dash: '3 3' },
  broken:      { label: '昨日破位龙头', color: '#26C281', dash: '2 4' },
}
// 只关注强势龙头（涨停 / 震荡），走弱/破位不展示
const GROUP_KEYS = ['limit_up', 'oscillation'] as const

const C_MAIN  = '#5EA6FF'
const L_MAIN  = '强势股均涨幅'

// ─── Custom legend ────────────────────────────────────────────────────────────

interface LegendItem {
  key: string       // dataKey used in chart
  label: string
  color: string
  dash?: string
  isArea?: boolean
}

function ChartLegend({
  items,
  hidden,
  onToggle,
}: {
  items: LegendItem[]
  hidden: Set<string>
  onToggle: (key: string) => void
}) {
  return (
    <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 pt-1 pb-0.5">
      {items.map(({ key, label, color, dash, isArea }) => {
        const isHidden = hidden.has(key)
        return (
          <button
            key={key}
            onClick={() => onToggle(key)}
            className={cn(
              'flex items-center gap-1.5 text-xs transition-opacity select-none',
              isHidden ? 'opacity-25 hover:opacity-50' : 'opacity-100 hover:opacity-75',
            )}
            title={isHidden ? `显示 ${label}` : `隐藏 ${label}`}
          >
            {/* Line/area preview */}
            <svg width="20" height="10" className="shrink-0">
              {isArea ? (
                <>
                  <defs>
                    <linearGradient id={`lg-${key}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={color} stopOpacity={0.4} />
                      <stop offset="100%" stopColor={color} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <rect x="0" y="4" width="20" height="6" fill={`url(#lg-${key})`} />
                  <line x1="0" y1="5" x2="20" y2="5" stroke={color} strokeWidth={2} />
                </>
              ) : (
                <line
                  x1="0" y1="5" x2="20" y2="5"
                  stroke={color}
                  strokeWidth={1.5}
                  strokeDasharray={dash ?? 'none'}
                />
              )}
            </svg>
            <span style={{ color: isHidden ? '#737A96' : color }}>{label}</span>
          </button>
        )
      })}
    </div>
  )
}

// ─── Tooltip ─────────────────────────────────────────────────────────────────

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  return (
    <div className="card p-2 text-xs space-y-1 shadow-xl min-w-[140px]">
      <div className="text-text-muted mb-0.5">{label}</div>
      {payload.map((p: any) => {
        if (p.value == null) return null
        const val = typeof p.value === 'number' ? p.value : null
        if (val == null) return null
        return (
          <div key={p.name} className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: p.color }} />
            <span className="text-text-secondary">{p.name}</span>
            <span className="font-mono ml-auto" style={{ color: p.color }}>
              {val > 0 ? '+' : ''}{val.toFixed(2)}%
            </span>
          </div>
        )
      })}
    </div>
  )
}

// ─── Chart ───────────────────────────────────────────────────────────────────

export function EmotionChart({ data }: EmotionChartProps) {
  const [hidden, setHidden] = useState<Set<string>>(new Set())

  const toggleSeries = useCallback((key: string) => {
    setHidden((prev) => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }, [])

  // Determine which group keys actually have data
  const presentGroupKeys = GROUP_KEYS.filter((key) =>
    data.some((d) => (d.profit_effect_groups ?? []).some((g) => g.key === key)),
  )

  const chartData = data.map((d) => {
    const groups = d.profit_effect_groups ?? []
    const groupMap: Record<string, number | null> = {}
    for (const g of groups) groupMap[g.key] = g.avg_pct

    return {
      date: format(new Date(d.date), 'MM/dd'),
      [L_MAIN]: d.strong_pool_avg_pct ?? null,
      ...Object.fromEntries(
        presentGroupKeys.map((key) => [GROUP_CONFIG[key].label, groupMap[key] ?? null])
      ),
    }
  })

  // Y-axis domain — only from visible series
  const allVals = chartData.flatMap((d) => [
    hidden.has(L_MAIN) ? null : d[L_MAIN],
    ...presentGroupKeys
      .filter((key) => !hidden.has(GROUP_CONFIG[key].label))
      .map((key) => (d as any)[GROUP_CONFIG[key].label]),
  ]).filter((v): v is number => v != null)

  const minVal = allVals.length ? Math.min(...allVals) : -5
  const maxVal = allVals.length ? Math.max(...allVals) : 5
  const pad    = Math.max(1, (maxVal - minVal) * 0.15)
  const yMin   = Math.floor(minVal - pad)
  const yMax   = Math.ceil(maxVal + pad)

  // Legend items
  const legendItems: LegendItem[] = [
    { key: L_MAIN, label: L_MAIN, color: C_MAIN, isArea: true },
    ...presentGroupKeys.map((key) => ({
      key:   GROUP_CONFIG[key].label,
      label: GROUP_CONFIG[key].label,
      color: GROUP_CONFIG[key].color,
      dash:  GROUP_CONFIG[key].dash,
    })),
  ]

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData} margin={{ top: 4, right: 8, left: -12, bottom: 0 }}>
            <defs>
              <linearGradient id="gradMain" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={C_MAIN} stopOpacity={0.25} />
                <stop offset="95%" stopColor={C_MAIN} stopOpacity={0}    />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
            <ReferenceLine y={0} stroke="#262D40" strokeWidth={1.5} strokeDasharray="2 2" />
            <XAxis
              dataKey="date"
              tick={{ fill: '#737A96', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={[yMin, yMax]}
              tickFormatter={(v) => `${v > 0 ? '+' : ''}${v}%`}
              tick={{ fill: '#737A96', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              width={46}
            />
            <Tooltip content={<CustomTooltip />} />

            {/* Primary: 强势股均涨幅 */}
            <Area
              type="monotone"
              dataKey={L_MAIN}
              stroke={C_MAIN}
              fill="url(#gradMain)"
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
              connectNulls
              hide={hidden.has(L_MAIN)}
            />

            {/* Group breakdown lines */}
            {presentGroupKeys.map((key) => {
              const { label, color, dash } = GROUP_CONFIG[key]
              return (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={label}
                  stroke={color}
                  strokeWidth={1.5}
                  strokeDasharray={dash}
                  dot={false}
                  activeDot={{ r: 3 }}
                  connectNulls
                  hide={hidden.has(label)}
                />
              )
            })}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Custom clickable legend */}
      <ChartLegend items={legendItems} hidden={hidden} onToggle={toggleSeries} />
    </div>
  )
}
