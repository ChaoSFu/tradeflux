import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Lightbulb, Plus, Trash2 } from 'lucide-react'
import {
  addWatch, deleteRelation, fetchLeaderRelations, fetchRelationReasons, fetchSharedSectors, putRelation, putResponse,
  type AttributeType, type ReasonUsed, type LeaderRelations, type Plan, type ResponseStatus, type SystemResponse, type WatchItem,
} from '@/api/marketRecap'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { StockPicker } from '@/components/common/StockPicker'
import { cn } from '@/utils/cn'
import { EventBadge } from './CorePoolPanel'
import { SectorPeersPanel } from './SectorPeersPanel'
import { ATTR_LABEL, BASIS_LABEL, BASIS_OF, Facts, INPUT, Manual, NUM, RESPONSE_LABEL, StockLink, basisText, fmtPct, pctCls } from './shared'

const ATTR_ORDER: AttributeType[] = ['industry', 'event', 'concept', 'manual', 'history', 'structure', 'name']
const TIMED: ResponseStatus[] = ['leading', 'sync', 'lagging']
const SOURCE_LABEL: Record<string, string> = {
  manual: '人工', name_rule: '名称规则', sector_relation: '板块关联', pool_sector: '观察池同板块（自动）', snapshot_concept: '行情概念',
}
const SYS_TONE: Record<SystemResponse, string> = {
  limit_up: 'text-up', up_response: 'text-up', independent_strong: 'text-accent', not_met: 'text-text-muted', insufficient: 'text-warn',
}
type Row = LeaderRelations['relations'][number]

/**
 * 基础原因（2026-11-22 用户要的：「之前的原因」要给出来，选不到再自己写）——就是改版前那几类关联类型。
 * 选了它们按原来的类别存（依据 A/B/C 由类别定，跟以前一样）；自己写的才是 manual，不分 A/B/C。
 * 共享概念不在这里：同属概念板块走「同属板块」。
 */
const BASIC_REASONS: { value: string; type: AttributeType; hint: string }[] = [
  { value: ATTR_LABEL.event, type: 'event', hint: '同一事件 / 政策 / 订单催化' },
  { value: ATTR_LABEL.industry, type: 'industry', hint: '上下游、同一产业链（不同属一个板块时用）' },
  { value: ATTR_LABEL.history, type: 'history', hint: '以前多次一起涨、一起炒过' },
  { value: ATTR_LABEL.structure, type: 'structure', hint: '同一梯队、补涨、跟风' },
  { value: ATTR_LABEL.name, type: 'name', hint: '名称里相同的字、谐音' },
]
const basicOf = (v: string) => BASIC_REASONS.find((b) => b.value === v.trim())

type ReasonItem = { value: string; basic?: (typeof BASIC_REASONS)[number]; used?: ReasonUsed }

/**
 * 「其他原因」输入框：上面是基础原因（原来那几类），下面是以前写过的（按用过的次数排）；
 * 点选或 ↑↓ + 回车选中，没有合适的就直接写新的。
 * 没用方向键选中某条时，回车照常提交表单——不会把正在写的新原因换成下拉里的旧原因。
 */
