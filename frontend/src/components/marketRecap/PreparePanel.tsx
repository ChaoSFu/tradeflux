import { useQuery } from '@tanstack/react-query'
import { fetchActivations, fetchPrepare, type RecapDraft, type RecapSummary, type WatchItem } from '@/api/marketRecap'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import { EventBadge } from './CorePoolPanel'
import { ATTR_LABEL, INPUT, POSTURE_LABEL, SCENARIO_META } from './shared'

/** 复盘决策摘要：一屏看清今天的结论（姿态是你确认的，不是系统打分给的） */
export function DecisionSummary({ date, draft, set, summary, pool }: {
  date: string
  draft: RecapDraft
  set: (p: Partial<RecapDraft>) => void
  summary: RecapSummary
  pool: WatchItem[]
}) {
  const acts = useQuery({ queryKey: ['recap-activations', date], queryFn: () => fetchActivations(date) })
  const kp = draft.key_points ?? []
  const deep = pool.filter((w) => w.deep)
  const evCount: Record<string, number> = {}
  for (const e of acts.data?.events ?? []) evCount[e.event_type] = (evCount[e.event_type] ?? 0) + 1
  const planDone = draft.flat_plan ? '空仓预案' : `${draft.plans.length} 只，条件写全 ${draft.plans.filter((p) => p.entry_trigger.trim() && p.invalidation.trim()).length} 只`
  return (
    <div className="card p-3 space-y-2">
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-2 text-xs">
        <div className="card-elevated px-2.5 py-1.5">
          <div className="text-[10px] text-text-muted">市场姿态（你的判断）</div>
          <div className="text-sm font-semibold text-purple-200">{draft.market_posture ? POSTURE_LABEL[draft.market_posture] : '未填'}</div>
        </div>
        <div className="card-elevated px-2.5 py-1.5 col-span-1 xl:col-span-2">
          <div className="text-[10px] text-text-muted">核心主线</div>
          <div className="text-text-primary truncate">{draft.sector_picks.filter((p) => p.tier === 'core').map((p) => p.name).join('、') || '—'}</div>
        </div>
        <div className="card-elevated px-2.5 py-1.5">
          <div className="text-[10px] text-text-muted">今日人气核心事件</div>
          <div className="flex flex-wrap gap-0.5 mt-0.5">
            {Object.entries(evCount).map(([t, n]) => <span key={t}><EventBadge t={t} /><span className="text-[10px] text-text-muted">{n}</span></span>)}
            {!Object.keys(evCount).length && <span className="text-text-muted">—</span>}
          </div>
        </div>
        <div className="card-elevated px-2.5 py-1.5">
          <div className="text-[10px] text-text-muted">重点高标 ★（{deep.length}）· 观察 {pool.length}</div>
          <div className="text-text-primary truncate">{deep.map((w) => w.name ?? w.code).join('、') || '—'}</div>
        </div>
        <div className="card-elevated px-2.5 py-1.5">
          <div className="text-[10px] text-text-muted">次日预案</div>
          <div className="text-text-primary">{planDone}</div>
          <div className="text-[10px] text-text-muted">关联 {summary.core.relations} 条 · 已记录响应 {summary.core.responses_judged}</div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[10px] px-1 rounded bg-purple-400/15 text-purple-300">人工</span>
        <span className="text-xs text-text-primary">明天最重要的三个观察点</span>
        {[0, 1, 2].map((i) => (
          <input key={i} value={kp[i] ?? ''} placeholder={`观察点 ${i + 1}`}
                 onChange={(e) => {
                   const n = [...kp]; while (n.length < 3) n.push('')
                   n[i] = e.target.value
                   set({ key_points: n })   // 空的那几条后端会丢掉
                 }}
                 className={cn(INPUT, 'flex-1 min-w-[12rem]')} />
        ))}
      </div>
    </div>
  )
}

