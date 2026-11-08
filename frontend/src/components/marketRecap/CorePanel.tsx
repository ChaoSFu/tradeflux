import { useMemo, useState } from 'react'
import { Plus, Star, X, ShieldAlert } from 'lucide-react'
import { addWatch, removeWatch, type Candidate, type RecapContextOk, type RecapDraft, type SectorTier, type WatchItem } from '@/api/marketRecap'
import { LIFECYCLE_ZH } from '@/lib/lifecycle'
import { cn } from '@/utils/cn'
import { Facts, GROUP_LABEL, INPUT, Manual, NUM, SECTOR_STATE_ZH, StockLink, TIER_LABEL, fmtPct, pctCls } from './shared'

const TIERS: (SectorTier | null)[] = ['core', 'secondary', 'drop', null]

function TierPicker({ value, onChange, disabled }: { value: SectorTier | null; onChange: (t: SectorTier | null) => void; disabled: boolean }) {
  return (
    <div className="inline-flex rounded border border-bg-border overflow-hidden">
      {TIERS.map((t) => (
        <button key={t ?? 'none'} disabled={disabled} onClick={() => onChange(t)}
                className={cn('px-1.5 py-0.5 text-[10px]',
                  value === t ? (t === 'core' ? 'bg-purple-400/20 text-purple-200' : t === 'drop' ? 'bg-bg-elevated text-text-muted' : t ? 'bg-purple-400/10 text-purple-200' : 'bg-bg-elevated text-text-secondary')
                    : 'text-text-muted hover:text-text-secondary')}>
          {t ? TIER_LABEL[t].replace('不再重点关注', '不再关注') : '未归类'}
        </button>
      ))}
    </div>
  )
}