function ReasonPicker({ value, onChange, reasons, recorded }: {
  value: string
  onChange: (v: string) => void
  reasons: ReasonUsed[]
  recorded: Set<string>          // 这对已经按这个原因记过的
}) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const term = value.trim()
  const hit = (s: string) => !term || s.includes(term)
  const basics: ReasonItem[] = BASIC_REASONS.filter((b) => hit(b.value) || hit(b.hint)).map((b) => ({ value: b.value, basic: b }))
  // 以前写过的里跟基础原因同名的（改版前存的类别名）不重复列
  const used: ReasonItem[] = reasons.filter((r) => !basicOf(r.value) && hit(r.value)).slice(0, 12).map((r) => ({ value: r.value, used: r }))
  const items = [...basics, ...used]
  const pick = (v: string) => { onChange(v); setOpen(false); setActive(-1) }
  const row = (it: ReasonItem, i: number) => {
    const done = recorded.has(it.value)
    return (
      <button key={`${it.basic ? 'b' : 'u'}-${it.value}`} type="button" disabled={done}
              onMouseDown={() => !done && pick(it.value)} onMouseEnter={() => setActive(i)}
              className={cn('flex w-full items-baseline gap-2 px-2.5 py-1.5 text-left text-xs',
                done ? 'opacity-40 cursor-not-allowed' : i === active ? 'bg-bg-card' : 'hover:bg-bg-card')}>
        <span className="text-text-primary shrink-0">{it.value}</span>
        {/* 类别字母紧跟名字：下拉在窄屏会被右边裁掉，放最右就看不到了 */}
        {it.basic && <span className="shrink-0 px-1 rounded text-[10px] bg-bg-card text-text-secondary">{BASIS_OF[it.basic.type]}</span>}
        {it.basic && <span className="text-[10px] text-text-muted truncate">{it.basic.hint}</span>}
        <span className="ml-auto shrink-0 text-[10px] text-text-muted">
          {done ? '这对已记' : it.used ? `用过 ${it.used.count} 次 · ${it.used.last_used.slice(5)}` : ''}
        </span>
      </button>
    )
  }
  return (
    <div className="relative w-72">
      <input value={value} maxLength={100} className={cn(INPUT, 'w-full')}
             placeholder="选一个原因，没有合适的就直接写"
             onFocus={() => setOpen(true)}
             onBlur={() => setTimeout(() => setOpen(false), 150)}
             onChange={(e) => { onChange(e.target.value); setOpen(true); setActive(-1) }}
             onKeyDown={(e) => {
               if (!open || !items.length) return
               if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => (i + 1) % items.length) }
               else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => (i <= 0 ? items.length - 1 : i - 1)) }
               else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); pick(items[active].value) }
               else if (e.key === 'Escape') setOpen(false)
             }} />
      {open && items.length > 0 && (
        <div className="absolute z-20 mt-1 w-[26rem] max-w-[90vw] max-h-80 overflow-auto rounded-lg border border-bg-border bg-bg-elevated shadow-xl shadow-black/40">
          {!!basics.length && (
            <div className="px-2.5 py-1 text-[10px] text-text-muted border-b border-bg-border/60">基础原因（依据 A/B/C 按类别定，跟以前一样）</div>
          )}
          {basics.map((it, i) => row(it, i))}
          {!!used.length && (
            <div className="px-2.5 py-1 text-[10px] text-text-muted border-y border-bg-border/60">
              以前自己写过的（按用过的次数排，单独一组、不分 A/B/C）{term ? ` · 含「${term}」` : ''}
            </div>
          )}
          {used.map((it, i) => row(it, basics.length + i))}
          {!!term && !basicOf(term) && !reasons.some((r) => r.value === term) && (
            <div className="px-2.5 py-1 text-[10px] text-text-muted border-t border-bg-border/60">没有合适的？直接按回车，记成新原因「{term}」</div>
          )}
        </div>
      )}
    </div>
  )
}

