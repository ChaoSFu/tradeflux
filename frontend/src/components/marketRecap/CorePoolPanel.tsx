import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, Star } from 'lucide-react'
import { addWatch, fetchActivations, fetchPopularCores, type CoreSource, type PopularCore } from '@/api/marketRecap'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { LIFECYCLE_ZH } from '@/lib/lifecycle'
import { cn } from '@/utils/cn'
import { Facts, NUM, StockLink, fmtPct, pctCls } from './shared'

export const EVENT_ZH: Record<string, string> = {
  limit_up: '今日涨停', relimit: '再次涨停', streak_continue: '连板延续', streak_break: '断板',
  repair_start: '修复启动', repair_confirm: '修复确认', repair_fail: '修复失败', volume_surge: '放量异动',
}
const EVENT_TONE: Record<string, string> = {
  limit_up: 'bg-up/15 text-up', relimit: 'bg-up/25 text-up', streak_continue: 'bg-up/15 text-up',
  streak_break: 'bg-down/15 text-down', repair_start: 'bg-accent/15 text-accent', repair_confirm: 'bg-accent/25 text-accent',
  repair_fail: 'bg-down/15 text-down', volume_surge: 'bg-warn/15 text-warn',
}
const VIEWS = [['active', '今日活跃'], ['historical', '历史人气'], ['repair', '修复观察'], ['mine', '我的重点'], ['all', '全部候选']] as const
type View = typeof VIEWS[number][0]
type SortKey = 'pct_change' | 'pct_change_5d' | 'pct_change_10d' | 'pct_change_20d' | 'pct_change_60d'
  | 'limit_up_days_10d' | 'limit_up_days_20d' | 'limit_up_days_60d' | 'current_board' | 'historical_max_board'
  | 'amount' | 'limit_up_next_avg_pct' | 'relations'
const RESTART_ZH: Record<string, string> = { repair_start: '修复启动', repair_confirm: '修复确认' }

/** 一条辨识度依据 → 一个客观标签。只写它实际满足的那条，不合成分数 */
function sourceTag(s: CoreSource, labels: Record<string, string>): string {
  const l = labels[s.source] ?? s.source
  if (s.source === 'turnover_repeat') return `成交额 5 日上榜 ${s.value} 次`
  if (s.source === 'lifecycle_restart') return RESTART_ZH[String(s.value)] ?? '生命周期重启'
  if (s.source === 'manual') return '我的观察'
  if (s.source.startsWith('lu_')) return `${l} ${s.value} 次·第${s.rank}`
  if (s.source === 'board') return `连板 ${s.value} 板·第${s.rank}`
  if (s.source === 'high_board') return `60日高板 ${s.value} 板·第${s.rank}`
  return `${l}第${s.rank}`
}

export function EventBadge({ t }: { t: string }) {
  return <span className={cn('px-1 rounded text-[10px] whitespace-nowrap', EVENT_TONE[t] ?? 'bg-bg-elevated text-text-secondary')}>{EVENT_ZH[t] ?? t}</span>
}

