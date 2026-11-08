import { useMemo } from 'react'
import { Plus, X } from 'lucide-react'
import type { Evidence, Posture, RecapContextOk, RecapDraft } from '@/api/marketRecap'
import { cn } from '@/utils/cn'
import {
  DIR_LABEL, Facts, INPUT, Manual, NUM, POSTURE_LABEL, TextArea, fmtPct, fmtRatio, fmtYi, pctCls,
} from './shared'

const INVALIDATION_TEMPLATES = [
  '上证指数收盘跌破 MA20', '核心主线板块同时转弱', '连板晋级率明显恶化', '昨日强势股出现大面积负反馈',
]

/** 从事实里拼出可勾选的依据。勾选时把当时的值一起存下来，事后数据变了也不影响 */
function evidenceOptions(c: RecapContextOk): Evidence[] {
  const out: Evidence[] = []
  for (const i of c.indices.items ?? []) {
    if (i.status === 'missing') continue
    out.push({ key: `index.${i.code}`, label: i.name,
               value: `${fmtPct(i.pct)} · ${i.arrangement ?? '均线不全'} · MA20${i.ma20_dir ? DIR_LABEL[i.ma20_dir] : '?'}` })
  }
  const b = c.breadth
  if (b.up_count != null && b.down_count != null) out.push({ key: 'breadth.updown', label: '涨跌家数', value: `${b.up_count} / ${b.down_count}` })
  if (b.limit_up_count != null) out.push({ key: 'breadth.limit', label: '涨停 / 跌停', value: `${b.limit_up_count} / ${b.limit_down_count ?? '—'}` })
  if (b.seal_rate != null) out.push({ key: 'breadth.seal', label: '封板率', value: fmtRatio(b.seal_rate) })
  const a = c.amount
  if (a.amount != null) out.push({ key: 'amount', label: '两市成交额', value: `${fmtYi(a.amount)}（较前日 ${fmtPct(a.change_pct)}）` })
  for (const co of c.feedback.cohorts ?? []) {
    if (!co.valid_count) continue
    out.push({ key: `feedback.${co.cohort_type}`, label: `${co.label}今日`,
               value: `中位 ${fmtPct(co.median_pct_change)}${co.advance_ratio != null ? ` · 晋级 ${fmtRatio(co.advance_ratio)}` : ''}（n=${co.valid_count}）` })
  }
  if (c.feedback.max_board != null) out.push({ key: 'max_board', label: '最高连板', value: `${c.feedback.max_board} 板` })
  return out
}

function Tile({ label, value, sub, cls }: { label: string; value: React.ReactNode; sub?: React.ReactNode; cls?: string }) {
  return (
    <div className="card-elevated px-2.5 py-1.5 min-w-0">
      <div className="text-[10px] text-text-muted whitespace-nowrap">{label}</div>
      <div className={cn('text-sm font-semibold', NUM, cls ?? 'text-text-primary')}>{value}</div>
      {sub && <div className="text-[10px] text-text-muted truncate">{sub}</div>}
    </div>
  )
}

