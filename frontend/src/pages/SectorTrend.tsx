/**
 * 板块趋势 · 主升板块雷达（V1 /sector-trend）
 *
 * 回答「现在哪几个板块在主升」。后端 /sector-trend 给每个关注板块过四道闸——趋势（板块指数
 * 均线）、相对强度（跑赢上证多少、在关注板块里排第几）、生态（成分股涨停序列与连板高度）、
 * 风险（乖离与见顶迹象）——状态由闸门组合决定，**没有分数**。规则见 docs/SECTOR_MAINLINE.md。
 * 只描述板块处在什么状态，不给买卖建议。
 *
 * 原来的「传统多周期排名」（SectorRanking + 生命周期分布条）折叠在页面底部，展开才挂载：
 * 它要拉带成员股的全量板块列表（2.1MB），不看就不付这个钱。
 */
import { Fragment, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Info, Loader2 } from 'lucide-react'
import {
  Bar, CartesianGrid, ComposedChart, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { fetchSectorTrend, fetchSectorTrendDetail } from '@/api/sectorTrend'
import type {
  GateKey, GateStatus, LuTrend, MainlineState, SectorTrendDetail, SectorTrendItem, SectorTrendList,
} from '@/api/sectorTrend'
import SectorRanking from './SectorRanking'
import { PhaseLifecycleBar } from '@/components/common/PhaseLifecycleBar'
import { cn } from '@/utils/cn'

// ─── 状态与闸门 ───────────────────────────────────────────────────────────────

const STATE_ORDER: MainlineState[] = [
  'ACCELERATION', 'MAIN_RISE', 'IGNITION', 'CLIMAX', 'DIVERGENCE', 'WEAKENING', 'NONE', 'UNKNOWN',
]

// 状态配色刻意不用 up/down（那是价格涨跌方向）也不用 safe/danger（那是闸门通过/不过）
const STATE_META: Record<MainlineState, { label: string; desc: string; cls: string; dot: string }> = {
  ACCELERATION: { label: '加速', desc: '在主升里，且偏离 MA20 ≥8%、5 日涨幅 ≥6%',
    cls: 'text-fuchsia-300 bg-fuchsia-500/15 border-fuchsia-500/40', dot: 'bg-fuchsia-400' },
  MAIN_RISE: { label: '主升', desc: '趋势、相对强度、生态三道闸，近 3 天里有 2 天全过',
    cls: 'text-sky-300 bg-sky-500/15 border-sky-500/40', dot: 'bg-sky-400' },
  IGNITION: { label: '点火', desc: '三道闸过了两道，或今天第一次全过（还差一天确认）',
    cls: 'text-teal-300 bg-teal-500/10 border-teal-500/30', dot: 'bg-teal-400' },
  CLIMAX: { label: '高潮', desc: '极端乖离，且出现见顶迹象（封板率低、放量滞涨、大阴、跌停）',
    cls: 'text-orange-300 bg-orange-500/15 border-orange-500/40', dot: 'bg-orange-400' },
  DIVERGENCE: { label: '分歧', desc: '最近 5 天主升过，今天有闸门没过；或 K 线还强，但涨停、高度在退',
    cls: 'text-yellow-300 bg-yellow-500/10 border-yellow-500/30', dot: 'bg-yellow-400' },
  WEAKENING: { label: '转弱', desc: '最近 5 天主升过，现在跌破 MA20 或跑输上证',
    cls: 'text-slate-300 bg-slate-500/15 border-slate-500/30', dot: 'bg-slate-400' },
  NONE: { label: '无', desc: '不在主线里',
    cls: 'text-text-muted bg-bg-elevated border-bg-border', dot: 'bg-text-muted/40' },
  UNKNOWN: { label: '未知', desc: '缺数据判断不了，缺什么写在理由里',
    cls: 'text-text-muted border-dashed border-bg-border', dot: 'border border-text-muted/60' },
}
// 卡片上列名字的状态（「无」「未知」只给数）
const NAMED = new Set<MainlineState>(['ACCELERATION', 'MAIN_RISE', 'IGNITION', 'CLIMAX', 'DIVERGENCE', 'WEAKENING'])

const GATES: GateKey[] = ['trend', 'rs', 'ecology', 'risk']
const GATE_LABEL: Record<GateKey, string> = { trend: '趋势', rs: '相对强度', ecology: '生态', risk: '风险' }
const GATE_SHORT: Record<GateKey, string> = { trend: '势', rs: '强', ecology: '生', risk: '险' }
const GATE_WORD: Record<GateStatus, string> = { PASS: '通过', WARN: '提示', FAIL: '不过', UNKNOWN: '不知道' }
const GATE_CLS: Record<GateStatus, string> = {
  PASS: 'bg-safe/15 text-safe border-safe/30',
  WARN: 'bg-warn/15 text-warn border-warn/30',
  FAIL: 'bg-danger/15 text-danger border-danger/30',
  UNKNOWN: 'bg-bg-elevated text-text-muted border-bg-border border-dashed',
}
const GATE_DOT: Record<GateStatus, string> = {
  PASS: 'bg-safe', WARN: 'bg-warn', FAIL: 'bg-danger', UNKNOWN: 'bg-bg-border',
}
const LU_TREND: Record<LuTrend, string> = {
  EXPANDING: '扩散', STABLE: '平稳', CONTRACTING: '收缩', SPIKE: '单日爆发', UNKNOWN: '未知',
}

// ─── 格式 ─────────────────────────────────────────────────────────────────────

const fmt = (v: number | null | undefined, d = 1) =>
  v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}`
const fmtPct = (v: number | null | undefined, d = 1) => (v == null ? '—' : `${fmt(v, d)}%`)
// 收益、相对强度按 A 股习惯：涨红跌绿
const dirCls = (v: number | null | undefined) =>
  v == null ? 'text-text-muted' : v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-secondary'

const AXIS_TICK = { fill: '#737A96', fontSize: 11 }
const TOOLTIP_STYLE = { background: '#181D2A', border: '1px solid #262D40', borderRadius: 6, fontSize: 12 }

// ─── 小部件 ───────────────────────────────────────────────────────────────────

function StateBadge({ state, small }: { state: MainlineState; small?: boolean }) {
  const m = STATE_META[state]
  return (
    <span title={m.desc}
          className={cn('inline-block rounded border whitespace-nowrap', small ? 'px-1 text-[11px]' : 'px-1.5 py-0.5 text-xs', m.cls)}>
      {m.label}
    </span>
  )
}

function Trail({ trail }: { trail: SectorTrendItem['trail'] }) {
  return (
    <span className="inline-flex items-center gap-0.5" title="最近 5 天的状态，最右是状态基准日">
      {trail.map((t) => (
        <span key={t.date} title={`${t.date} ${STATE_META[t.state].label}`}
              className={cn('w-1.5 h-1.5 rounded-full', STATE_META[t.state].dot)} />
      ))}
    </span>
  )
}

function GatePills({ gates }: { gates: SectorTrendItem['gates'] }) {
  return (
    <span className="inline-flex gap-1">
      {GATES.map((g) => (
        <span key={g} title={`${GATE_LABEL[g]} · ${GATE_WORD[gates[g].status]}：${gates[g].reason}`}
              className={cn('w-5 h-5 inline-flex items-center justify-center rounded border text-[11px]',
                            GATE_CLS[gates[g].status])}>
          {GATE_SHORT[g]}
        </span>
      ))}
    </span>
  )
}

function LuSeries({ series, dates }: { series: (number | null)[]; dates?: string[] }) {
  return (
    <span className="font-mono text-xs">
      {series.map((v, i) => (
        <Fragment key={i}>
          {i > 0 && <span className="text-text-muted/50">·</span>}
          <span title={dates?.[i]}
                className={cn(i >= series.length - 3 ? 'text-text-primary' : 'text-text-muted',
                              v == null && 'text-warn')}>
            {v ?? '?'}
          </span>
        </Fragment>
      ))}
    </span>
  )
}

// ─── 顶部 ─────────────────────────────────────────────────────────────────────

function Summary({ data }: { data: SectorTrendList }) {
  const main = data.sectors.filter((s) => s.state === 'ACCELERATION' || s.state === 'MAIN_RISE')
  return (
    <div className="card p-3 space-y-1.5">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <div className="text-sm font-semibold text-text-primary">主升板块雷达</div>
        <div className="text-xs text-text-secondary">
          状态基准：<span className="font-mono text-text-primary">{data.state_date ?? '—'}</span> 收盘
        </div>
        <div className="text-[11px] text-text-muted">
          对比 {data.benchmark.name} · 事实 → 四道闸 → 状态，不打分 · 只描述状态，不构成买卖建议 · {data.version}
        </div>
      </div>
      <div className="text-xs text-text-secondary">
        主线（加速 + 主升）：
        {main.length
          ? main.map((s, i) => (
              <Fragment key={s.code}>
                {i > 0 && '、'}
                <span className={s.state === 'ACCELERATION' ? 'text-fuchsia-300' : 'text-sky-300'}>{s.name}</span>
              </Fragment>
            ))
          : <span className="text-text-muted">没有板块在主升</span>}
      </div>
      {data.notes.map((n) => (
        <div key={n} className="text-[11px] text-warn flex items-start gap-1">
          <Info className="w-3 h-3 mt-0.5 shrink-0" />{n}
        </div>
      ))}
    </div>
  )
}

function StateCards({ data, filter, onSelect }: {
  data: SectorTrendList; filter: MainlineState | null; onSelect: (s: MainlineState | null) => void
}) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-2">
      {STATE_ORDER.map((st) => {
        const m = STATE_META[st]
        const n = data.counts[st] ?? 0
        const names = data.sectors.filter((s) => s.state === st).map((s) => s.name)
        const active = filter === st
        return (
          <button key={st} type="button" title={m.desc} disabled={n === 0}
                  onClick={() => onSelect(active ? null : st)}
                  className={cn('card p-2.5 text-left border transition-all',
                                active ? m.cls : 'border-transparent hover:bg-bg-elevated',
                                n === 0 && 'opacity-40 cursor-default',
                                filter && !active && 'opacity-60')}>
            <div className="flex items-center gap-1.5 text-xs">
              <span className={cn('w-2 h-2 rounded-full shrink-0', m.dot)} />
              <span className="text-text-secondary">{m.label}</span>
              <span className="ml-auto font-mono text-base text-text-primary">{n}</span>
            </div>
            {NAMED.has(st) && names.length > 0 && (
              <div className="mt-1 text-[11px] text-text-secondary truncate" title={names.join('、')}>
                {names.slice(0, 4).join('、')}{names.length > 4 && ' …'}
              </div>
            )}
          </button>
        )
      })}
    </div>
  )
}

// ─── 展开：为什么是这个状态 + 两张图 ───────────────────────────────────────────

function HistoryStrip({ history }: { history: SectorTrendDetail['history'] }) {
  return (
    <div>
      <div className="text-[11px] text-text-muted mb-1">
        近 {history.length} 个交易日（滞回：近 3 天里 2 天三道闸全过才进主升，主升里掉一天先记分歧）
      </div>
      <div className="grid grid-cols-5 sm:grid-cols-10 gap-1">
        {history.map((h) => (
          <div key={h.date} title={`${h.date} ${h.state_label}：${h.reason}`} className="text-center">
            <div className="text-[10px] text-text-muted font-mono">{h.date.slice(5)}</div>
            <div className="mt-0.5"><StateBadge state={h.state} small /></div>
            <div className="flex justify-center gap-0.5 mt-1">
              {GATES.map((g) => (
                <span key={g} className={cn('w-1.5 h-1.5 rounded-sm', GATE_DOT[h.gates[g].status])} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function Why({ item, history }: { item: SectorTrendItem; history?: SectorTrendDetail['history'] }) {
  return (
    <div className="space-y-3">
      <div>
        <div className="text-xs text-text-secondary flex items-center gap-1.5">
          为什么是 <StateBadge state={item.state} />
          <span className="text-[11px] text-text-muted">成分 {item.members} 只</span>
        </div>
        <div className="text-xs text-text-primary mt-1.5 leading-relaxed">{item.state_reason}</div>
      </div>
      <div className="space-y-1.5">
        {GATES.map((g) => (
          <div key={g} className="flex items-start gap-2 text-xs">
            <span className={cn('shrink-0 w-[4.5rem] text-center py-0.5 rounded border text-[11px]',
                                GATE_CLS[item.gates[g].status])}>
              {GATE_LABEL[g]} · {GATE_WORD[item.gates[g].status]}
            </span>
            <span className="text-text-secondary leading-relaxed">{item.gates[g].reason}</span>
          </div>
        ))}
      </div>
      {item.evidence.length > 0 && (
        <div className="text-[11px] text-text-muted space-y-0.5">
          {item.evidence.map((e) => (
            <div key={e} className="flex items-start gap-1"><Info className="w-3 h-3 mt-0.5 shrink-0" />{e}</div>
          ))}
        </div>
      )}
      {history && <HistoryStrip history={history} />}
    </div>
  )
}

function PriceChart({ bars }: { bars: SectorTrendDetail['bars'] }) {
  const data = bars.map((b) => ({ ...b, d: b.date.slice(5) }))
  return (
    <div>
      <div className="text-[11px] text-text-muted mb-1">板块指数：收盘与 MA5 / MA10 / MA20（近 {bars.length} 个交易日）</div>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
            <XAxis dataKey="d" tick={AXIS_TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={28} />
            <YAxis tick={AXIS_TICK} axisLine={false} tickLine={false} width={56} domain={['auto', 'auto']}
                   tickFormatter={(v: number) => v.toFixed(0)} />
            <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={{ color: '#A2A9C4' }}
                     formatter={(v) => (typeof v === 'number' ? v.toFixed(2) : v)} />
            <Legend wrapperStyle={{ fontSize: 11 }} iconSize={8} />
            <Line dataKey="close" name="收盘" stroke="#EDF0F5" strokeWidth={1.6} dot={false} />
            <Line dataKey="ma5" name="MA5" stroke="#F59E0B" strokeWidth={1.2} dot={false} connectNulls />
            <Line dataKey="ma10" name="MA10" stroke="#B47CFF" strokeWidth={1.2} dot={false} connectNulls />
            <Line dataKey="ma20" name="MA20" stroke="#5EA6FF" strokeWidth={1.2} dot={false} connectNulls />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function EcologyChart({ eco }: { eco: SectorTrendDetail['ecology'] }) {
  const data = eco.map((e) => ({ ...e, d: e.date.slice(5) }))
  return (
    <div>
      <div className="text-[11px] text-text-muted mb-1">
        成分股生态：每日涨停、炸板只数与最高连板（近 {eco.length} 个交易日；空着的日子是库里没快照，不是 0）
      </div>
      <div className="h-40">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={data} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
            <XAxis dataKey="d" tick={AXIS_TICK} axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={28} />
            <YAxis yAxisId="n" tick={AXIS_TICK} axisLine={false} tickLine={false} width={28} allowDecimals={false} />
            <YAxis yAxisId="h" orientation="right" tick={AXIS_TICK} axisLine={false} tickLine={false} width={24}
                   allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} labelStyle={{ color: '#A2A9C4' }} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
            <Legend wrapperStyle={{ fontSize: 11 }} iconSize={8} />
            <Bar yAxisId="n" dataKey="lu" name="涨停" fill="#FF4560" maxBarSize={10} />
            <Bar yAxisId="n" dataKey="broken" name="炸板" fill="#737A96" maxBarSize={10} />
            <Line yAxisId="h" dataKey="height" name="最高板" stroke="#FFD700" strokeWidth={1.4} dot={{ r: 1.5 }} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function Detail({ item }: { item: SectorTrendItem }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['sector-trend-detail', item.code],
    queryFn: () => fetchSectorTrendDetail(item.code),
    staleTime: 60_000,
  })
  return (
    <div className="grid lg:grid-cols-5 gap-4 px-4 py-3 bg-bg-base/40">
      <div className="lg:col-span-2"><Why item={item} history={data?.history} /></div>
      <div className="lg:col-span-3 space-y-3">
        {isLoading && (
          <div className="text-xs text-text-muted flex items-center gap-1.5">
            <Loader2 className="w-3 h-3 animate-spin" />读取指数日线与成分股生态…
          </div>
        )}
        {isError && <div className="text-xs text-danger">读取失败：{(error as Error)?.message}</div>}
        {data && <PriceChart bars={data.bars} />}
        {data && <EcologyChart eco={data.ecology} />}
      </div>
    </div>
  )
}

// ─── 雷达表 ───────────────────────────────────────────────────────────────────

type SortKey = 'default' | 'r5' | 'rs10' | 'dev20' | 'lu_3d'
const SORT_VALUE: Record<Exclude<SortKey, 'default'>, (s: SectorTrendItem) => number | null> = {
  r5: (s) => s.facts.r5, rs10: (s) => s.facts.rs10, dev20: (s) => s.facts.dev20, lu_3d: (s) => s.facts.lu_3d,
}

function RadarTable({ rows, ecoDates, hot, extreme }: {
  rows: SectorTrendItem[]; ecoDates?: string[]; hot: number; extreme: number
}) {
  const [sort, setSort] = useState<SortKey>('default')
  const [open, setOpen] = useState<string | null>(null)
  const sorted = useMemo(() => {
    if (sort === 'default') return rows    // 后端已按 状态优先级 → RS10 → 5 日涨幅名次 → 近 3 日涨停 排好
    const val = SORT_VALUE[sort]
    return [...rows].sort((a, b) => (val(b) ?? -Infinity) - (val(a) ?? -Infinity))
  }, [rows, sort])

  const th = (label: string, key?: SortKey, right = true) => (
    <th onClick={key ? () => setSort(sort === key ? 'default' : key) : undefined}
        className={cn('px-3 py-2 text-xs font-medium whitespace-nowrap select-none',
                      right ? 'text-right' : 'text-left',
                      key && 'cursor-pointer hover:text-text-secondary',
                      key && sort === key ? 'text-accent' : 'text-text-secondary/70')}>
      {label}{key && sort === key && ' ↓'}
    </th>
  )

  return (
    <div className="card overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-bg-border">
            {th('板块', undefined, false)}
            {th('状态 · 近5天', undefined, false)}
            {th('四道闸', undefined, false)}
            {th('5日', 'r5')}
            {th('RS10 / RS20', 'rs10')}
            {th('偏离MA20', 'dev20')}
            {th('近6日涨停', 'lu_3d', false)}
            {th('最高板')}
            {th('封板率')}
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => {
            const f = s.facts
            const isOpen = open === s.code
            return (
              <Fragment key={s.code}>
                <tr onClick={() => setOpen(isOpen ? null : s.code)}
                    className={cn('border-t border-bg-border/40 cursor-pointer hover:bg-bg-elevated/60',
                                  isOpen && 'bg-bg-elevated/40')}>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <div className="flex items-center gap-1.5">
                      {isOpen ? <ChevronDown className="w-3.5 h-3.5 text-text-muted" />
                              : <ChevronRight className="w-3.5 h-3.5 text-text-muted" />}
                      <span className="text-text-primary">{s.name}</span>
                      <span className="text-[10px] text-text-muted font-mono">{s.code}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className="inline-flex items-center gap-2"><StateBadge state={s.state} /><Trail trail={s.trail} /></span>
                  </td>
                  <td className="px-3 py-2"><GatePills gates={s.gates} /></td>
                  <td className={cn('px-3 py-2 text-right font-mono text-xs', dirCls(f.r5))}>{fmtPct(f.r5)}</td>
                  <td className="px-3 py-2 text-right font-mono text-xs whitespace-nowrap">
                    <span className={dirCls(f.rs10)}>{fmt(f.rs10, 2)}</span>
                    <span className="text-text-muted"> / </span>
                    <span className={dirCls(f.rs20)}>{fmt(f.rs20, 2)}</span>
                    {f.rs10_rank != null && (
                      <div className="text-[10px] text-text-muted">排 {f.rs10_rank}/{f.rs10_n}</div>
                    )}
                  </td>
                  <td className={cn('px-3 py-2 text-right font-mono text-xs',
                                    f.dev20 == null ? 'text-text-muted'
                                      : f.dev20 >= extreme ? 'text-orange-300'
                                      : f.dev20 >= hot ? 'text-warn' : 'text-text-secondary')}>
                    {fmtPct(f.dev20)}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <LuSeries series={f.lu_series} dates={ecoDates} />
                    <span className="ml-1.5 text-[10px] text-text-muted">{LU_TREND[f.lu_trend]}</span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs text-text-secondary">{f.height_3d ?? '—'}</td>
                  <td className="px-3 py-2 text-right font-mono text-xs text-text-secondary">
                    {f.seal_rate == null ? '—' : `${Math.round(f.seal_rate * 100)}%`}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="border-t border-bg-border/40">
                    <td colSpan={9} className="p-0"><Detail item={s} /></td>
                  </tr>
                )}
              </Fragment>
            )
          })}
          {sorted.length === 0 && (
            <tr><td colSpan={9} className="px-3 py-6 text-center text-xs text-text-muted">没有板块</td></tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

// ─── 传统多周期排名（折叠，展开才挂载）──────────────────────────────────────────

function LegacyRanking() {
  const [open, setOpen] = useState(false)
  const [phase, setPhase] = useState<number | null>(null)
  return (
    <div className="card">
      <button type="button" onClick={() => setOpen((v) => !v)}
              className="w-full flex items-center gap-2 px-3 py-2 text-xs text-text-secondary hover:text-text-primary">
        {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        传统多周期排名（5 / 10 / 20 / 60 日涨幅打标 + 生命周期分布）
        <span className="ml-auto text-[11px] text-text-muted">展开才加载——要拉带成员股的全量板块列表</span>
      </button>
      {open && (
        <div className="px-3 pb-3 space-y-3">
          <div className="text-[11px] text-text-muted">
            生命周期阶段来自旧的板块阶段服务（按情绪分、风险分判定），上面的雷达不参考它，这里只留作对照。
          </div>
          <PhaseLifecycleBar selected={phase} onSelect={setPhase} />
          <SectorRanking fixedView="trend" phaseFilter={phase} />
        </div>
      )}
    </div>
  )
}

// ─── 页面 ─────────────────────────────────────────────────────────────────────

export default function SectorTrend() {
  const [filter, setFilter] = useState<MainlineState | null>(null)
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['sector-trend'],
    queryFn: fetchSectorTrend,
    staleTime: 60_000,
  })
  const rows = useMemo(
    () => (data?.sectors ?? []).filter((s) => !filter || s.state === filter),
    [data, filter],
  )

  return (
    <div className="space-y-3 animate-fade-in">
      {isLoading && (
        <div className="card p-4 text-xs text-text-muted flex items-center gap-1.5">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />正在给关注板块过四道闸…
        </div>
      )}
      {isError && <div className="card p-4 text-xs text-danger">读取失败：{(error as Error)?.message}</div>}
      {data && (
        <>
          <Summary data={data} />
          <StateCards data={data} filter={filter} onSelect={setFilter} />
          {filter && (
            <div className="text-xs text-text-secondary">
              只看「{STATE_META[filter].label}」{rows.length} 个 ·{' '}
              <button type="button" className="underline hover:text-text-primary" onClick={() => setFilter(null)}>看全部</button>
            </div>
          )}
          <RadarTable rows={rows} ecoDates={data.eco_dates}
                      hot={data.thresholds.hot_dev20 ?? 8} extreme={data.thresholds.extreme_dev20 ?? 12} />
        </>
      )}
      <LegacyRanking />
    </div>
  )
}
