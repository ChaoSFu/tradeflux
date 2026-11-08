import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Lightbulb, Plus, Trash2 } from 'lucide-react'
import {
  deleteRelation, fetchLeaderRelations, putRelation, putResponse,
  type AttributeType, type Relation, type ResponseStatus, type Suggestion, type WatchItem,
} from '@/api/marketRecap'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import { ATTR_LABEL, BASIS_LABEL, BASIS_OF, INPUT, Manual, NUM, RESPONSE_LABEL, StockLink, fmtPct, pctCls } from './shared'

const ATTR_ORDER: AttributeType[] = ['industry', 'event', 'concept', 'history', 'structure', 'name']
const TIMED: ResponseStatus[] = ['leading', 'sync', 'lagging']
const SOURCE_LABEL: Record<string, string> = {
  manual: '人工', name_rule: '名称规则', sector_relation: '板块关联', snapshot_concept: '行情概念',
}

function RelationRow({ r, date, leaderTime, onChanged, readOnly }: {
  r: Relation
  date: string
  leaderTime: string | null | undefined
  onChanged: () => void
  readOnly: boolean
}) {
  const [err, setErr] = useState('')
  const f = r.facts
  const timedOk = !!leaderTime && !!f?.first_limit_time
  const act = async (fn: () => Promise<unknown>) => {
    setErr('')
    try { await fn(); onChanged() } catch (e: any) { setErr(e?.message ?? '保存失败') }  // eslint-disable-line @typescript-eslint/no-explicit-any
  }
  return (
    <tr className="border-b border-bg-border/50 last:border-0 align-top">
      <td className="px-2 py-1 whitespace-nowrap"><StockLink code={r.related_code} name={r.related_name} hasDetail={f?.has_detail ?? true} /></td>
      <td className="px-2 py-1 text-text-primary whitespace-nowrap">{r.attribute_value}</td>
      <td className="px-2 py-1 whitespace-nowrap" title={BASIS_LABEL[r.relation_basis]}>
        <span className={cn('px-1 rounded text-[10px]', r.relation_basis === 'A' ? 'bg-accent/15 text-accent'
          : r.relation_basis === 'B' ? 'bg-bg-elevated text-text-secondary' : 'bg-warn/10 text-warn')}>{r.relation_basis}</span>
      </td>
      <td className="px-2 py-1 text-[11px] text-text-muted max-w-[16rem]" title={r.evidence ?? undefined}>
        <div className="truncate">{r.evidence ?? '—'}</div>
        <div className="text-[10px]">{SOURCE_LABEL[r.source] ?? r.source} · {r.first_recorded.slice(5)} 起</div>
      </td>
      <td className={cn('px-2 py-1 text-right', NUM, pctCls(f?.pct))}>{f?.status === 'missing' ? '无数据' : fmtPct(f?.pct)}</td>
      <td className={cn('px-2 py-1 text-right', NUM, pctCls(f?.pct_5d))}>{fmtPct(f?.pct_5d)}</td>
      <td className={cn('px-2 py-1 text-right', NUM, pctCls(f?.pct_20d))}>{fmtPct(f?.pct_20d)}</td>
      <td className={cn('px-2 py-1 text-right whitespace-nowrap', NUM)}>
        {f?.is_limit_up == null ? <span className="text-text-muted">?</span>
          : f.is_limit_up ? <span className="text-up">{f.board_count ? `${f.board_count}板` : '涨停'} {f.first_limit_time?.slice(0, 5) ?? ''}</span>
          : <span className="text-text-muted">否</span>}
      </td>
      <td className={cn('px-2 py-1 text-right text-text-secondary', NUM)}>{f?.amount_yi != null ? `${f.amount_yi.toFixed(1)}亿` : '—'}</td>
      <td className="px-2 py-1">
        <select disabled={readOnly} value={r.response?.response_status ?? ''}
                onChange={(e) => e.target.value && act(() => putResponse({ date, relation_id: r.id, response_status: e.target.value as ResponseStatus }))}
                className={cn(INPUT, 'w-28 py-0.5')}
                title={timedOk ? '两边都有首封时间，可以判领先 / 同步 / 滞后' : '没有分时证据（两边当天都要有首封时间），不能判领先 / 跟随'}>
          <option value="">未记录</option>
          {(Object.keys(RESPONSE_LABEL) as ResponseStatus[]).map((s) => (
            <option key={s} value={s} disabled={TIMED.includes(s) && !timedOk}>
              {RESPONSE_LABEL[s]}{TIMED.includes(s) && !timedOk ? '（缺分时）' : ''}
            </option>
          ))}
        </select>
        {err && <div className="text-[10px] text-down max-w-[12rem]">{err}</div>}
      </td>
      <td className="px-2 py-1">
        <select disabled={readOnly} value={r.confidence_status}
                onChange={(e) => act(() => putRelation({ ...r, id: r.id, date, confidence_status: e.target.value }))}
                className={cn(INPUT, 'w-20 py-0.5')} title="关联依据本身有没有核实过（不是可信度分）">
          <option value="unverified">待核</option>
          <option value="verified">已核实</option>
          <option value="rejected">不成立</option>
        </select>
      </td>
      <td className="px-2 py-1">
        {!readOnly && (
          <button onClick={() => act(() => deleteRelation(r.id))} title="删除这条关联"
                  className="text-text-muted hover:text-down"><Trash2 className="w-3.5 h-3.5" /></button>
        )}
      </td>
    </tr>
  )
}

