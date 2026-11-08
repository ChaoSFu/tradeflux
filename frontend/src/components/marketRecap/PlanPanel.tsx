import { Link } from 'react-router-dom'
import { ClipboardCheck, Plus, Trash2 } from 'lucide-react'
import type { Plan, RecapDraft, Scenario, WatchItem } from '@/api/marketRecap'
import { cn } from '@/utils/cn'
import { INPUT, Manual, SCENARIO_META, TextArea } from './shared'

const MAX_PLANS = 5
const SCENARIOS = Object.keys(SCENARIO_META) as Scenario[]
const PLAN_STATUS = { waiting: '等待', triggered: '已触发', not_triggered: '未触发', invalid: '失效', unverified: '未验证' }

const emptyPlan = (): Plan => ({
  stock_code: '', stock_name: null, leader_code: null, sector_name: null, scenario: 'A', reason: null,
  entry_trigger: '', invalidation: '', planned_stop: null, risk_budget: null, position_limit: null,
  observe_until: null, status: 'waiting',
})

const numOrNull = (v: string) => (v.trim() === '' ? null : Number(v))

export function ScenarioPanel({ draft, set, readOnly }: { draft: RecapDraft; set: (p: Partial<RecapDraft>) => void; readOnly: boolean }) {
  const put = (s: Scenario, f: string, v: string) =>
    set({ scenarios: { ...draft.scenarios, [s]: { ...(draft.scenarios[s] ?? {}), [f]: v } } })
  return (
    <Manual title="次日情景推演" hint="不是预测走势，是提前准备不同情景下看什么、怎么应对；四个情景各存各的">
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        {SCENARIOS.map((s) => (
          <div key={s} className="card-elevated p-2.5 space-y-1.5">
            <div className="text-xs font-medium text-text-primary">{SCENARIO_META[s].title}</div>
            {SCENARIO_META[s].fields.map(([f, label]) => (
              <div key={f}>
                <div className="text-[10px] text-text-muted mb-0.5">{label}</div>
                <TextArea value={draft.scenarios[s]?.[f]} onChange={(v) => put(s, f, v)} rows={1} disabled={readOnly} />
              </div>
            ))}
          </div>
        ))}
      </div>
    </Manual>
  )
}

