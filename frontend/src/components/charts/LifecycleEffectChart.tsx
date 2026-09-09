import { useCallback, useMemo, useState } from 'react'
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { format } from 'date-fns'
import { cn } from '@/utils/cn'
import { LIFECYCLE_ZH } from '@/lib/lifecycle'
import type { LifecycleSeriesPoint, LifecycleTodayEstimate } from '@/api/stocks'
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
/** 顺序按生命周期推进方向（跟 STATE_ORDER 一致），不是按颜色好看 */
const LINES: { state: string; color: string; dash?: string }[] = [
  { state: 'STREAKING',       color: '#FF4560' },
  { state: 'BROKEN',          color: '#FB923C', dash: '5 2' },
  { state: 'REPAIRING',       color: '#F59E0B', dash: '4 2' },
  { state: 'CROSS_SUCCESS',   color: '#E879F9' },
  { state: 'CROSS_WEAKENING', color: '#5EA6FF', dash: '3 3' },
  { state: 'CROSS_FAILED',    color: '#26C281', dash: '2 4' },
  { state: 'FADED',           color: '#737A96', dash: '1 3' },
]

/**
 * 默认亮着的三条：基线 + 两个核心观察状态。
 *
 * 七条全开会糊成一团，而每天真正要看的是「第一次转强」和「已完成二波」这两组
 * 相对基线怎么走。其余的点图例就能加回来——**只是默认收起，不是没有**。
 */
const DEFAULT_ON = new Set(['REPAIRING', 'CROSS_SUCCESS'])
const C_MAIN = '#5EA6FF'
const L_MAIN = '强势股均涨幅'
const zh = (st: string) => LIFECYCLE_ZH[st] ?? st

interface Row {
  date: string
  [k: string]: string | number | null
}

export function LifecycleEffectChart({ series, history, todayEstimate }: {
  series: LifecycleSeriesPoint[]
  history: MarketHistoryPoint[]
  /** 未收盘时的当日估算。**它不在 series 里**——盘中价不能进跨日统计 */
  todayEstimate?: LifecycleTodayEstimate | null
}) {
  // 默认收起 DEFAULT_ON 之外的线。用 zh(state) 作 key，跟图例/dataKey 一致
  const [hidden, setHidden] = useState<Set<string>>(
    () => new Set(LINES.filter((l) => !DEFAULT_ON.has(l.state)).map((l) => zh(l.state))))
  const toggle = useCallback((k: string) => setHidden((p) => {
    const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n
  }), [])

  // 只画实际出现过的状态。**没出现过就不给图例**——一条永远空着的线，
  // 跟"这个状态今天是 0"看起来一样
  // 估算点也算「出现过」：只有它有数据的状态，图例上也该给得出来
  const present = useMemo(
    () => LINES.filter((l) => series.some((p) => p.values[l.state])
                          || !!todayEstimate?.values[l.state]),
    [series, todayEstimate])

  const rows = useMemo<Row[]>(() => {
    const avgByDate = new Map(history.map((h) => [h.date.slice(0, 10), h.strong_pool_avg_pct]))
    // 估算点拼在末尾。**它是另一种东西**（盘中价 vs 收盘价），所以带个标记，
    // tooltip 上要说出来——不说就是拿盘中价冒充当日结果
    const all: (LifecycleSeriesPoint & { __est?: true })[] =
      todayEstimate ? [...series, { ...todayEstimate, __est: true as const }] : series
    return all.map((p) => {
      const r: Row = { date: format(new Date(p.trade_date), 'MM/dd') }
      r.__est = ('__est' in p && p.__est) ? 1 : 0
      r[L_MAIN] = avgByDate.get(p.trade_date) ?? null
      for (const l of present) {
        // 当天没有该状态的票 → **图上按 0 画**（产品决定：那一组当天没有贡献
        // 赚钱效应）。但数据层不撒谎：后端那边"没有该状态"就是没有这个 key，
        // 这里只是展示层填 0，并且把 n 一起带进 tooltip——n0 就是"当天这组没人"，
        // 跟"这组有人但平均涨 0%"仍然分得出来
        const v = p.values[l.state]
        r[zh(l.state)] = v?.avg ?? 0
        r[`${zh(l.state)}__n`] = v?.n ?? 0
      }
      return r
    })
  }, [series, history, present, todayEstimate])

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
            {/* offset 拉开一点、允许溢出绘图区：贴着光标画会正好压在刚才
                悬停的那几条线上 */}
            <Tooltip content={<Tip present={present} />} offset={24}
                     allowEscapeViewBox={{ x: false, y: true }}
                     wrapperStyle={{ zIndex: 30 }} />
            <Area type="monotone" dataKey={L_MAIN} stroke={C_MAIN} fill="url(#gradLifeMain)"
                  strokeWidth={2} dot={false} activeDot={{ r: 4 }} connectNulls
                  hide={hidden.has(L_MAIN)} />
            {present.map((l) => (
              // 空组已经在上面填成 0 了，这里不会再有 null；connectNulls 保留
              // 只是为了万一后端某天真给出 null 时不画一段假的
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
  // **按赚钱效应从大到小。** 图例顺序是生命周期推进方向，那是为了看懂"这条线是
  // 哪一档"；而悬停时要回答的是"这天谁涨得最多"，两件事排序依据不同。
  // 空值沉底——「那天这组没有成员」不是「最低」
  const rows = [...payload].sort((a, b) => {
    const av = a.value, bv = b.value
    if (av === null && bv === null) return 0
    if (av === null) return 1
    if (bv === null) return -1
    return bv - av
  })
  return (
    // 背景必须是**实心**的：这块浮在折线上面，透出来就看不清了。
    // 这里原来写的是 `bg-bg-surface`——tailwind 配置里根本没有 surface 这个色阶，
    // 那个类什么都不生成，于是 tooltip 一直是全透明的，线直接从底下穿过去。
    // 同一个不存在的类当时还写在另外四处（V2 头部/侧栏、KPI 格子），一并改掉了。
    <div className="bg-bg-elevated border border-bg-border rounded px-2.5 py-1.5
                    text-[11px] space-y-0.5 shadow-xl shadow-black/50">
      <div className="text-text-primary font-medium">
        {label}
        {row.__est === 1 && (
          <span className="ml-1.5 text-warn font-normal">盘中估算 · 未收盘</span>
        )}
      </div>
      {rows.map((p) => {
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
            {/* **n 一起给。** n=1 那天是一只票的涨幅，画成线跟 n=20 一样权威；
                n0 是"当天这组没人"，图上那个 0 不是"不赚不亏" */}
            {typeof n === 'number' && (
              <span className={cn('font-mono',
                n === 0 ? 'text-warn/70' : 'text-text-muted/70')}>
                {n === 0 ? '无成员' : `n${n}`}
              </span>
            )}
          </div>
        )
      })}
      {present.length === 0 && <div className="text-text-muted">没有分状态数据</div>}
    </div>
  )
}
