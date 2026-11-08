import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Lock } from 'lucide-react'
import { fetchLockedVersions, type RecapDraft, type RecapSummary, type Verification, type VersionMeta } from '@/api/marketRecap'
import { cn } from '@/utils/cn'
import { Manual, NUM, POSTURE_LABEL, SCENARIO_META, TextArea, fmtPct, pctCls } from './shared'

const MISTAKE_TYPES = { judgment: '判断错误', execution: '执行错误', strategy: '策略错误（需足够样本）', none: '没有可归因错误' }

export function ReflectPanel({ draft, set, readOnly }: { draft: RecapDraft; set: (p: Partial<RecapDraft>) => void; readOnly: boolean }) {
  const quick = (k: 'discovery' | 'mistake' | 'hypothesis', text: string) => (
    !readOnly && <button onClick={() => set({ [k]: text, ...(k === 'mistake' ? { mistake_type: 'none' as const } : {}) })}
                         className="text-[10px] text-text-muted hover:text-text-secondary">{text}</button>
  )
  return (
    <Manual title="认知与执行复盘" hint="每天至少一个发现、一个错误、一个待验证假设；没有就如实写没有，不硬编">
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-[11px] text-text-secondary">一个发现 {quick('discovery', '今天未发现新规律')}</div>
          <TextArea value={draft.discovery} onChange={(v) => set({ discovery: v })} rows={3} disabled={readOnly} />
        </div>
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-[11px] text-text-secondary">
            一个错误或偏差
            <select value={draft.mistake_type ?? ''} disabled={readOnly}
                    onChange={(e) => set({ mistake_type: (e.target.value || null) as RecapDraft['mistake_type'] })}
                    className="bg-bg-elevated border border-bg-border rounded px-1 text-[10px]">
              <option value="">类型</option>
              {Object.entries(MISTAKE_TYPES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            {quick('mistake', '没有可归因错误')}
          </div>
          <TextArea value={draft.mistake} onChange={(v) => set({ mistake: v })} rows={3} disabled={readOnly}
                    placeholder="原判断 → 实际事实 → 偏差原因 → 下一步改进。一次亏损不能证明策略错误" />
        </div>
        <div className="space-y-1">
          <div className="text-[11px] text-text-secondary">一个待验证假设</div>
          <TextArea value={draft.hypothesis} onChange={(v) => set({ hypothesis: v })} rows={3} disabled={readOnly}
                    placeholder="写成以后能被事实验证的形式：在什么条件下，预期看到什么" />
        </div>
      </div>
    </Manual>
  )
}

function Card4({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card-elevated p-3 space-y-1">
      <div className="text-[11px] text-text-muted">{title}</div>
      <div className="text-xs text-text-secondary space-y-0.5">{children}</div>
    </div>
  )
}

/** 四项最终结果。数字都是从已保存的记录数出来的 */
export function SummaryCard({ s, dirty }: { s: RecapSummary; dirty: boolean }) {
  return (
    <div className="space-y-1.5">
      {dirty && <div className="text-[11px] text-warn">有未保存的修改：下面按已保存的内容统计</div>}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <Card4 title="① 市场状态（你的判断）">
          <div>操作姿态：<b className="text-purple-200">{s.market.posture ? POSTURE_LABEL[s.market.posture] : '未填'}</b></div>
          <div>依据：{s.market.evidence.length ? s.market.evidence.map((e) => `${e.label} ${e.value}`).join('；') : '—'}</div>
          {s.market.evidence_note && <div className="text-text-muted">{s.market.evidence_note}</div>}
          <div>反证：{s.market.invalidation.length ? s.market.invalidation.join('；') : '—'}</div>
        </Card4>
        <Card4 title="② 核心观察池">
          <div>核心主线 {s.core.core_sectors.length} 个：{s.core.core_sectors.map((x) => x.name).join('、') || '—'}</div>
          <div>次级观察 {s.core.secondary_sectors.length} 个</div>
          <div>重点观察 <b className={NUM}>{s.core.watch_count}</b> 只（深入研究 {s.core.deep_count}）</div>
          <div>关联关系 <b className={NUM}>{s.core.relations}</b> 条：已记录响应 {s.core.responses_judged}，待验证 {s.core.responses_pending}</div>
        </Card4>
        <Card4 title="③ 次日交易预案">
          {s.plan.flat ? <div className="text-purple-200">空仓预案</div> : (
            <>
              <div>候选股 <b className={NUM}>{s.plan.count}</b> 只</div>
              <div>入场 / 失效 / 风险都写全的 {s.plan.complete} 只</div>
            </>
          )}
        </Card4>
        <Card4 title="④ 认知与行为改进">
          <div>今日发现：{s.learning.discovery ? '1 项' : '—'}</div>
          <div>需要修正的判断：{s.learning.mistake ? `1 项${s.learning.mistake_type ? `（${MISTAKE_TYPES[s.learning.mistake_type as keyof typeof MISTAKE_TYPES] ?? ''}）` : ''}` : '—'}</div>
          <div>待验证假设：{s.learning.hypothesis ? '1 项' : '—'}</div>
        </Card4>
      </div>
    </div>
  )
}

/** 某个复盘日锁定的预案 vs 下一交易日实际。用的是最后一份盘前版本 */
export function VerificationCard({ v, recapDate }: { v: Verification; recapDate: string }) {
  if (v.status === 'no_lock') return <div className="text-xs text-text-muted">{recapDate} 没有锁定的预案。</div>
  const head = (
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <span className="text-text-secondary">{recapDate} 锁定的预案 v{v.version?.version}</span>
      <span className="text-text-muted">锁定于 {v.version?.locked_at.replace('T', ' ').slice(0, 16)}</span>
      {v.backfilled
        ? <span className="px-1 rounded bg-warn/15 text-warn text-[10px]" title="锁定时间晚于目标交易日开盘：行情已经开始了，不能当盘前判断">事后记录</span>
        : <span className="px-1 rounded bg-up/10 text-up text-[10px]">盘前锁定</span>}
      <span className="text-text-muted">→ 目标交易日 {v.target_trade_date}</span>
    </div>
  )
  if (v.status === 'pending') return <div className="space-y-1">{head}<div className="text-xs text-text-muted">{v.message}</div></div>
  return (
    <div className="space-y-2">
      {head}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <span>当时的姿态：<b className="text-purple-200">{v.market?.posture ? POSTURE_LABEL[v.market.posture] : '未填'}</b></span>
        {v.market?.indices.map((i) => (
          <span key={i.code} className="text-text-secondary">{i.name} <span className={cn(NUM, pctCls(i.pct))}>{fmtPct(i.pct)}</span>
            {i.above_ma20 === false && <span className="text-down text-[10px] ml-0.5">MA20下</span>}</span>
        ))}
      </div>
      {!!v.market?.invalidation.length && (
        <div className="text-[11px] text-text-muted">当时写的反证条件（对照今天的事实自己判断是否出现）：{v.market.invalidation.join('；')}</div>
      )}
      {v.flat_plan ? <div className="text-xs text-text-secondary">当时是空仓预案。</div> : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs [&_td]:whitespace-nowrap">
            <thead>
              <tr className="text-text-muted border-b border-bg-border">
                {['股票', '情景', '入场触发', '失效条件', '止损价', '锁定时', '次日涨跌', '次日收盘', '涨停', '止损'].map((h, i) => (
                  <th key={h} className={cn('px-2 py-1 font-medium', i >= 4 ? 'text-right' : 'text-left')}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(v.plans ?? []).map((p) => (
                <tr key={p.stock_code} className="border-b border-bg-border/50 last:border-0 align-top">
                  <td className="px-2 py-1">{p.stock_name ?? p.stock_code}<span className="ml-1 text-[10px] text-text-muted">{p.stock_code}</span></td>
                  <td className="px-2 py-1 text-text-muted">{p.scenario ? SCENARIO_META[p.scenario].title.split(' ')[0] : '—'}</td>
                  <td className="px-2 py-1 text-text-secondary whitespace-normal max-w-[16rem]">{p.entry_trigger}</td>
                  <td className="px-2 py-1 text-text-secondary whitespace-normal max-w-[16rem]">{p.invalidation}</td>
                  <td className={cn('px-2 py-1 text-right', NUM)}>{p.planned_stop ?? '—'}</td>
                  <td className={cn('px-2 py-1 text-right', NUM, pctCls(p.at_lock?.pct))}>{fmtPct(p.at_lock?.pct)}</td>
                  <td className={cn('px-2 py-1 text-right', NUM, pctCls(p.actual.pct))}>
                    {p.actual.status === 'missing' ? <span className="text-text-muted">无数据</span> : fmtPct(p.actual.pct)}
                  </td>
                  <td className={cn('px-2 py-1 text-right', NUM)}>{p.actual.close ?? '—'}</td>
                  <td className="px-2 py-1 text-right">{p.actual.is_limit_up == null ? '?' : p.actual.is_limit_up ? <span className="text-up">是 {p.actual.first_limit_time?.slice(0, 5) ?? ''}</span> : '否'}</td>
                  <td className="px-2 py-1 text-right">{p.below_stop == null ? '—' : p.below_stop ? <span className="text-down">收盘跌破</span> : <span className="text-text-muted">未破</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="text-[11px] text-text-muted">入场是否触发要你对照分时自己判断；系统只摆出收盘事实，不替你补判。</div>
    </div>
  )
}

export function VersionList({ date, versions }: { date: string; versions: VersionMeta[] }) {
  const [open, setOpen] = useState<number | null>(null)
  const { data } = useQuery({ queryKey: ['recap-locked', date], queryFn: () => fetchLockedVersions(date), enabled: open != null })
  if (!versions.length) return null
  const shown = data?.find((v) => v.version === open)
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <Lock className="w-3.5 h-3.5 text-text-muted" />
        {versions.map((v) => (
          <button key={v.version} onClick={() => setOpen(open === v.version ? null : v.version)}
                  className={cn('px-1.5 py-0.5 rounded border text-[11px]', open === v.version ? 'border-accent text-accent' : 'border-bg-border text-text-secondary')}>
            v{v.version} · {v.locked_at.replace('T', ' ').slice(5, 16)} · {v.pre_open ? '盘前' : '事后'}
          </button>
        ))}
        <span className="text-[11px] text-text-muted">锁定版本只读；改草稿再锁定会生成新版本</span>
      </div>
      {shown && (
        <div className="card-elevated p-2.5 text-xs space-y-1">
          <div>姿态：{shown.payload.recap.market_posture ? POSTURE_LABEL[shown.payload.recap.market_posture] : '未填'}；
            反证：{(shown.payload.recap.invalidation ?? []).join('；') || '—'}</div>
          <div>观察池：{shown.payload.watch_pool.map((w) => `${w.deep ? '★' : ''}${w.name ?? w.code}`).join('、') || '—'}；关联 {shown.payload.relations.length} 条</div>
          {shown.payload.recap.flat_plan ? <div>空仓预案</div> : shown.payload.plans.map((p) => (
            <div key={p.stock_code} className="text-text-secondary">
              {p.stock_name ?? p.stock_code}（{p.scenario ?? '—'}）入场：{p.entry_trigger}｜失效：{p.invalidation}
              {p.planned_stop != null && `｜止损 ${p.planned_stop}`}{p.risk_budget != null && `｜风险 ${p.risk_budget}%`}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