export function CorePanel({ ctx, draft, set, pool, date, readOnly, onPoolChanged }: {
  ctx: RecapContextOk
  draft: RecapDraft
  set: (patch: Partial<RecapDraft>) => void
  pool: WatchItem[]
  date: string
  readOnly: boolean
  onPoolChanged: () => void
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [err, setErr] = useState('')
  const [manualCode, setManualCode] = useState('')
  const tierOf = new Map(draft.sector_picks.map((p) => [p.code, p.tier]))
  const inPool = new Set(pool.map((w) => w.code))
  const sectors = ctx.sectors.items ?? []
  const cands = useMemo(() => ctx.candidates.items ?? [], [ctx])

  const setTier = (code: string, name: string, label: string, t: SectorTier | null) => {
    const rest = draft.sector_picks.filter((p) => p.code !== code)
    set({ sector_picks: t ? [...rest, { code, name, tier: t, state_label: label }] : rest })
  }
  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key); setErr('')
    try { await fn(); onPoolChanged() } catch (e: any) { setErr(e?.message ?? '操作失败') }  // eslint-disable-line @typescript-eslint/no-explicit-any
    finally { setBusy(null) }
  }
  const add = (c: { code: string; name: string | null }) => run(c.code, () => addWatch({ code: c.code, date, name: c.name }))

  return (
    <>
      <Facts title={`主线板块（板块趋势的状态，基准日 ${ctx.sectors.state_date ?? '—'}）`} meta={ctx.sectors.meta}>
        {!sectors.length ? <div className="text-xs text-text-muted">这天没有可用的板块状态</div> : (
          <div className="overflow-x-auto max-h-[320px]">
            <table className="w-full text-xs [&_td]:whitespace-nowrap">
              <thead className="sticky top-0 bg-bg-card">
                <tr className="text-text-muted border-b border-bg-border">
                  {['板块', '状态', '今日', '5日', '20日相对强度', '涨停', '最高板', '涨停趋势', '近5日状态', '我的归类'].map((h, i) => (
                    <th key={h} className={cn('px-2 py-1 font-medium', i < 2 || i > 6 ? 'text-left' : 'text-right')}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sectors.map((s) => (
                  <tr key={s.code} className="border-b border-bg-border/50 last:border-0">
                    <td className="px-2 py-1 text-text-primary">{s.name}</td>
                    <td className="px-2 py-1" title={s.reason}>{s.state_label}</td>
                    <td className={cn('px-2 py-1 text-right', NUM, pctCls(s.pct))}>{fmtPct(s.pct)}</td>
                    <td className={cn('px-2 py-1 text-right', NUM, pctCls(s.r5))}>{fmtPct(s.r5)}</td>
                    <td className={cn('px-2 py-1 text-right', NUM, pctCls(s.rs20))}>{s.rs20 == null ? '—' : s.rs20.toFixed(2)}</td>
                    <td className={cn('px-2 py-1 text-right', NUM)}>{s.lu ?? '—'}</td>
                    <td className={cn('px-2 py-1 text-right', NUM)}>{s.height ?? '—'}</td>
                    <td className="px-2 py-1 text-text-secondary">{s.lu_trend ?? '—'}</td>
                    <td className="px-2 py-1 text-[10px] text-text-muted" title={s.trail.map((t) => `${t.date} ${SECTOR_STATE_ZH[t.state] ?? t.state}`).join('\n')}>
                      {s.trail.map((t) => (SECTOR_STATE_ZH[t.state] ?? t.state).slice(0, 1)).join(' → ')}
                    </td>
                    <td className="px-2 py-1">
                      <TierPicker value={tierOf.get(s.code) ?? null} disabled={readOnly}
                                  onChange={(t) => setTier(s.code, s.name, s.state_label, t)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="text-[11px] text-text-muted">归类是你的复盘判断，不改板块趋势的事实；建议重点跟踪 3~5 个。</div>
      </Facts>

      <Facts title="核心股票候选（连板 / N天M板 / 生命周期 / 成交额前列，各组按上限取，不打分）" meta={ctx.candidates.meta}>
        <div className="overflow-x-auto max-h-[420px]">
          <table className="w-full text-xs [&_td]:whitespace-nowrap">
            <thead className="sticky top-0 bg-bg-card">
              <tr className="text-text-muted border-b border-bg-border">
                {['股票', '来源', '今日', '连续连板', 'N天M板', '10日涨停', '20日涨幅', '生命周期', '成交额排名', '板块', '监管', ''].map((h, i) => (
                  <th key={h + i} className={cn('px-2 py-1 font-medium', [0, 1, 7, 9, 10].includes(i) ? 'text-left' : 'text-right')}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {cands.map((c: Candidate) => (
                <tr key={c.code} className={cn('border-b border-bg-border/50 last:border-0', inPool.has(c.code) && 'bg-purple-400/5')}>
                  <td className="px-2 py-1"><StockLink code={c.code} name={c.name} hasDetail={c.has_snapshot} /></td>
                  <td className="px-2 py-1">
                    {c.groups.map((g) => <span key={g} className="mr-1 px-1 rounded bg-bg-elevated text-[10px] text-text-secondary">{GROUP_LABEL[g]}</span>)}
                    {!c.groups.length && c.watched && <span className="text-[10px] text-purple-300">观察池</span>}
                  </td>
                  <td className={cn('px-2 py-1 text-right', NUM, pctCls(c.pct))}>{fmtPct(c.pct)}</td>
                  <td className={cn('px-2 py-1 text-right', NUM, c.is_limit_up ? 'text-up' : 'text-text-secondary')}
                      title={c.first_limit_time ? `首封 ${c.first_limit_time}` : undefined}>
                    {c.is_limit_up && c.board_count ? `${c.board_count}板` : '—'}
                  </td>
                  <td className={cn('px-2 py-1 text-right text-text-secondary', NUM)}>{c.limit_stat ?? '—'}</td>
                  <td className={cn('px-2 py-1 text-right text-text-secondary', NUM)}>{c.limit_up_days_10d ?? '—'}</td>
                  <td className={cn('px-2 py-1 text-right', NUM, pctCls(c.pct_20d))}>{fmtPct(c.pct_20d)}</td>
                  <td className="px-2 py-1 text-text-secondary"
                      title={c.peak_board_count ? `本轮最高 ${c.peak_board_count} 板` : undefined}>
                    {c.lifecycle_state ? LIFECYCLE_ZH[c.lifecycle_state] ?? c.lifecycle_state : '—'}
                  </td>
                  <td className={cn('px-2 py-1 text-right text-text-secondary', NUM)}>{c.turnover_rank ?? '—'}</td>
                  <td className="px-2 py-1 text-text-secondary max-w-[8rem] truncate" title={c.sector_basis ? `${c.sector}（${c.sector_basis}）` : undefined}>
                    {c.sector ?? '—'}{c.sector_basis?.startsWith('当前') && <span className="text-warn">*</span>}
                  </td>
                  <td className="px-2 py-1">
                    {c.regulatory && (
                      <span className="inline-flex items-center gap-0.5 text-warn text-[10px]" title={`${c.regulatory.reason ?? ''} 至 ${c.regulatory.until ?? '—'}`}>
                        <ShieldAlert className="w-3 h-3" />{c.regulatory.status}
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1 text-right">
                    {inPool.has(c.code) ? <span className="text-[10px] text-purple-300">已在观察</span> : !readOnly && (
                      <button disabled={busy === c.code} onClick={() => add(c)}
                              className="inline-flex items-center gap-0.5 text-[11px] text-accent hover:underline">
                        <Plus className="w-3 h-3" />观察
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="text-[11px] text-text-muted">连续连板和 N天M板分开显示；板块带 <span className="text-warn">*</span> 的是股票表上的当前主板块，不是当天口径。</div>
      </Facts>

      <Manual title={`我的重点观察池（${pool.length}）`} hint="跨日保留，直到你取消；★ 是要建深入关联图谱的 3~5 只">
        {err && <div className="text-xs text-down">{err}</div>}
        {!pool.length ? <div className="text-xs text-text-muted">还没有观察股：从上面候选里点「观察」，或者直接输代码。</div> : (
          <div className="flex flex-wrap gap-1.5">
            {pool.map((w) => (
              <div key={w.code} className={cn('flex items-center gap-1.5 px-2 py-1 rounded border text-xs',
                w.deep ? 'border-purple-400/60 bg-purple-400/10' : 'border-bg-border')}>
                <button disabled={readOnly || busy === w.code} title={w.deep ? '取消深入研究' : '标为深入研究'}
                        onClick={() => run(w.code, () => addWatch({ code: w.code, date, deep: !w.deep }))}>
                  <Star className={cn('w-3.5 h-3.5', w.deep ? 'fill-purple-300 text-purple-300' : 'text-text-muted')} />
                </button>
                <StockLink code={w.code} name={w.name} hasDetail={w.facts?.has_detail ?? true} />
                <span className={cn(NUM, pctCls(w.facts?.pct))}>{fmtPct(w.facts?.pct)}</span>
                {w.facts?.is_limit_up && <span className="text-up text-[10px]">涨停{w.facts.board_count ? `·${w.facts.board_count}板` : ''}</span>}
                <span className="text-[10px] text-text-muted" title="关联关系条数">关联 {w.relations}</span>
                <span className="text-[10px] text-text-muted" title="加入观察的日期">{w.added_date.slice(5)}起</span>
                {!readOnly && (
                  <button disabled={busy === w.code} title="移出观察池（之前那些天的记录保留）"
                          onClick={() => run(w.code, () => removeWatch(w.code, date))}
                          className="text-text-muted hover:text-down"><X className="w-3 h-3" /></button>
                )}
              </div>
            ))}
          </div>
        )}
        {!readOnly && (
          <form className="flex items-center gap-1.5" onSubmit={(e) => {
            e.preventDefault()
            if (/^\d{6}$/.test(manualCode)) { add({ code: manualCode, name: null }); setManualCode('') }
          }}>
            <input value={manualCode} onChange={(e) => setManualCode(e.target.value.trim())} placeholder="输 6 位代码加入观察"
                   className={cn(INPUT, 'w-44')} />
            <button className="px-2 py-1 rounded bg-purple-400/15 text-purple-200 text-xs">加入</button>
          </form>
        )}
      </Manual>
    </>
  )
}