/** 次日准备：某个复盘日锁定的预案（最后一份盘前版本），开盘前一屏看完 */
export function PreparePanel({ date }: { date: string }) {
  const { data, isLoading } = useQuery({ queryKey: ['recap-prepare', date], queryFn: () => fetchPrepare(date) })
  if (isLoading) return <LoadingSpinner />
  if (!data || data.status !== 'ok') {
    return <div className="card p-6 text-sm text-text-muted">{data?.message ?? '没有锁定的预案'}——次日准备只看锁定版本，草稿不算。</div>
  }
  const byLeader = data.scenarios?.by_leader ?? {}
  return (
    <div className="space-y-3">
      <div className="card p-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span className="text-text-secondary">{data.recap_date} 锁定 v{data.version}</span>
        <span className="text-text-muted">锁定于 {data.locked_at?.replace('T', ' ').slice(0, 16)}</span>
        {data.backfilled ? <span className="px-1 rounded bg-warn/15 text-warn text-[10px]">事后记录</span>
          : <span className="px-1 rounded bg-up/10 text-up text-[10px]">盘前锁定</span>}
        <span className="text-text-muted">→ 用于 {data.target_trade_date}</span>
        <span>姿态 <b className="text-purple-200">{data.posture ? POSTURE_LABEL[data.posture] : '未填'}</b></span>
        {!!data.invalidation?.length && <span className="text-text-muted">反证：{data.invalidation.join('；')}</span>}
      </div>
      {!!data.key_points?.length && (
        <div className="card p-3 text-xs space-y-0.5">
          <div className="text-[11px] text-text-muted">三个观察点</div>
          {data.key_points.map((k, i) => <div key={i} className="text-text-primary">{i + 1}. {k}</div>)}
        </div>
      )}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
        {(data.focus_leaders ?? []).map((w) => {
          const rels = data.relations?.[w.code] ?? []
          const sc = byLeader[w.code]
          const evs = (data.events ?? []).filter((e) => e.leader_code === w.code)
          return (
            <div key={w.code} className="card p-3 space-y-1.5 text-xs">
              <div className="flex items-center gap-2">
                <span className="text-text-primary font-medium">★ {w.name ?? w.code}</span>
                <span className="text-[10px] text-text-muted">{w.code}</span>
                {evs.map((e) => <EventBadge key={e.event_type} t={e.event_type} />)}
              </div>
              {sc && (['A', 'B', 'C', 'N'] as const).map((s) => {
                const filled = SCENARIO_META[s].fields.filter(([f]) => sc[s]?.[f])
                if (!filled.length) return null
                return (
                  <div key={s} className="text-text-secondary">
                    <span className="text-text-muted">{SCENARIO_META[s].title}：</span>
                    {filled.map(([f, l]) => `${l} ${sc[s][f]}`).join('；')}
                  </div>
                )
              })}
              <div className="text-[11px] text-text-muted">关联（锁定时）{rels.length} 条：
                {rels.slice(0, 12).map((r) => `${r.related_name ?? r.related_code}（${ATTR_LABEL[r.attribute_type]}·${r.relation_basis}）`).join('、') || '—'}
              </div>
            </div>
          )
        })}
        {!(data.focus_leaders ?? []).length && <div className="card p-3 text-xs text-text-muted">锁定时没有 ★ 重点高标</div>}
      </div>
      <div className="card p-3">
        <div className="text-xs font-medium text-text-primary mb-1.5">次日预案（锁定内容，只读）</div>
        {data.flat_plan ? <div className="text-xs text-purple-200">空仓预案</div> : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-text-muted border-b border-bg-border">
                  {['股票', '关联高标', '情景', '观察触发', '入场触发', '失效条件', '止损', '风险%', '仓位%'].map((h) => (
                    <th key={h} className="px-2 py-1 text-left font-medium whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(data.plans ?? []).map((p) => (
                  <tr key={p.stock_code} className="border-b border-bg-border/50 last:border-0 align-top">
                    <td className="px-2 py-1 whitespace-nowrap">{p.stock_name ?? p.stock_code}<span className="ml-1 text-[10px] text-text-muted">{p.stock_code}</span></td>
                    <td className="px-2 py-1 whitespace-nowrap text-text-secondary">{p.leader_code ?? '—'}</td>
                    <td className="px-2 py-1 whitespace-nowrap text-text-muted">{p.scenario ?? '—'}</td>
                    <td className="px-2 py-1 text-text-secondary">{p.watch_trigger ?? '—'}</td>
                    <td className="px-2 py-1 text-text-primary">{p.entry_trigger}</td>
                    <td className="px-2 py-1 text-text-primary">{p.invalidation}</td>
                    <td className="px-2 py-1">{p.planned_stop ?? '—'}</td>
                    <td className="px-2 py-1">{p.risk_budget ?? '—'}</td>
                    <td className="px-2 py-1">{p.position_limit ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="mt-1.5 text-[11px] text-text-muted">高标触发只说明该去看关联池；每只还要满足它自己的入场条件，并过买入检查。</div>
      </div>
    </div>
  )
}