export function PlanPanel({ draft, set, pool, readOnly }: {
  draft: RecapDraft
  set: (p: Partial<RecapDraft>) => void
  pool: WatchItem[]
  readOnly: boolean
}) {
  const plans = draft.plans
  const upd = (i: number, patch: Partial<Plan>) => set({ plans: plans.map((p, j) => (j === i ? { ...p, ...patch } : p)) })
  const leaders = pool.filter((w) => w.deep).length ? pool.filter((w) => w.deep) : pool

  return (
    <Manual title={`次日交易预案（${plans.length}/${MAX_PLANS}）`} hint="入场触发和失效条件都要写成看得见的条件，不写「感觉强就买」；空仓也是有效预案">
      <label className="flex items-center gap-1.5 text-xs text-text-secondary">
        <input type="checkbox" checked={draft.flat_plan} disabled={readOnly || plans.length > 0}
               onChange={(e) => set({ flat_plan: e.target.checked })} />
        空仓预案（明天不开新仓）{plans.length > 0 && <span className="text-text-muted">——有候选股时不能选</span>}
      </label>

      {plans.map((p, i) => (
        <div key={i} className="card-elevated p-2.5 space-y-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] text-text-muted w-4">{i + 1}</span>
            <input value={p.stock_code} disabled={readOnly} placeholder="代码"
                   onChange={(e) => {
                     const code = e.target.value.trim()
                     upd(i, { stock_code: code, stock_name: pool.find((w) => w.code === code)?.name ?? p.stock_name })
                   }} className={cn(INPUT, 'w-20')} />
            <input value={p.stock_name ?? ''} disabled={readOnly} placeholder="名称"
                   onChange={(e) => upd(i, { stock_name: e.target.value })} className={cn(INPUT, 'w-24')} />
            <select value={p.leader_code ?? ''} disabled={readOnly} onChange={(e) => upd(i, { leader_code: e.target.value || null })}
                    className={cn(INPUT, 'w-32')} title="关联的高标">
              <option value="">关联高标（可空）</option>
              {leaders.map((w) => <option key={w.code} value={w.code}>{w.name ?? w.code}</option>)}
            </select>
            <input value={p.sector_name ?? ''} disabled={readOnly} placeholder="对应主线"
                   onChange={(e) => upd(i, { sector_name: e.target.value })} className={cn(INPUT, 'w-28')} />
            <select value={p.scenario ?? ''} disabled={readOnly} onChange={(e) => upd(i, { scenario: (e.target.value || null) as Scenario | null })}
                    className={cn(INPUT, 'w-36')}>
              <option value="">适用情景</option>
              {SCENARIOS.map((s) => <option key={s} value={s}>{SCENARIO_META[s].title}</option>)}
            </select>
            <select value={p.status} disabled={readOnly} onChange={(e) => upd(i, { status: e.target.value as Plan['status'] })}
                    className={cn(INPUT, 'w-20')}>
              {Object.entries(PLAN_STATUS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            {/^\d{6}$/.test(p.stock_code) && (
              <Link to={`/pre-trade-check?code=${p.stock_code}${p.planned_stop != null ? `&planned_stop=${p.planned_stop}` : ''}${p.position_limit != null ? `&position_pct=${p.position_limit}` : ''}`}
                    className="inline-flex items-center gap-0.5 text-[11px] text-accent hover:underline"
                    title="预案本身不是买入许可：交易前仍要过一遍买入检查">
                <ClipboardCheck className="w-3 h-3" />进入买入检查
              </Link>
            )}
            {!readOnly && (
              <button onClick={() => set({ plans: plans.filter((_, j) => j !== i) })} className="ml-auto text-text-muted hover:text-down">
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-1.5">
            <div>
              <div className="text-[10px] text-text-muted mb-0.5">选择理由（市场事实 + 你的判断）</div>
              <TextArea value={p.reason} onChange={(v) => upd(i, { reason: v })} disabled={readOnly} />
            </div>
            <div>
              <div className="text-[10px] text-text-muted mb-0.5">入场触发 <span className="text-down">*</span></div>
              <TextArea value={p.entry_trigger} onChange={(v) => upd(i, { entry_trigger: v })} disabled={readOnly}
                        placeholder="例：高标回封后，它放量突破 12.50 且板块保持领先" />
            </div>
            <div>
              <div className="text-[10px] text-text-muted mb-0.5">失效条件 <span className="text-down">*</span></div>
              <TextArea value={p.invalidation} onChange={(v) => upd(i, { invalidation: v })} disabled={readOnly}
                        placeholder="例：跌破 11.80；高标炸板不回封；10:30 前没触发" />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-[11px] text-text-muted">
            <label className="flex items-center gap-1">止损价
              <input value={p.planned_stop ?? ''} disabled={readOnly} inputMode="decimal"
                     onChange={(e) => upd(i, { planned_stop: numOrNull(e.target.value) })} className={cn(INPUT, 'w-20')} />
            </label>
            <label className="flex items-center gap-1">单笔风险预算
              <input value={p.risk_budget ?? ''} disabled={readOnly} inputMode="decimal"
                     onChange={(e) => upd(i, { risk_budget: numOrNull(e.target.value) })} className={cn(INPUT, 'w-16')} />% 账户
            </label>
            <label className="flex items-center gap-1">仓位上限
              <input value={p.position_limit ?? ''} disabled={readOnly} inputMode="decimal"
                     onChange={(e) => upd(i, { position_limit: numOrNull(e.target.value) })} className={cn(INPUT, 'w-16')} />%
            </label>
            <label className="flex items-center gap-1">观察期限
              <input type="date" value={p.observe_until ?? ''} disabled={readOnly}
                     onChange={(e) => upd(i, { observe_until: e.target.value || null })} className={cn(INPUT, 'w-36')} />
            </label>
          </div>
        </div>
      ))}

      {!readOnly && plans.length < MAX_PLANS && !draft.flat_plan && (
        <div className="flex flex-wrap items-center gap-1.5">
          <button onClick={() => set({ plans: [...plans, emptyPlan()] })}
                  className="inline-flex items-center gap-0.5 px-2 py-1 rounded bg-purple-400/15 text-purple-200 text-xs">
            <Plus className="w-3 h-3" />添加候选股
          </button>
          {pool.filter((w) => !plans.some((p) => p.stock_code === w.code)).slice(0, 8).map((w) => (
            <button key={w.code} onClick={() => set({ plans: [...plans, { ...emptyPlan(), stock_code: w.code, stock_name: w.name }] })}
                    className="px-1.5 py-0.5 rounded bg-bg-elevated text-[11px] text-text-muted hover:text-text-secondary">
              + {w.name ?? w.code}
            </button>
          ))}
        </div>
      )}
    </Manual>
  )
}