export function MarketPanel({ ctx, draft, set, readOnly }: {
  ctx: RecapContextOk
  draft: RecapDraft
  set: (patch: Partial<RecapDraft>) => void
  readOnly: boolean
}) {
  const options = useMemo(() => evidenceOptions(ctx), [ctx])
  const chosen = new Set(draft.market_evidence.map((e) => e.key))
  const toggle = (e: Evidence) => set({
    market_evidence: chosen.has(e.key) ? draft.market_evidence.filter((x) => x.key !== e.key) : [...draft.market_evidence, e],
  })
  const b = ctx.breadth, a = ctx.amount, f = ctx.feedback

  return (
    <>
      <Facts title="指数趋势（均线与价格事实，不打分）" meta={ctx.indices.meta}>
        <div className="overflow-x-auto">
          <table className="w-full text-xs [&_td]:whitespace-nowrap">
            <thead>
              <tr className="text-text-muted border-b border-bg-border">
                {['指数', '收盘', '今日', 'MA5', 'MA10', 'MA20', 'MA60', '均线排列', 'MA20 / MA60 方向', '5日', '20日'].map((h) => (
                  <th key={h} className={cn('px-2 py-1 font-medium', h === '指数' ? 'text-left' : 'text-right')}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ctx.indices.items.map((i) => i.status === 'missing' ? (
                <tr key={i.code} className="border-b border-bg-border/50"><td className="px-2 py-1">{i.name}</td>
                  <td colSpan={10} className="px-2 py-1 text-text-muted">这天没有日线</td></tr>
              ) : (
                <tr key={i.code} className="border-b border-bg-border/50 last:border-0">
                  <td className="px-2 py-1 text-text-primary">{i.name}</td>
                  <td className={cn('px-2 py-1 text-right', NUM)}>{i.close?.toFixed(2)}</td>
                  <td className={cn('px-2 py-1 text-right', NUM, pctCls(i.pct))}>{fmtPct(i.pct)}</td>
                  {(['ma5', 'ma10', 'ma20', 'ma60'] as const).map((k) => (
                    <td key={k} className={cn('px-2 py-1 text-right text-text-secondary', NUM)}>{i[k]?.toFixed(2) ?? '—'}</td>
                  ))}
                  <td className={cn('px-2 py-1 text-right', i.arrangement === '多头' ? 'text-up' : i.arrangement === '空头' ? 'text-down' : 'text-text-secondary')}>
                    {i.arrangement ?? '—'}
                  </td>
                  <td className="px-2 py-1 text-right text-text-secondary">
                    {i.ma20_dir ? DIR_LABEL[i.ma20_dir] : '—'} / {i.ma60_dir ? DIR_LABEL[i.ma60_dir] : '—'}
                    <span className="ml-1 text-[10px] text-text-muted">
                      {i.above_ma20 == null ? '' : i.above_ma20 ? '站上MA20' : '在MA20下'}
                    </span>
                  </td>
                  <td className={cn('px-2 py-1 text-right', NUM, pctCls(i.r5))}>{fmtPct(i.r5)}</td>
                  <td className={cn('px-2 py-1 text-right', NUM, pctCls(i.r20))}>{fmtPct(i.r20)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Facts>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Facts title="涨跌广度" meta={b.meta}>
          <div className="grid grid-cols-3 gap-1.5">
            <Tile label="上涨 / 平 / 下跌" value={b.up_count == null ? '—' : <><span className="text-up">{b.up_count}</span><span className="text-text-muted"> / {b.flat_count ?? '—'} / </span><span className="text-down">{b.down_count}</span></>} />
            <Tile label="涨停 / 跌停" value={b.limit_up_count == null ? '—' : <><span className="text-up">{b.limit_up_count}</span><span className="text-text-muted"> / </span><span className="text-down">{b.limit_down_count}</span></>} />
            <Tile label="炸板 · 封板率" value={b.broken_count == null ? '—' : `${b.broken_count} · ${fmtRatio(b.seal_rate)}`} />
          </div>
        </Facts>
        <Facts title="市场成交" meta={a.meta}>
          <div className="grid grid-cols-3 gap-1.5">
            <Tile label={a.basis === 'intraday_last' ? '两市成交额（盘中快照）' : '两市成交额'} value={fmtYi(a.amount)} />
            <Tile label="较前一交易日" value={fmtPct(a.change_pct)} cls={pctCls(a.change_pct)} sub={a.prev_amount != null ? `前日 ${fmtYi(a.prev_amount)}` : undefined} />
            <Tile label={`较近 ${a.avg60_n ?? 0} 日均额`} value={fmtPct(a.vs_avg60_pct)} cls={pctCls(a.vs_avg60_pct)} sub={a.avg60 != null ? `均额 ${fmtYi(a.avg60)}` : undefined} />
          </div>
        </Facts>
        <Facts title={`短线接力（昨日 ${f.prev_date ?? '—'} 群体今日表现）`} meta={f.meta}>
          <div className="grid grid-cols-2 gap-1.5">
            {(f.cohorts ?? []).map((co) => (
              <Tile key={co.cohort_type} label={`${co.label}（n=${co.valid_count}）`}
                    value={co.valid_count ? fmtPct(co.median_pct_change) : '无样本'} cls={pctCls(co.median_pct_change)}
                    sub={co.advance_ratio != null ? `晋级 ${fmtRatio(co.advance_ratio)} · 红盘 ${fmtRatio(co.red_ratio)}` : co.valid_count ? `红盘 ${fmtRatio(co.red_ratio)}` : undefined} />
            ))}
          </div>
        </Facts>
      </div>

      <Facts title="连板梯队与晋级" meta={f.meta}>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs">
          <span>最高 <b className={cn('text-up', NUM)}>{f.max_board ?? '—'}</b> 板</span>
          {f.board_distribution && Object.entries(f.board_distribution).map(([k, v]) => (
            <span key={k} className="text-text-secondary">{k === '1' ? '首板' : `${k}板`} <b className={cn('text-text-primary', NUM)}>{v}</b></span>
          ))}
          <span className="text-text-muted">|</span>
          {(f.ladder ?? []).map((r) => (
            <span key={r.from_board} className="text-text-secondary" title={`昨日 ${r.from_board} 板 ${r.observed_count} 只（今天有行情的），晋级 ${r.advanced_count} 只`}>
              {r.from_board}→{r.to_board} <b className={NUM}>{fmtRatio(r.advance_ratio)}</b>
              <span className="text-text-muted">（{r.advanced_count}/{r.observed_count}）</span>
            </span>
          ))}
          {!(f.ladder ?? []).length && <span className="text-text-muted">晋级率无样本</span>}
        </div>
      </Facts>

      <Manual title="次日交易姿态" hint="这是你的判断，不是系统算出来的市场结论">
        <div className="flex flex-wrap gap-1.5">
          {(Object.keys(POSTURE_LABEL) as Posture[]).map((p) => (
            <button key={p} disabled={readOnly} onClick={() => set({ market_posture: draft.market_posture === p ? null : p })}
                    className={cn('px-3 py-1 rounded border text-xs',
                      draft.market_posture === p ? 'border-purple-400 bg-purple-400/15 text-purple-200'
                        : 'border-bg-border text-text-secondary hover:border-purple-400/50')}>
              {POSTURE_LABEL[p]}
            </button>
          ))}
        </div>
        <div className="text-[11px] text-text-muted">主要依据（点选数据，会连同当时的数值一起存下来）</div>
        <div className="flex flex-wrap gap-1.5">
          {options.map((e) => (
            <button key={e.key} disabled={readOnly} onClick={() => toggle(e)}
                    className={cn('px-2 py-0.5 rounded border text-[11px]',
                      chosen.has(e.key) ? 'border-purple-400/70 bg-purple-400/10 text-text-primary' : 'border-bg-border text-text-muted hover:text-text-secondary')}>
              {e.label} <span className={NUM}>{e.value}</span>
            </button>
          ))}
          {!options.length && <span className="text-[11px] text-text-muted">这天没有可选的数据依据</span>}
        </div>
        <TextArea value={draft.evidence_note} onChange={(v) => set({ evidence_note: v })} disabled={readOnly}
                  placeholder="补充依据（板块集中度、高标反馈、你看到的盘面细节……）" />
        <div className="text-[11px] text-text-muted">反证条件（出现就说明判断错了；次日拿来对照）</div>
        <div className="space-y-1">
          {draft.invalidation.map((c, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <input value={c} disabled={readOnly} className={INPUT}
                     onChange={(e) => set({ invalidation: draft.invalidation.map((x, j) => (j === i ? e.target.value : x)) })} />
              {!readOnly && (
                <button onClick={() => set({ invalidation: draft.invalidation.filter((_, j) => j !== i) })}
                        className="text-text-muted hover:text-down"><X className="w-3.5 h-3.5" /></button>
              )}
            </div>
          ))}
          {!readOnly && (
            <div className="flex flex-wrap items-center gap-1.5">
              <button onClick={() => set({ invalidation: [...draft.invalidation, ''] })}
                      className="inline-flex items-center gap-0.5 text-[11px] text-accent"><Plus className="w-3 h-3" />自己写一条</button>
              {INVALIDATION_TEMPLATES.filter((t) => !draft.invalidation.includes(t)).map((t) => (
                <button key={t} onClick={() => set({ invalidation: [...draft.invalidation, t] })}
                        className="px-1.5 py-0.5 rounded bg-bg-elevated text-[11px] text-text-muted hover:text-text-secondary">+ {t}</button>
              ))}
            </div>
          )}
        </div>
      </Manual>
    </>
  )
}
