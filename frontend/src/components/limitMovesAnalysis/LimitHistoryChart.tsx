import { useMemo, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts'
import { cn } from '@/utils/cn'
import type { LimitMoveTrendPoint } from '@/types'
import type { HeightPoint } from '@/api/marketTrend'

type MetricKey = 'limit_up' | 'limit_down' | 'height' | 'multi_board'

const METRICS: { key: MetricKey; label: string; color: string; axis: 'left' | 'right' }[] = [
  { key: 'limit_up',    label: '涨停',     color: '#FF4560', axis: 'left' },
  { key: 'limit_down',  label: '跌停',     color: '#26C281', axis: 'right' },
  { key: 'height',      label: '最高板',   color: '#F59E0B', axis: 'right' },
  { key: 'multi_board', label: '3板以上',  color: '#5EA6FF', axis: 'right' },
]

/**
 * 历史走势。**一次最多两条**——四条挤在一起，涨停数（几十）会把最高板（个位数）
 * 压成一条直线。
 *
 * **炸板数和封板率不在这里。** 它们只有当日值（涨停板块雷达的 summary），
 * 后端没有历史序列。给一条画不出来的曲线，不如直说没有。
 */
export function LimitHistoryChart({ trend, heights }: {
  trend: LimitMoveTrendPoint[]
  heights: HeightPoint[]
}) {
  const [picked, setPicked] = useState<MetricKey[]>(['limit_up', 'height'])

  const toggle = (k: MetricKey) =>
    setPicked((p) => p.includes(k) ? (p.length > 1 ? p.filter((x) => x !== k) : p)
                                   // 满两条时挤掉最早选中的那条
                                   : [...p, k].slice(-2))

  const rows = useMemo(() => {
    const h = new Map(heights.map((p) => [p.date, p]))
    return trend.map((t) => ({
      date: t.date.slice(5),
      涨停: t.limit_up_count,
      跌停: t.limit_down_count,
      最高板: h.get(t.date)?.height ?? null,
      '3板以上': h.get(t.date)?.multi_board_count ?? null,
    }))
  }, [trend, heights])

  const active = METRICS.filter((m) => picked.includes(m.key))

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <span className="text-xs font-semibold text-text-primary">
          历史走势 · 近 {rows.length} 个交易日
        </span>
        <div className="flex gap-1">
          {METRICS.map((m) => (
            <button key={m.key} onClick={() => toggle(m.key)}
                    className={cn('px-2 py-0.5 rounded text-[10px] border transition-colors',
                      picked.includes(m.key)
                        ? 'border-transparent text-bg-base font-medium'
                        : 'border-bg-border text-text-muted hover:text-text-secondary')}
                    style={picked.includes(m.key) ? { background: m.color } : undefined}>
              {m.label}
            </button>
          ))}
        </div>
      </div>
      <div className="h-52">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: '#737A96', fontSize: 10 }}
                   axisLine={false} tickLine={false}
                   interval="preserveStartEnd" minTickGap={28} />
            <YAxis yAxisId="left" width={32} tick={{ fill: '#737A96', fontSize: 10 }}
                   axisLine={false} tickLine={false} allowDecimals={false} />
            <YAxis yAxisId="right" orientation="right" width={32}
                   tick={{ fill: '#737A96', fontSize: 10 }}
                   axisLine={false} tickLine={false} allowDecimals={false} />
            <Tooltip contentStyle={{ background: '#12172A', border: '1px solid #262D40',
                                     borderRadius: 4, fontSize: 11 }} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            {active.map((m) => (
              // connectNulls 关掉：缺那天就是缺，不要连一条不存在的线
              <Line key={m.key} yAxisId={m.axis} type="monotone" dataKey={m.label}
                    stroke={m.color} strokeWidth={1.8} dot={false} connectNulls={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="text-[10px] text-text-muted">
        左轴 = 涨停，右轴 = 其余。一次最多两条：涨停数几十、最高板个位数，
        同轴会把后者压成直线。
        <span className="text-text-secondary">
          {' '}炸板数和封板率只有当日值，后端没有历史序列，所以这里没有。
        </span>
      </p>
    </div>
  )
}
