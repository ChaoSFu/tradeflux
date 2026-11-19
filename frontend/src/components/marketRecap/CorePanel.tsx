import { useState } from 'react'
import { Star, X } from 'lucide-react'
import { addWatch, removeWatch, type RecapContextOk, type RecapDraft, type SectorFact, type SectorPick, type SectorTier, type WatchItem } from '@/api/marketRecap'
import { cn } from '@/utils/cn'
import { Facts, INPUT, Manual, NUM, SECTOR_STATE_ZH, StockLink, TIER_LABEL, fmtPct, pctCls } from './shared'

const TIERS: (SectorTier | null)[] = ['core', 'secondary', 'drop', null]

/**
 * 手动把一个关注板块加进「主线板块」列表（高标关联研究的板块卡片、顶部状态条的板块都走这里）。
 * 板块趋势已经列出来的不重复加；加进来的先不归类，你再点归类。返回新的归类列表，或者为什么没加。
 */
export function addSectorPick(picks: SectorPick[], listed: SectorFact[], s: { code: string; name: string; state_label?: string | null }):
  { picks: SectorPick[] | null; msg: string } {
  if (listed.some((x) => x.code === s.code)) return { picks: null, msg: `「${s.name}」已经在主线板块列表里` }
  if (picks.some((p) => p.code === s.code)) return { picks: null, msg: `「${s.name}」已经加过了` }
  return { picks: [...picks, { code: s.code, name: s.name, tier: null, state_label: s.state_label ?? null, manual: true }],
           msg: `「${s.name}」已加进主线板块（草稿，记得保存）` }
}

function TierPicker({ value, onChange, disabled }: { value: SectorTier | null; onChange: (t: SectorTier | null) => void; disabled: boolean }) {
  return (
    <div className="inline-flex rounded border border-bg-border overflow-hidden">
      {TIERS.map((t) => (
        // 再点一次已选中的归类 = 取消（回到未归类）
        <button key={t ?? 'none'} disabled={disabled} onClick={() => onChange(t && value === t ? null : t)}
                title={t && value === t ? '再点一次取消' : undefined}
                className={cn('px-1.5 py-0.5 text-[10px]',
                  value === t ? (t === 'core' ? 'bg-purple-400/20 text-purple-200' : t === 'drop' ? 'bg-warn/15 text-warn' : t ? 'bg-purple-400/10 text-purple-200' : 'bg-bg-elevated text-text-secondary')
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
  const listed = ctx.sectors.items ?? []
  const listedCodes = new Set(listed.map((s) => s.code))
  const factOf = new Map((ctx.sectors.others ?? []).map((s) => [s.code, s]))
  // 手动加的 + 之前归过类、现在板块趋势不再列出的：排在最前面，带「手动加入」
  const extra = draft.sector_picks.filter((p) => !listedCodes.has(p.code))
  const rows: { s: SectorFact; pick?: SectorPick }[] = [
    ...extra.map((p) => ({ pick: p, s: factOf.get(p.code) ?? { code: p.code, name: p.name, state: 'NONE', state_label: '无状态', reason: '这天没有它的板块趋势数据', trail: [], pct: null, r5: null, rs20: null, lu: null, height: null, lu_trend: null } })),
    ...listed.map((s) => ({ s })),
  ]

  const setTier = (code: string, name: string, label: string, t: SectorTier | null) => {
    const prev = draft.sector_picks.find((p) => p.code === code)
    const next = prev ? draft.sector_picks.map((p) => (p.code === code ? { ...p, tier: t, state_label: label } : p))
      : [...draft.sector_picks, { code, name, tier: t, state_label: label }]
    // 不是手动加的，不归类就不存；手动加的留在列表里，点 × 才拿掉
    set({ sector_picks: next.filter((p) => p.tier || p.manual) })
  }
  const dropManual = (code: string) => set({ sector_picks: draft.sector_picks.filter((p) => p.code !== code) })
  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key); setErr('')
    try { await fn(); onPoolChanged() } catch (e: any) { setErr(e?.message ?? '操作失败') }  // eslint-disable-line @typescript-eslint/no-explicit-any
    finally { setBusy(null) }
  }
  const add = (c: { code: string; name: string | null }) => run(c.code, () => addWatch({ code: c.code, date, name: c.name }))

  return (
    <>
      <Facts title={`主线板块（板块趋势的状态，基准日 ${ctx.sectors.state_date ?? '—'}）`} meta={ctx.sectors.meta}>
        {!rows.length ? <div className="text-xs text-text-muted">这天没有可用的板块状态</div> : (
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
                {rows.map(({ s, pick }) => (
                  <tr key={s.code} className={cn('border-b border-bg-border/50 last:border-0', tierOf.get(s.code) === 'drop' && 'opacity-50')}>
                    <td className="px-2 py-1 text-text-primary">
                      {s.name}
                      {pick && !listedCodes.has(s.code) && (
                        <span className="ml-1 text-[10px] px-1 rounded bg-accent/10 text-accent" title="板块趋势这天没把它列进主线，是你手动加进来的">手动加入</span>
                      )}
                    </td>
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
                      <div className="flex items-center gap-1">
                        <TierPicker value={tierOf.get(s.code) ?? null} disabled={readOnly}
                                    onChange={(t) => setTier(s.code, s.name, s.state_label, t)} />
                        {pick?.manual && !readOnly && (
                          <button onClick={() => dropManual(s.code)} title="从主线板块列表拿掉（手动加的）"
                                  className="text-text-muted hover:text-down"><X className="w-3 h-3" /></button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="text-[11px] text-text-muted">
          归类是你的复盘判断，不改板块趋势的事实；建议重点跟踪 3~5 个。再点一次已选的归类就是取消。
          高标关联研究的板块卡片、顶部状态条点开的板块，都可以「加入主线板块」。
        </div>
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