export function CorePoolPanel({ date, onResearch, onPoolChanged }: {
  date: string
  onResearch: (code: string, name: string | null) => void
  onPoolChanged: () => void
}) {
  const qc = useQueryClient()
  const cores = useQuery({ queryKey: ['recap-cores', date], queryFn: () => fetchPopularCores(date) })
  const acts = useQuery({ queryKey: ['recap-activations', date], queryFn: () => fetchActivations(date) })
  const [view, setView] = useState<View>('active')
  const [board, setBoard] = useState<'all' | '主板' | '创业板' | '科创板'>('all')
  const [evType, setEvType] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: 'pct_change_20d', dir: -1 })
  const [busy, setBusy] = useState<string | null>(null)

  const items = cores.data?.items ?? []
  const counts = useMemo(() => Object.fromEntries(VIEWS.map(([v]) => [v, items.filter((i) => i.views.includes(v)).length])), [items])
  const shown = useMemo(() => {
    const rows = items.filter((i) => i.views.includes(view) && (board === 'all' || i.board === board)
      && (!evType || i.events.includes(evType))
      && (!q || i.stock_code.includes(q) || (i.stock_name ?? '').includes(q) || (i.sector ?? '').includes(q)))
    const has = rows.filter((r) => r[sort.key] != null), none = rows.filter((r) => r[sort.key] == null)
    has.sort((a, b) => sort.dir * (((a[sort.key] as number) ?? 0) - ((b[sort.key] as number) ?? 0)))
    return [...has, ...none]
  }, [items, view, board, evType, q, sort])

  if (cores.isLoading) return <LoadingSpinner />
  if (cores.isError) return <div className="text-xs text-down">人气核心池读取失败：{(cores.error as Error)?.message}</div>
  const d = cores.data!
  const evCounts: Record<string, number> = {}
  for (const e of acts.data?.events ?? []) evCounts[e.event_type] = (evCounts[e.event_type] ?? 0) + 1
  const watch = async (c: PopularCore) => {
    setBusy(c.stock_code)
    try { await addWatch({ code: c.stock_code, date, name: c.stock_name }); qc.invalidateQueries({ queryKey: ['recap-cores', date] }); onPoolChanged() }
    finally { setBusy(null) }
  }
  const Th = ({ k, label, title }: { k: SortKey; label: string; title?: string }) => (
    <th className="px-2 py-1 text-right font-medium whitespace-nowrap" title={title}>
      <button onClick={() => setSort((s) => ({ key: k, dir: s.key === k ? (s.dir === -1 ? 1 : -1) : -1 }))}
              className={cn(sort.key === k ? 'text-accent' : 'hover:text-text-secondary')}>
        {label}{sort.key === k ? (sort.dir === -1 ? '↓' : '↑') : ''}
      </button>
    </th>
  )

  return (
    <>
      <Facts title={`召回来源（${d.frozen ? `已冻结 ${d.built_at?.replace('T', ' ').slice(5, 16) ?? ''}` : '现算，还没冻结'} · 规则 ${d.rule_version}）`}>
        <div className="flex flex-wrap gap-1">
          {Object.entries(d.sources).map(([k, m]) => (
            <span key={k} title={`${m.scope}${m.note ? `\n${m.note}` : ''}`}
                  className={cn('px-1.5 py-0.5 rounded text-[10px] border',
                    m.status === 'ok' ? 'border-bg-border text-text-secondary' : m.status === 'partial' ? 'border-warn/40 text-warn' : 'border-bg-border text-text-muted line-through')}>
              {d.source_labels[k] ?? k} {m.status === 'missing' ? '无数据' : m.n}
            </span>
          ))}
        </div>
        <div className="text-[11px] text-text-muted">
          区间涨幅是全市场排名（东财前复权）；涨停次数、连板、高板只在<b className="text-text-secondary">已跟踪股票</b>（进过候选池的）里排，不是全 A 股。
          并列同名次。没有人气分，每只只挂它实际满足的依据；悬停看各来源的统计范围。
          {!d.premium_is_current && ' 历史日期现算时不回填「涨停次日均」（股票表上只有当前值）。'}
        </div>
      </Facts>

      <Facts title={`今日人气核心事件（${acts.data?.events.length ?? 0}）——只提醒去看关联池，不是买入信号`}>
        <div className="flex flex-wrap items-center gap-1">
          <button onClick={() => setEvType(null)} className={cn('px-1.5 py-0.5 rounded text-[11px]', !evType ? 'bg-accent/15 text-accent' : 'text-text-muted')}>全部</button>
          {Object.entries(evCounts).map(([t, n]) => (
            <button key={t} onClick={() => { setEvType(evType === t ? null : t); setView('all') }}
                    className={cn('rounded', evType === t && 'ring-1 ring-accent')}>
              <EventBadge t={t} /> <span className={cn('text-[11px] text-text-muted', NUM)}>{n}</span>
            </button>
          ))}
          {acts.data?.not_supported.map((x) => <span key={x} className="text-[10px] text-text-muted ml-2">（{x}）</span>)}
        </div>
      </Facts>

      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded border border-bg-border overflow-hidden">
          {VIEWS.map(([v, l]) => (
            <button key={v} onClick={() => setView(v)}
                    className={cn('px-2 py-0.5 text-xs', view === v ? 'bg-accent/15 text-accent' : 'text-text-muted hover:text-text-secondary')}>
              {l} <span className={NUM}>{counts[v]}</span>
            </button>
          ))}
        </div>
        <div className="inline-flex rounded border border-bg-border overflow-hidden" title="涨跌停制度不同（主板 10%，创业板 / 科创板 20%），可以分开看">
          {(['all', '主板', '创业板', '科创板'] as const).map((b) => (
            <button key={b} onClick={() => setBoard(b)}
                    className={cn('px-2 py-0.5 text-xs', board === b ? 'bg-accent/15 text-accent' : 'text-text-muted')}>{b === 'all' ? '全部板块' : b}</button>
          ))}
        </div>
        <label className="flex items-center gap-1 text-xs text-text-muted ml-auto">
          <Search className="w-3 h-3" />
          <input value={q} onChange={(e) => setQ(e.target.value.trim())} placeholder="名称 / 代码 / 行业"
                 className="bg-bg-elevated border border-bg-border rounded px-1.5 py-0.5 w-36 text-text-primary" />
        </label>
      </div>

      <div className="overflow-auto max-h-[520px]">
        <table className="w-full text-xs [&_td]:whitespace-nowrap">
          <thead className="sticky top-0 bg-bg-card z-10">
            <tr className="text-text-muted border-b border-bg-border">
              <th className="px-2 py-1 text-left font-medium">股票</th>
              <th className="px-2 py-1 text-left font-medium" title="凭什么进池（历史辨识度）">辨识度依据</th>
              <th className="px-2 py-1 text-left font-medium" title="生命周期（当前结构）——跟辨识度依据分开看">当前状态</th>
              <th className="px-2 py-1 text-left font-medium">今日事件</th>
              <Th k="pct_change" label="今日" />
              <Th k="pct_change_5d" label="5D" />
              <Th k="pct_change_20d" label="20D" />
              <Th k="pct_change_60d" label="60D" />
              <Th k="limit_up_days_10d" label="10日涨停" />
              <Th k="limit_up_days_60d" label="60日涨停" />
              <Th k="current_board" label="连板" />
              <Th k="historical_max_board" label="60日高板" />
              <Th k="amount" label="成交额" />
              <Th k="limit_up_next_avg_pct" label="涨停次日均" title="有记录的涨停，次一交易日收盘涨跌幅的平均值（带样本数）。不是次日开盘可买到的收益；样本少时只有描述意义" />
              <Th k="relations" label="关联股" />
              <th className="px-2 py-1" />
            </tr>
          </thead>
          <tbody>
            {shown.map((i) => (
              <tr key={i.stock_code} className={cn('border-b border-bg-border/50 last:border-0 align-top', i.watched && 'bg-purple-400/5')}>
                <td className="px-2 py-1">
                  <StockLink code={i.stock_code} name={i.stock_name} />
                  <div className="text-[10px] text-text-muted">{i.board}{i.is_st ? ' · ST' : ''}{i.sector ? ` · ${i.sector}` : ''}{i.sector_basis?.startsWith('当前') ? '*' : ''}</div>
                </td>
                <td className="px-2 py-1 whitespace-normal max-w-[18rem]">
                  <div className="flex flex-wrap gap-0.5">
                    {i.selection_sources.map((s, k) => (
                      <span key={k} title={s.scope} className="px-1 rounded bg-bg-elevated text-[10px] text-text-secondary">{sourceTag(s, d.source_labels)}</span>
                    ))}
                  </div>
                </td>
                <td className="px-2 py-1 text-text-secondary">{i.lifecycle_state ? LIFECYCLE_ZH[i.lifecycle_state] ?? i.lifecycle_state : <span className="text-text-muted">—</span>}</td>
                <td className="px-2 py-1"><div className="flex flex-wrap gap-0.5">{i.events.map((t) => <EventBadge key={t} t={t} />)}</div></td>
                {(['pct_change', 'pct_change_5d', 'pct_change_20d', 'pct_change_60d'] as const).map((k) => (
                  <td key={k} className={cn('px-2 py-1 text-right', NUM, pctCls(i[k]))}
                      title={k !== 'pct_change' && i[k] != null ? `全市场第 ${i[k.replace('pct_change', 'rank') as 'rank_5d'] ?? '20 名外'}` : undefined}>
                    {fmtPct(i[k])}
                  </td>
                ))}
                <td className={cn('px-2 py-1 text-right', NUM)}>{i.limit_up_days_10d ?? '—'}</td>
                <td className={cn('px-2 py-1 text-right', NUM)}>{i.limit_up_days_60d ?? '—'}</td>
                <td className={cn('px-2 py-1 text-right', NUM, i.current_board ? 'text-up' : 'text-text-muted')}>{i.current_board == null ? '—' : i.current_board || '—'}</td>
                <td className={cn('px-2 py-1 text-right', NUM)}>{i.historical_max_board ?? '—'}</td>
                <td className={cn('px-2 py-1 text-right text-text-secondary', NUM)} title={i.turnover_rank ? `成交额第 ${i.turnover_rank}` : undefined}>
                  {i.amount != null ? `${i.amount.toFixed(1)}亿` : '—'}
                </td>
                <td className={cn('px-2 py-1 text-right', NUM, pctCls(i.limit_up_next_avg_pct))}>
                  {i.limit_up_next_avg_pct == null ? '—' : <>{fmtPct(i.limit_up_next_avg_pct)}<span className="text-[10px] text-text-muted ml-0.5">{i.limit_up_next_samples}次</span></>}
                </td>
                <td className={cn('px-2 py-1 text-right', NUM, i.relations ? 'text-text-primary' : 'text-text-muted')}>{i.relations}</td>
                <td className="px-2 py-1 text-right">
                  <div className="flex items-center justify-end gap-1.5">
                    {i.watched ? <span className="text-[10px] text-purple-300">{i.deep && <Star className="inline w-3 h-3 fill-purple-300" />}已观察</span>
                      : <button disabled={busy === i.stock_code} onClick={() => watch(i)} className="inline-flex items-center text-[11px] text-accent"><Plus className="w-3 h-3" />观察</button>}
                    <button onClick={() => onResearch(i.stock_code, i.stock_name)} className="text-[11px] text-purple-300 hover:underline">关联研究</button>
                  </div>
                </td>
              </tr>
            ))}
            {!shown.length && <tr><td colSpan={16} className="px-2 py-6 text-center text-text-muted">这个视角下没有股票</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="text-[11px] text-text-muted">
        观察视角不互斥，也不是收益等级。「当前状态」和「辨识度依据」分开：60 日涨幅第一但已经走弱的票照样在「历史人气」里。用户加进观察池的票永远在「我的重点」，不受榜单淘汰。
      </div>
    </>
  )
}
