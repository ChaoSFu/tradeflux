import { useCallback, useMemo, useState } from 'react'
import {
  ComposedChart, Area, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { format } from 'date-fns'
import { cn } from '@/utils/cn'
import { LIFECYCLE_ZH } from '@/lib/lifecycle'
import type { LifecycleStateCounts } from '@/api/stocks'
import { LIFECYCLE_DEFAULT_ON, LIFECYCLE_LINES } from '@/components/charts/LifecycleEffectChart'

/**
 * 逐日各生命周期状态的**只数**（2026-11-15 用户提）。
 *
 * 想法：一只票穿越成功、立起标杆之后，一批票跟着模仿；模仿的规模大到一定程度，往往是短期
 * 顶部。所以先把「每天各状态各有多少只」摆出来。**这里只是计数**：不打分、不设阈值、
 * 不判见顶——那个假设在历史上站不站得住还没验证过。
 *
 * 颜色、图例、默认亮的三条跟上面「逐日赚钱效应」同一套，两张图对着看。
 * 池子大小每天在变（近 60 日最高连板 ≥ 4），所以总只数一起画、也能切成占比看。
 *
 * 「在场只数」（2026-11-22 用户要的）：高标池里还在震荡 / 强势的有多少只。按四个在场状态
 * 加起来（连板中 + 刚断板 + 修复中 + 穿越成功），不是拿池子总数减三种收尾——池子里还有判不出
 * 状态的票，减法会把它们也当成在场。判不出的只数在悬停里单独写出来。
 */
const C_POOL = '#737A96'
const L_POOL = '高标池总数'
const C_ALIVE = '#E2E8F0'
const L_ALIVE = '在场只数'
const ALIVE = ['STREAKING', 'BROKEN', 'REPAIRING', 'CROSS_SUCCESS']
const ALIVE_HINT = '在场 = 连板中 + 刚断板 + 修复中 + 穿越成功（高标池去掉周期结束、修复失败、成功后走弱）'
const FOCUS = 'CROSS_SUCCESS'
const zh = (st: string) => LIFECYCLE_ZH[st] ?? st

type Mode = 'count' | 'share'
interface Row { date: string; full: string; pool: number; [k: string]: string | number }
const aliveOf = (p: { counts: Record<string, number> }) => ALIVE.reduce((t, st) => t + (p.counts[st] ?? 0), 0)

export function LifecycleCountChart({ data }: { data: LifecycleStateCounts | null | undefined }) {
  const series = data?.series ?? []
  const [mode, setMode] = useState<Mode>('count')
  const [hidden, setHidden] = useState<Set<string>>(
    () => new Set(LIFECYCLE_LINES.filter((l) => !LIFECYCLE_DEFAULT_ON.has(l.state)).map((l) => zh(l.state))))
  const toggle = useCallback((k: string) => setHidden((p) => {
    const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n
  }), [])

  // 只给窗口里出现过的状态图例：一条永远是 0 的线跟「这个状态今天 0 只」看起来一样
  const present = useMemo(
    () => LIFECYCLE_LINES.filter((l) => series.some((p) => (p.counts[l.state] ?? 0) > 0)),
    [series])

  const rows = useMemo<Row[]>(() => series.map((p) => {
    const alive = aliveOf(p)
    const known = Object.values(p.counts).reduce((t, n) => t + n, 0)
    const r: Row = { date: format(new Date(p.trade_date), 'MM/dd'), full: p.trade_date, pool: p.pool,
                     [L_ALIVE]: mode === 'count' ? alive : (p.pool ? Math.round((alive / p.pool) * 1000) / 10 : 0),
                     alive__n: alive, unknown__n: Math.max(0, p.pool - known) }
    for (const l of present) {
      // 当天没有该状态的票：后端没有这个 key，这里就是 0 只——计数的 0 是事实，不是缺数据
      const n = p.counts[l.state] ?? 0
      r[zh(l.state)] = mode === 'count' ? n : (p.pool ? Math.round((n / p.pool) * 1000) / 10 : 0)
      r[`${zh(l.state)}__n`] = n
      r[`${zh(l.state)}__in`] = p.entered[l.state] ?? 0
    }
    return r
  }), [series, present, mode])

  // 摘要：穿越成功的最新值、占比、当天新进、近 N 日最高 / 中位。只是描述，不是信号
  const summary = useMemo(() => {
    if (!series.length) return null
    const last = series[series.length - 1]
    const vals = series.map((p) => p.counts[FOCUS] ?? 0)
    const sorted = [...vals].sort((a, b) => a - b)
    const mid = sorted.length % 2 ? sorted[(sorted.length - 1) / 2]
      : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2
    const max = Math.max(...vals)
    const maxDay = series[vals.lastIndexOf(max)].trade_date
    return { last, n: last.counts[FOCUS] ?? 0, entered: last.entered[FOCUS] ?? 0, max, maxDay, mid, days: series.length,
             alive: aliveOf(last) }
  }, [series])

  if (!series.length) {
    return <div className="h-56 flex items-center justify-center text-text-muted text-sm">暂无逐日数据</div>
  }

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
        {summary && (
          <span className="text-text-secondary">
            {summary.last.trade_date.slice(5)} 穿越成功
            <span className="mx-1 font-mono tabular-nums text-sm font-semibold" style={{ color: '#E879F9' }}>{summary.n}</span>只
            <span className="text-text-muted">
              （高标池 {summary.last.pool} 只，占 {summary.last.pool ? ((summary.n / summary.last.pool) * 100).toFixed(0) : '—'}%）
            </span>
            · 当天新进 <span className="font-mono tabular-nums text-text-primary">{summary.entered}</span>
            <span className="text-text-muted">
              {summary.max === 0
                ? ` · 近 ${summary.days} 个交易日都是 0`
                : ` · 近 ${summary.days} 个交易日最高 ${summary.max}（${summary.maxDay.slice(5)}）、中位 ${summary.mid}`}
            </span>
            <span className="text-text-muted" title={ALIVE_HINT}>
              {' · '}在场 <span className="font-mono tabular-nums text-text-primary">{summary.alive}</span> 只
            </span>
          </span>
        )}
        <div className="ml-auto inline-flex rounded border border-bg-border overflow-hidden">
          {([['count', '只数'], ['share', '占池子比例']] as const).map(([k, l]) => (
            <button key={k} onClick={() => setMode(k)}
                    className={cn('px-2 py-0.5', mode === k ? 'bg-accent/15 text-accent' : 'text-text-muted hover:text-text-secondary')}>{l}</button>
          ))}
        </div>
      </div>

      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: '#737A96', fontSize: 10 }} axisLine={false} tickLine={false}
                   interval="preserveStartEnd" minTickGap={28} />
            <YAxis yAxisId="s" tick={{ fill: '#737A96', fontSize: 10 }} axisLine={false} tickLine={false} width={36}
                   allowDecimals={false}
                   tickFormatter={(v: number) => (mode === 'count' ? `${v}` : `${v}%`)} />
            {/* 池子总数单独一根右轴：60 只上下，跟各状态的个位数放一根轴会把它们压扁 */}
            <YAxis yAxisId="p" orientation="right" tick={{ fill: '#4B5368', fontSize: 10 }} axisLine={false}
                   tickLine={false} width={30} allowDecimals={false} hide={mode === 'share' || hidden.has(L_POOL)} />
            <Tooltip content={<Tip present={present} mode={mode} />} offset={24}
                     allowEscapeViewBox={{ x: false, y: true }} wrapperStyle={{ zIndex: 30 }} />
            <Area yAxisId="p" type="stepAfter" dataKey="pool" name={L_POOL} stroke={C_POOL} strokeDasharray="2 3"
                  fill={C_POOL} fillOpacity={0.06} strokeWidth={1} dot={false} isAnimationActive={false}
                  hide={mode === 'share' || hidden.has(L_POOL)} />
            {/* 在场只数走左轴，跟各状态同一把尺子：它是四条状态线之和，必须画在它们上面。
                放右轴跟池子总数一起的话，在场 19 会画得比刚断板 19 还低 */}
            <Line yAxisId="s" type="stepAfter" dataKey={L_ALIVE} stroke={C_ALIVE}
                  strokeWidth={1.5} dot={false} activeDot={{ r: 3 }} isAnimationActive={false}
                  hide={hidden.has(L_ALIVE)} />
            {present.map((l) => (
              <Line key={l.state} yAxisId="s" type="monotone" dataKey={zh(l.state)} stroke={l.color}
                    strokeWidth={l.state === FOCUS ? 2.5 : 1.5} strokeDasharray={l.dash} dot={false}
                    activeDot={{ r: 3 }} hide={hidden.has(zh(l.state))} />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="flex flex-wrap justify-center gap-x-4 gap-y-1">
        {[...(mode === 'count' ? [{ key: L_POOL, color: C_POOL, dash: '2 3' as string | undefined }] : []),
          { key: L_ALIVE, color: C_ALIVE, dash: undefined as string | undefined },
          ...present.map((l) => ({ key: zh(l.state), color: l.color, dash: l.dash }))]
          .map((it) => {
            const off = hidden.has(it.key)
            return (
              <button key={it.key} onClick={() => toggle(it.key)}
                      className={cn('flex items-center gap-1.5 text-xs select-none transition-opacity',
                        off ? 'opacity-25 hover:opacity-50' : 'hover:opacity-75')}
                      title={`${off ? '显示' : '隐藏'} ${it.key}${it.key === L_ALIVE ? `\n${ALIVE_HINT}` : ''}`}>
                <svg width="20" height="10" className="shrink-0">
                  <line x1="0" y1="5" x2="20" y2="5" stroke={it.color} strokeWidth={2} strokeDasharray={it.dash} />
                </svg>
                <span className="text-text-secondary">{it.key}</span>
              </button>
            )
          })}
      </div>
      {!!data?.skipped_unsettled.length && (
        <div className="text-[10px] text-warn text-center">
          {data.skipped_unsettled.join('、')} 还没收盘，状态判不出来，这一天先不画（收盘同步后补上）
        </div>
      )}
    </div>
  )
}

function Tip({ active, payload, present, mode }: {
  active?: boolean
  payload?: { dataKey: string; value: number; color: string; payload: Row }[]
  present: { state: string; color: string }[]
  mode: Mode
}) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  // 悬停时按只数从多到少：这时要回答的是「这天哪个状态的票最多」
  const lines = present
    .map((l) => ({ name: zh(l.state), color: l.color, n: Number(row[`${zh(l.state)}__n`] ?? 0),
                   inn: Number(row[`${zh(l.state)}__in`] ?? 0) }))
    .sort((a, b) => b.n - a.n)
  return (
    <div className="bg-bg-elevated border border-bg-border rounded px-2.5 py-1.5 text-[11px] space-y-0.5 shadow-xl shadow-black/50">
      <div className="text-text-primary font-medium">
        {row.full}<span className="ml-2 text-text-muted font-normal">高标池 {row.pool} 只</span>
      </div>
      <div className="flex items-center gap-1.5 whitespace-nowrap border-b border-bg-border/60 pb-0.5 mb-0.5">
        <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: C_ALIVE }} />
        <span className="text-text-secondary">{L_ALIVE}</span>
        <span className="font-mono tabular-nums ml-auto text-text-primary">
          {Number(row.alive__n)} 只{row.pool ? <span className="text-text-muted">（{((Number(row.alive__n) / row.pool) * 100).toFixed(0)}%）</span> : null}
        </span>
        <span className="text-[10px] text-text-muted w-12 text-right">
          {Number(row.unknown__n) ? `判不出 ${Number(row.unknown__n)}` : ''}
        </span>
      </div>
      {lines.map((l) => (
        <div key={l.name} className="flex items-center gap-1.5 whitespace-nowrap">
          <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: l.color }} />
          <span className="text-text-secondary">{l.name}</span>
          <span className="font-mono tabular-nums ml-auto text-text-primary">
            {l.n} 只{mode === 'share' && row.pool ? <span className="text-text-muted">（{((l.n / row.pool) * 100).toFixed(0)}%）</span> : null}
          </span>
          <span className={cn('font-mono text-[10px] w-12 text-right', l.inn ? 'text-accent' : 'text-text-muted/50')}
                title="当天刚转入这个状态的只数">新进 {l.inn}</span>
        </div>
      ))}
    </div>
  )
}
