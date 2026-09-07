import { useCallback, useMemo, useState } from 'react'
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { format } from 'date-fns'
import { cn } from '@/utils/cn'
import { LIFECYCLE_ZH } from '@/lib/lifecycle'
import type { LifecycleSeriesPoint } from '@/api/stocks'
import type { MarketHistoryPoint } from '@/types'

/**
 * 逐日赚钱效应,按**生命周期状态**分组。
 *
 * 每一条线读作:「昨天处于该状态的票,今天平均涨了多少」。
 *
 * 换掉了旧的四条(昨日涨停龙头/震荡/走弱/破位)——那四组按 `Stock.phase` 分,
 * 只是"收盘价在哪条均线下面"的单日快照,一只刚断板正在修复的票和一只连跌十天
 * 的老龙都可能被叫「震荡龙头」。
 *
 * **均值不是中位数**:这条线要跟「强势股均涨幅」那条可比,而那条一直是均值。
 */
const LINES: { state: string; color: string; dash?: string }[] = [
  { state: 'STREAKING',       color: '#FF4560' },
  { state: 'REPAIRING',       color: '#F59E0B', dash: '4 2' },
  { state: 'CROSS_SUCCESS',   color: '#E879F9' },
  { state: 'CROSS_WEAKENING', color: '#5EA6FF', dash: '3 3' },
  { state: 'CROSS_FAILED',    color: '#26C281', dash: '2 4' },
  { state: 'FADED',           color: '#737A96', dash: '1 3' },
]
const C_MAIN = '#5EA6FF'
const L_MAIN = '强势股均涨幅'
const zh = (st: string) => LIFECYCLE_ZH[st] ?? st

interface Row {
  date: string
  [k: string]: string | number | null
}

export function LifecycleEffectChart({ series, history }: {
  series: LifecycleSeriesPoint[]
  history: MarketHistoryPoint[]
}) {
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const toggle = useCallback((k: string) => setHidden((p) => {
    const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n
  }), [])

  // 只画实际出现过的状态。**没出现过就不给图例**——一条永远空着的线，
  // 跟"这个状态今天是 0"看起来一样
  const present = useMemo(
    () => LINES.filter((l) => series.some((p) => p.values[l.state])), [series])

  const rows = useMemo<Row[]>(() => {
    const avgByDate = new Map(history.map((h) => [h.date.slice(0, 10), h.strong_pool_avg_pct]))
    return series.map((p) => {
      const r: Row = { date: format(new Date(p.trade_date), 'MM/dd') }
      r[L_MAIN] = avgByDate.get(p.trade_date) ?? null
      for (const l of present) {
        // **没有该状态的票时是 null，不是 0**。0 会被读成"那天这组不赚不亏"
        r[zh(l.state)] = p.values[l.state]?.avg ?? null
        r[`${zh(l.state)}__n`] = p.values[l.state]?.n ?? null
      }
      return r
    })
  }, [series, history, present])

  if (!series.length) {
    return <div className="h-64 flex items-center justify-center text-text-muted text-sm">
      暂无逐日数据
    </div>
  }

  return (
    <div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="gradLifeMain" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={C_MAIN} stopOpacity={0.25} />
                <stop offset="95%" stopColor={C_MAIN} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: '#737A96', fontSize: 10 }}
                   axisLine={false} tickLine={false}
                   interval="preserveStartEnd" minTickGap={28} />
            <YAxis tick={{ fill: '#737A96', fontSize: 10 }} axisLine={false}
                   tickLine={false} width={44}
                   tickFormatter={(v: number) => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`} />
            <ReferenceLine y={0} stroke="#3A4258" />
            <Tooltip content={<Tip present={present} />} />
            <Area type="monotone" dataKey={L_MAIN} stroke={C_MAIN} fill="url(#gradLifeMain)"
                  strokeWidth={2} dot={false} activeDot={{ r: 4 }} connectNulls
                  hide={hidden.has(L_MAIN)} />
            {present.map((l) => (
              // connectNulls 关掉：那天没有这个状态的票就是断的，
              // 连过去等于凭空造一段并不存在的走势
              <Line key={l.state} type="monotone" dataKey={zh(l.state)} stroke={l.color}
                    strokeWidth={1.5} strokeDasharray={l.dash} dot={false}
                    activeDot={{ r: 3 }} connectNulls={false}
                    hide={hidden.has(zh(l.state))} />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 pt-1">
        {[{ key: L_MAIN, color: C_MAIN, area: true },
          ...present.map((l) => ({ key: zh(l.state), color: l.color, dash: l.dash, area: false }))]
          .map((it) => {
            const off = hidden.has(it.key)
            return (
              <button key={it.key} onClick={() => toggle(it.key)}
                      className={cn('flex items-center gap-1.5 text-xs select-none transition-opacity',
                        off ? 'opacity-25 hover:opacity-50' : 'hover:opacity-75')}
                      title={off ? `显示 ${it.key}` : `隐藏 ${it.key}`}>
                <svg width="20" height="10" className="shrink-0">
                  <line x1="0" y1="5" x2="20" y2="5" stroke={it.color} strokeWidth={2}
                        strokeDasharray={'dash' in it ? it.dash : undefined} />
                </svg>
                <span className="text-text-secondary">{it.key}</span>
              </button>
            )
          })}
      </div>
    </div>
  )
}

interface TipProps {
  active?: boolean
  label?: string
  payload?: { dataKey: string; value: number | null; color: string; payload: Row }[]
  present: { state: string }[]
}

function Tip({ active, label, payload, present }: TipProps) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  return (
    <div className="bg-bg-surface border border-bg-border rounded px-2.5 py-1.5
                    text-[11px] space-y-0.5 shadow-lg">
      <div className="text-text-primary font-medium">{label}</div>
      {payload.map((p) => {
        const n = row[`${p.dataKey}__n`]
        return (
          <div key={p.dataKey} className="flex items-center gap-1.5 whitespace-nowrap">
            <span className="w-1.5 h-1.5 rounded-full shrink-0"
                  style={{ background: p.color }} />
            <span className="text-text-secondary">{p.dataKey}</span>
            <span className="font-mono tabular-nums ml-auto"
                  style={{ color: (p.value ?? 0) >= 0 ? '#FF4560' : '#26C281' }}>
              {p.value === null ? '—' : `${p.value > 0 ? '+' : ''}${p.value.toFixed(2)}%`}
            </span>
            {/* **n 一起给。** n=1 的那天是一只票的涨幅，画成线跟 n=20 一样权威 */}
            {typeof n === 'number' && (
              <span className="text-text-muted/70 font-mono">n{n}</span>
            )}
          </div>
        )
      })}
      {present.length === 0 && <div className="text-text-muted">没有分状态数据</div>}
    </div>
  )
}