function RelationRow({ r, date, leaderTime, labels, onChanged, onWatch, onPlan, readOnly }: {
  r: Row
  date: string
  leaderTime: string | null | undefined
  labels: Record<SystemResponse, string>
  onChanged: () => void
  onWatch: (r: Row) => void
  onPlan: (r: Row) => void
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
    <tr className={cn('border-b border-bg-border/50 last:border-0 align-top', r.confidence_status === 'rejected' && 'opacity-50')}>
      <td className="px-2 py-1 whitespace-nowrap"><StockLink code={r.related_code} name={r.related_name ?? f?.name ?? null} hasDetail={f?.has_detail ?? true} /></td>
      <td className="px-2 py-1 text-text-primary whitespace-nowrap max-w-[14rem] truncate" title={r.attribute_value}>{r.attribute_value}</td>
      <td className="px-2 py-1 whitespace-nowrap" title={BASIS_LABEL[r.relation_basis]}>
        <span className={cn('px-1 rounded text-[10px]', r.relation_basis === 'A' ? 'bg-accent/15 text-accent'
          : r.relation_basis === 'B' ? 'bg-bg-elevated text-text-secondary'
          : r.relation_basis === 'M' ? 'bg-purple-400/15 text-purple-300' : 'bg-warn/10 text-warn')}>{basisText(r.relation_basis)}</span>
      </td>
      <td className="px-2 py-1 text-[11px] text-text-muted max-w-[14rem]" title={r.evidence ?? undefined}>
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
      <td className={cn('px-2 py-1 whitespace-nowrap text-[11px]', SYS_TONE[r.system_response])}
          title="按预先写死的条件分：涨停 / 当日涨幅 ≥5% / 高标跌幅 ≤-3% 或断板时独立走强。「尚未满足」不等于「没响应」">
        {labels[r.system_response]}
      </td>
      <td className="px-2 py-1">
        <select disabled={readOnly} value={r.response?.response_status ?? ''}
                onChange={(e) => e.target.value && act(() => putResponse({ date, relation_id: r.id, response_status: e.target.value as ResponseStatus }))}
                className={cn(INPUT, 'w-28 py-0.5')}
                title={timedOk ? '两边都有首封时间：只能说谁先封板，不能证明谁带动谁' : '没有首封时间（两边都要有），不能判先后'}>
          <option value="">未记录</option>
          {(Object.keys(RESPONSE_LABEL) as ResponseStatus[]).map((s) => (
            <option key={s} value={s} disabled={TIMED.includes(s) && !timedOk}>
              {RESPONSE_LABEL[s]}{TIMED.includes(s) && !timedOk ? '（缺首封）' : ''}
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
      <td className="px-2 py-1 whitespace-nowrap">
        {!readOnly && (
          <div className="flex items-center gap-1.5">
            <button onClick={() => onWatch(r)} className="text-[11px] text-accent" title="加入观察池（不是交易）">观察</button>
            <button onClick={() => onPlan(r)} className="text-[11px] text-purple-300" title="放进次日预案（还要写它自己的入场和失效条件）">拟交易</button>
            <button onClick={() => act(() => deleteRelation(r.id))} title="删除这条关联" className="text-text-muted hover:text-down">
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </td>
    </tr>
  )
}

export function RelationPanel({ pool, date, readOnly, selected, onSelect, extraLeader, onAddPlan, onPoolChanged, mainline, onAddSector }: {
  pool: WatchItem[]
  date: string
  readOnly: boolean
  selected: string | null
  onSelect: (code: string) => void
  extraLeader: { code: string; name: string | null } | null
  onAddPlan: (p: Partial<Plan>) => void
  onPoolChanged: () => void
  mainline: Set<string>                                        // 已经在主线板块列表里的板块代码
  onAddSector: (s: { code: string; name: string }) => void      // 加进主线板块列表（草稿）
}) {
  const qc = useQueryClient()
  const leaders = useMemo(() => {
    // 关联条数多的在前；一样多看近 60 日涨停天数（没数据的排后面）；再一样保持观察池原来的顺序
    const lu60 = (w: WatchItem) => w.facts?.limit_up_days_60d ?? -1
    const l = [...pool].sort((a, b) => b.relations - a.relations || lu60(b) - lu60(a))
      .map((w) => ({ code: w.code, name: w.name, deep: w.deep, relations: w.relations, lu60: w.facts?.limit_up_days_60d ?? null, temp: false }))
    if (extraLeader && !l.find((x) => x.code === extraLeader.code)) l.unshift({ ...extraLeader, deep: false, relations: 0, lu60: null, temp: true })
    return l
  }, [pool, extraLeader])
  const code = selected && leaders.find((w) => w.code === selected) ? selected : leaders[0]?.code ?? null
  const leader = leaders.find((w) => w.code === code)
  const [showRejected, setShowRejected] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['recap-relations', code, date, showRejected],
    queryFn: () => fetchLeaderRelations(code!, date, leader?.name, showRejected),
    enabled: !!code,
  })
  const reasons = useQuery({ queryKey: ['recap-relation-reasons'], queryFn: fetchRelationReasons, staleTime: 5 * 60 * 1000, enabled: !readOnly })
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['recap-relations', code, date] })
    qc.invalidateQueries({ queryKey: ['recap-relation-reasons'] })
    qc.invalidateQueries({ queryKey: ['recap-day', date] })
    qc.invalidateQueries({ queryKey: ['recap-cores', date] })
  }

  const [q, setQ] = useState('')
  // 关联原因只有两种（2026-11-22 用户定）：同属某个板块（从两只共同所在的板块里选），或者自己写一句话
  const [form, setForm] = useState({ kind: 'sector' as 'sector' | 'other', sector: '', reason: '',
                                     related_code: '', related_name: null as string | null, evidence: '' })
  const pairOk = !!code && /^\d{6}$/.test(form.related_code) && form.related_code !== code
  const shared = useQuery({
    queryKey: ['recap-shared-sectors', code, form.related_code],
    queryFn: () => fetchSharedSectors(code!, form.related_code),
    enabled: pairOk,
    staleTime: 10 * 60 * 1000,
  })
  const [formErr, setFormErr] = useState('')
  const [showSugg, setShowSugg] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())

  if (!leaders.length) {
    return <div className="text-xs text-text-muted">先在人气核心池里点「观察」或「关联研究」，再来这里建关联。</div>
  }
  const rels = (data?.relations ?? []).filter((r) => !q || r.related_code.includes(q) || (r.related_name ?? '').includes(q) || r.attribute_value.includes(q))
  const grouped = ATTR_ORDER.map((t) => [t, rels.filter((r) => r.attribute_type === t)] as const).filter(([, rs]) => rs.length)
  const recordedFor = new Set((data?.relations ?? []).filter((r) => r.related_code === form.related_code).map((r) => r.attribute_value))
  const leaderTime = data?.leader.facts?.first_limit_time
  const sum = data?.summary
  const save = async (s: { attribute_type: AttributeType; attribute_value: string; related_code: string; related_name?: string | null; evidence?: string | null; source?: string; confidence_status?: string }) => {
    await putRelation({ date, leader_code: code!, leader_name: data?.leader.name ?? leader?.name, ...s })
  }
  const submit = async () => {
    setFormErr('')
    try {
      const sec = form.kind === 'sector' ? (shared.data ?? []).find((x) => x.code === form.sector) : null
      if (form.kind === 'sector' && !sec) { setFormErr('先选一个两只共同所在的板块，或者切到「其他原因」自己写'); return }
      await save({
        // 基础原因按原来的类别存；自己写的是 manual
        attribute_type: sec ? sec.type : basicOf(form.reason)?.type ?? 'manual', attribute_value: sec ? sec.name : form.reason.trim(),
        related_code: form.related_code, related_name: form.related_name, evidence: form.evidence, source: 'manual',
      })
      setForm((f) => ({ ...f, sector: '', reason: '', related_code: '', related_name: null, evidence: '' }))
      refresh()
    } catch (e: any) { setFormErr(e?.message ?? '保存失败') }  // eslint-disable-line @typescript-eslint/no-explicit-any
  }
  const batch = async (status: 'unverified' | 'rejected') => {
    setFormErr('')
    const chosen = (data?.suggestions ?? []).filter((s) => picked.has(`${s.attribute_type}-${s.related_code}`))
    try {
      for (const s of chosen) await save({ ...s, confidence_status: status })
      setPicked(new Set())
      refresh()
    } catch (e: any) { setFormErr(e?.message ?? '保存失败') }  // eslint-disable-line @typescript-eslint/no-explicit-any
  }
  const onWatch = async (r: Row) => { await addWatch({ code: r.related_code, date, name: r.related_name }); onPoolChanged() }
  const watchCode = async (c: string, name: string | null) => { await addWatch({ code: c, date, name }); onPoolChanged() }
  const adoptSector = async (sec: { name: string; type: string }, c: string, name: string | null, active: string) => {
    setFormErr('')
    try {
      await save({ attribute_type: sec.type === 'industry' ? 'industry' : 'concept', attribute_value: sec.name,
                   related_code: c, related_name: name, source: 'sector_relation',
                   evidence: `同属关注板块「${sec.name}」（${date} 当天${active}）` })
      refresh()
    } catch (e: any) { setFormErr(e?.message ?? '保存失败') }  // eslint-disable-line @typescript-eslint/no-explicit-any
  }
  const adopted = new Set((data?.relations ?? []).filter((r) => r.attribute_type === 'industry' || r.attribute_type === 'concept')
    .map((r) => `${r.attribute_value}|${r.related_code}`))
  const onPlan = (r: Row) => onAddPlan({
    stock_code: r.related_code, stock_name: r.related_name ?? r.facts?.name ?? null, leader_code: code,
    relation_note: `${ATTR_LABEL[r.attribute_type]}：${r.attribute_value}（${r.relation_basis === 'M' ? '人工写的原因' : `${r.relation_basis} 类依据`}）${r.evidence ? `；${r.evidence}` : ''}`,
  })

  return (
    <>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[11px] text-text-muted">选择高标</span>
        {leaders.map((w) => (
          <button key={w.code} onClick={() => onSelect(w.code)}
                  title={`关联股 ${w.relations} 只（按股票去重） · 近60日涨停 ${w.lu60 ?? '—'} 次`}
                  className={cn('px-2 py-0.5 rounded border text-xs', w.code === code ? 'border-accent bg-accent/10 text-accent' : 'border-bg-border text-text-secondary')}>
            {w.deep && '★ '}{w.name ?? w.code}{w.temp && <span className="text-[10px] text-text-muted ml-0.5">（未观察）</span>}
            <span className="ml-1 text-[10px] text-text-muted">{w.relations}</span>
          </button>
        ))}
      </div>

      {isLoading || !data ? <LoadingSpinner /> : (
        <>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-text-primary font-medium">{data.leader.name ?? code}</span>
            <span className={cn(NUM, pctCls(data.leader.facts?.pct))}>{fmtPct(data.leader.facts?.pct)}</span>
            {data.leader.facts?.is_limit_up && <span className="text-up">涨停 {data.leader.facts.board_count ? `${data.leader.facts.board_count}板` : ''} 首封 {leaderTime ?? '—'}</span>}
            {data.leader.events.map((t) => <EventBadge key={t} t={t} />)}
            <input value={q} onChange={(e) => setQ(e.target.value.trim())} placeholder="搜关联股 / 属性" className={cn(INPUT, 'w-40 ml-auto')} />
          </div>
          {sum && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-text-secondary">
              <span>已记录的关联 <b className={NUM}>{sum.stocks}</b> 只（按股票去重）：</span>
              {Object.entries(sum.by_type).map(([t, n]) => <span key={t}>{ATTR_LABEL[t as AttributeType]} {n}</span>)}
              <span className="text-text-muted">|</span>
              {(Object.keys(sum.response_labels) as SystemResponse[]).filter((k) => sum.by_response[k]).map((k) => (
                <span key={k} className={SYS_TONE[k]}>{sum.response_labels[k]} {sum.by_response[k]}</span>
              ))}
              {sum.manual_pending > 0 && <span className="text-text-muted">人工待验证 {sum.manual_pending}</span>}
              <label className="ml-auto flex items-center gap-1 text-text-muted">
                <input type="checkbox" checked={showRejected} onChange={(e) => setShowRejected(e.target.checked)} />显示「不成立」的
              </label>
            </div>
          )}
          <div className="text-[11px] text-text-muted leading-relaxed">
            A/B/C 只是依据性质（A 产业/事件，B 概念/历史/情绪结构，C 名称/叙事），不是机会等级。系统响应只按写死的条件分；
            首封时间只能说明谁先封板，证明不了谁带动谁。高标活跃只是该来看这张表的提醒，关联股要过它自己的买入检查。
          </div>

          <Facts title="① 板块关联（自动）：高标所在的每个板块，当天同板块的活跃股">
            <SectorPeersPanel code={code!} date={date} adopted={adopted} readOnly={readOnly}
                              onWatch={watchCode} onPlan={onAddPlan} onAdopt={adoptSector}
                              mainline={mainline} onAddSector={onAddSector} />
          </Facts>

          <div className="flex items-center gap-2 pt-1">
            <span className="text-[10px] px-1 rounded bg-purple-400/15 text-purple-300">人工</span>
            <span className="text-xs font-medium text-text-primary">② 其他关联（人工录入的长期知识：产业链、事件、名称、历史联动……）</span>
            <span className="text-[11px] text-text-muted">重点观察池里同属一个关注板块的，自动互相记上（待核）；删掉或标不成立的不会再自动加回来</span>
          </div>
          {!grouped.length ? <div className="text-xs text-text-muted">还没有录入其他关联。板块关联里点「记为关联」、重点观察池里同板块的，都会进这里。</div> : grouped.map(([t, rs]) => (
            <div key={t}>
              {/* 按股票数：一只票挂了几个属性（同属几个关注板块）也只算一只 */}
              <div className="text-[11px] text-text-secondary mb-1">{ATTR_LABEL[t]} <span className="text-text-muted">（{new Set(rs.map((r) => r.related_code)).size} 只）</span></div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-text-muted border-b border-bg-border">
                      {['关联股', '属性', '依据', '依据说明 / 来源', '今日', '5日', '20日', '涨停·首封', '成交额', '系统响应', '人工记录', '核验', ''].map((h, i) => (
                        <th key={h + i} className={cn('px-2 py-1 font-medium whitespace-nowrap', i >= 4 && i <= 8 ? 'text-right' : 'text-left')}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rs.map((r) => <RelationRow key={r.id} r={r} date={date} leaderTime={leaderTime} labels={sum!.response_labels}
                                                onChanged={refresh} onWatch={onWatch} onPlan={onPlan} readOnly={readOnly} />)}
                  </tbody>
                </table>
              </div>
            </div>
          ))}

          {!readOnly && (
            <Manual title="新增关联" hint="同属板块：行业 A、概念 B；其他原因：基础原因按类别定 A/B/C，自己写的单独一组、不分 A/B/C">
              {/* 顺序就是做事的顺序：① 先搜到关联股 → ② 关联原因（同属板块 / 自己写一句话）→ 添加。
                  原来「关联类型」下拉和「属性名」是两格，其实是一件事（2026-11-22 用户指出） */}
              <form className="flex flex-wrap items-center gap-1.5" onSubmit={(e) => { e.preventDefault(); submit() }}>
                <span className="text-[11px] text-text-muted">① 关联股</span>
                <div className="w-52">
                  <StockPicker code={form.related_code} name={form.related_name} inputCls={cn(INPUT, 'w-full')}
                               placeholder="搜代码或名称，点结果选中"
                               onPick={(c, n) => setForm((f) => ({ ...f, related_code: c, related_name: n, sector: '' }))} />
                </div>
                <span className="text-[11px] text-text-muted ml-1">② 关联原因</span>
                <div className="inline-flex rounded border border-bg-border overflow-hidden text-xs">
                  {([['sector', '同属板块'], ['other', '其他原因']] as const).map(([k, l]) => (
                    <button key={k} type="button" onClick={() => setForm((f) => ({ ...f, kind: k }))}
                            className={cn('px-2 py-1', form.kind === k ? 'bg-purple-400/15 text-purple-200' : 'text-text-muted hover:text-text-secondary')}>{l}</button>
                  ))}
                </div>
                {form.kind === 'sector' ? (
                  <select value={form.sector} onChange={(e) => setForm({ ...form, sector: e.target.value })}
                          disabled={!pairOk || !shared.data?.length} className={cn(INPUT, 'w-56')}>
                    <option value="">
                      {!pairOk ? '先在①选关联股' : shared.isLoading ? '读取共同板块…'
                        : shared.isError ? '共同板块读取失败，可以先用「其他原因」'
                        : !shared.data?.length ? '这两只没有共同板块，用「其他原因」' : `选共同板块（${shared.data.length} 个）`}
                    </option>
                    {(shared.data ?? []).map((x) => {
                      const done = recordedFor.has(x.name)
                      return (
                        <option key={x.code} value={x.code} disabled={done}>
                          {x.watched ? '★ ' : ''}{x.name}（{x.type === 'industry' ? '行业 A' : `${x.sector_type === 'region' ? '地区' : '概念'} B`}）{done ? ' · 已记' : ''}
                        </option>
                      )
                    })}
                  </select>
                ) : (
                  <ReasonPicker value={form.reason} onChange={(v) => setForm((f) => ({ ...f, reason: v }))}
                                reasons={reasons.data ?? []} recorded={recordedFor} />
                )}
                <input value={form.evidence} onChange={(e) => setForm({ ...form, evidence: e.target.value })}
                       placeholder="来源 / 依据（可选：公告、新闻、业务……）" className={cn(INPUT, 'flex-1 min-w-[12rem]')} />
                <button disabled={!form.related_code || (form.kind === 'sector' ? !form.sector : !form.reason.trim())}
                        title={!form.related_code ? '先在①搜到并选中关联股' : '先在②选共同板块或写关联原因'}
                        className="inline-flex items-center gap-0.5 px-2 py-1 rounded bg-purple-400/15 text-purple-200 text-xs disabled:opacity-40 disabled:cursor-not-allowed"><Plus className="w-3 h-3" />添加</button>
              </form>
              {!!shared.data?.length && form.kind === 'sector' && (
                <div className="text-[10px] text-text-muted">★ 是关注板块，排在前面；「已记」是这对已经按这个板块记过的</div>
              )}
              {formErr && <div className="text-xs text-down">{formErr}</div>}
            </Manual>
          )}

          {!readOnly && (
            <div>
              <button onClick={() => setShowSugg(!showSugg)} className="inline-flex items-center gap-1 text-[11px] text-accent">
                <Lightbulb className="w-3 h-3" />名称联想建议（{data.suggestions.length}）：名称里相同的字，带重名数（越少越具体）；只是建议，采纳了才记
              </button>
              {showSugg && (
                <div className="mt-1.5 space-y-1">
                  <div className="flex items-center gap-2 text-[11px]">
                    <button onClick={() => setPicked(new Set(data.suggestions.map((s) => `${s.attribute_type}-${s.related_code}`)))} className="text-text-muted">全选</button>
                    <button onClick={() => setPicked(new Set())} className="text-text-muted">清空</button>
                    <span className="text-text-muted">已选 {picked.size}</span>
                    <button disabled={!picked.size} onClick={() => batch('unverified')} className="px-1.5 py-0.5 rounded bg-purple-400/15 text-purple-200 disabled:opacity-40">采纳选中（待核）</button>
                    <button disabled={!picked.size} onClick={() => batch('rejected')} className="px-1.5 py-0.5 rounded bg-bg-elevated text-text-secondary disabled:opacity-40"
                            title="记成「不成立」，以后不再建议；勾「显示不成立的」还能看到">忽略选中</button>
                  </div>
                  <div className="max-h-56 overflow-auto space-y-0.5">
                    {data.suggestions.map((s) => {
                      const k = `${s.attribute_type}-${s.related_code}`
                      return (
                        <label key={k} className="flex items-center gap-2 text-xs">
                          <input type="checkbox" checked={picked.has(k)} onChange={(e) => {
                            const n = new Set(picked); if (e.target.checked) n.add(k); else n.delete(k); setPicked(n)
                          }} />
                          <span className={cn('px-1 rounded text-[10px]', s.attribute_type === 'name' ? 'bg-warn/10 text-warn' : 'bg-bg-elevated text-text-secondary')}>{ATTR_LABEL[s.attribute_type]}</span>
                          <StockLink code={s.related_code} name={s.related_name} />
                          <span className="text-text-muted truncate">{s.evidence}</span>
                        </label>
                      )
                    })}
                    {!data.suggestions.length && <div className="text-xs text-text-muted">没有建议</div>}
                  </div>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </>
  )
}