export function RelationPanel({ pool, date, readOnly }: { pool: WatchItem[]; date: string; readOnly: boolean }) {
  const qc = useQueryClient()
  const leaders = useMemo(() => [...pool].sort((a, b) => Number(b.deep) - Number(a.deep)), [pool])
  const [code, setCode] = useState<string | null>(null)
  useEffect(() => {
    if (!leaders.find((w) => w.code === code)) setCode(leaders[0]?.code ?? null)
  }, [leaders, code])
  const leader = leaders.find((w) => w.code === code)
  const { data, isLoading } = useQuery({
    queryKey: ['recap-relations', code, date],
    queryFn: () => fetchLeaderRelations(code!, date, leader?.name),
    enabled: !!code,
  })
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['recap-relations', code, date] })
    qc.invalidateQueries({ queryKey: ['recap-day', date] })
  }

  const [q, setQ] = useState('')
  const [form, setForm] = useState({ attribute_type: 'concept' as AttributeType, attribute_value: '', related_code: '', evidence: '' })
  const [formErr, setFormErr] = useState('')
  const [showSugg, setShowSugg] = useState(false)

  if (!leaders.length) {
    return <div className="text-xs text-text-muted">先在「核心观察」里把高标加进观察池（★ 标出要深入研究的），再来这里建关联。</div>
  }
  const rels = (data?.relations ?? []).filter((r) => !q || r.related_code.includes(q) || (r.related_name ?? '').includes(q) || r.attribute_value.includes(q))
  const grouped = ATTR_ORDER.map((t) => [t, rels.filter((r) => r.attribute_type === t)] as const).filter(([, rs]) => rs.length)
  const leaderTime = data?.leader.facts?.first_limit_time

  const save = async (s: Partial<Suggestion> & { attribute_type: AttributeType; attribute_value: string; related_code: string }) => {
    setFormErr('')
    try {
      await putRelation({ date, leader_code: code!, leader_name: data?.leader.name ?? leader?.name, ...s })
      setForm((f) => ({ ...f, attribute_value: '', related_code: '', evidence: '' }))
      refresh()
    } catch (e: any) { setFormErr(e?.message ?? '保存失败') }  // eslint-disable-line @typescript-eslint/no-explicit-any
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-text-muted">选择高标</span>
        {leaders.map((w) => (
          <button key={w.code} onClick={() => setCode(w.code)}
                  className={cn('px-2 py-0.5 rounded border text-xs', w.code === code ? 'border-accent bg-accent/10 text-accent' : 'border-bg-border text-text-secondary')}>
            {w.deep && '★ '}{w.name ?? w.code}<span className="ml-1 text-[10px] text-text-muted">{w.relations}</span>
          </button>
        ))}
      </div>

      {isLoading || !data ? <LoadingSpinner /> : (
        <>
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <span className="text-text-primary font-medium">{data.leader.name ?? code}</span>
            <span className={cn(NUM, pctCls(data.leader.facts?.pct))}>{fmtPct(data.leader.facts?.pct)}</span>
            {data.leader.facts?.is_limit_up && <span className="text-up">涨停 {data.leader.facts.board_count ? `${data.leader.facts.board_count}板` : ''} 首封 {leaderTime ?? '—'}</span>}
            <span className="text-text-muted">高标 → 属性 → 关联股 → {date} 的市场响应</span>
            <input value={q} onChange={(e) => setQ(e.target.value.trim())} placeholder="搜关联股 / 属性"
                   className={cn(INPUT, 'w-40 ml-auto')} />
          </div>
          <div className="text-[11px] text-text-muted leading-relaxed">
            A/B/C 只表示关联依据的性质（A 实际产业/事件，B 共享概念/历史联动，C 名称/叙事联想），不是机会等级。
            关联知识跨日保留；响应每天单独记，昨天的联动不算今天成立。高标转强只是打开观察池的条件，不是关联股的买入信号。
          </div>

          {!grouped.length ? <div className="text-xs text-text-muted">这只还没有关联关系。</div> : grouped.map(([t, rs]) => (
            <div key={t}>
              <div className="text-[11px] text-text-secondary mb-1">{ATTR_LABEL[t]} <span className="text-text-muted">（{rs.length}）</span></div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-text-muted border-b border-bg-border">
                      {['关联股', '属性', '依据', '依据说明 / 来源', '今日', '5日', '20日', '涨停·首封', '成交额', '今日响应', '核验', ''].map((h, i) => (
                        <th key={h + i} className={cn('px-2 py-1 font-medium whitespace-nowrap', i >= 4 && i <= 8 ? 'text-right' : 'text-left')}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rs.map((r) => <RelationRow key={r.id} r={r} date={date} leaderTime={leaderTime} onChanged={refresh} readOnly={readOnly} />)}
                  </tbody>
                </table>
              </div>
            </div>
          ))}

          {!readOnly && (
            <Manual title="新增关联" hint={`依据性质自动定：${BASIS_LABEL[BASIS_OF[form.attribute_type]]}`}>
              <form className="flex flex-wrap items-center gap-1.5" onSubmit={(e) => {
                e.preventDefault()
                save({ ...form, source: 'manual' })
              }}>
                <select value={form.attribute_type} onChange={(e) => setForm({ ...form, attribute_type: e.target.value as AttributeType })}
                        className={cn(INPUT, 'w-32')}>
                  {ATTR_ORDER.map((t) => <option key={t} value={t}>{ATTR_LABEL[t]}</option>)}
                </select>
                <input value={form.attribute_value} onChange={(e) => setForm({ ...form, attribute_value: e.target.value })}
                       placeholder="属性名（概念 / 事件 / 相同的字）" className={cn(INPUT, 'w-48')} />
                <input value={form.related_code} onChange={(e) => setForm({ ...form, related_code: e.target.value.trim() })}
                       placeholder="关联股代码" className={cn(INPUT, 'w-28')} />
                <input value={form.evidence} onChange={(e) => setForm({ ...form, evidence: e.target.value })}
                       placeholder="关联依据（来源、公告、业务……）" className={cn(INPUT, 'flex-1 min-w-[12rem]')} />
                <button className="inline-flex items-center gap-0.5 px-2 py-1 rounded bg-purple-400/15 text-purple-200 text-xs">
                  <Plus className="w-3 h-3" />添加
                </button>
              </form>
              {formErr && <div className="text-xs text-down">{formErr}</div>}
            </Manual>
          )}

          {!readOnly && (
            <div>
              <button onClick={() => setShowSugg(!showSugg)} className="inline-flex items-center gap-1 text-[11px] text-accent">
                <Lightbulb className="w-3 h-3" />关联建议（{data.suggestions.length}）：名称里相同的字、同概念且当天也涨停的——只是建议，采纳了才记
              </button>
              {showSugg && (
                <div className="mt-1.5 max-h-56 overflow-auto space-y-0.5">
                  {data.suggestions.map((s) => (
                    <div key={`${s.attribute_type}-${s.related_code}`} className="flex items-center gap-2 text-xs">
                      <span className={cn('px-1 rounded text-[10px]', s.attribute_type === 'name' ? 'bg-warn/10 text-warn' : 'bg-bg-elevated text-text-secondary')}>
                        {ATTR_LABEL[s.attribute_type]}
                      </span>
                      <StockLink code={s.related_code} name={s.related_name} />
                      <span className="text-text-muted truncate">{s.evidence}</span>
                      <button onClick={() => save(s)} className="ml-auto text-[11px] text-accent shrink-0">采纳</button>
                    </div>
                  ))}
                  {!data.suggestions.length && <div className="text-xs text-text-muted">没有建议</div>}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </>
  )
}
