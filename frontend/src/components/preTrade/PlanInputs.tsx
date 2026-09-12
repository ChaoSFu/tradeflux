import type { ManualAnswers, PreTradeContext } from '@/api/preTradeCheck'
import { ChipGroup } from './ChipGroup'

/**
 * 你的计划（pretrade_v3）：点选稳定的理由代码，不再临盘写作文。只有选了「其他 / 自定义」才打字。
 *
 * 系统按事实给候选项标「✓事实 / ✗冲突」，但**从不替你勾选**——你以为你在做什么，
 * 检查时会跟当时的事实对一遍。历史复盘时这里是事后补选的，跟当时交易记录里的理由分开放。
 */
export function PlanInputs({ ctx, answers, onChange, historical, code, inputCls }: {
  ctx: PreTradeContext
  answers: ManualAnswers
  onChange: (a: ManualAnswers) => void
  historical: boolean
  code: string
  inputCls: string
}) {
  const po = ctx.plan_options
  if (!po) return null
  const je = ctx.journal_entry && ctx.journal_entry.stock_code === code ? ctx.journal_entry : null
  const set = (patch: Partial<ManualAnswers>) => onChange({ ...answers, ...patch })
  return (
    <div className="card p-4 space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs font-semibold text-text-primary">
          你的计划
          {historical && <span className="ml-2 font-normal text-warn">复盘时补选——不代表当时的想法</span>}
        </span>
        <span className="text-[11px] text-text-muted">点选即可，不用打字 · 「✓事实」只是系统核对过有事实支持，选不选由你</span>
      </div>
      {je && (
        <div className="rounded border border-bg-border/60 px-3 py-2 text-xs text-text-secondary">
          <span className="text-text-muted">当时交易记录里写的（{je.trade_time.slice(5, 16).replace('T', ' ')} {je.action}）：</span>
          {je.reason ? `「${je.reason}」` : '没写理由'}
          {je.emotion_tag && <span className="ml-2 text-text-muted">情绪：{je.emotion_tag}</span>}
        </div>
      )}
      <ChipGroup title="为什么是这个板块" hint={`选 1～${po.sector.max} 个`} options={po.sector.options} max={po.sector.max}
                 selected={answers.sector_reason_codes} onChange={(v) => set({ sector_reason_codes: v })}
                 otherValue={answers.sector_reason_other} onOtherChange={(v) => set({ sector_reason_other: v })}
                 inputCls={inputCls} />
      <ChipGroup title="为什么是这只" hint={`选 1～${po.stock.max} 个`} options={po.stock.options} max={po.stock.max}
                 selected={answers.stock_reason_codes} onChange={(v) => set({ stock_reason_codes: v })}
                 otherValue={answers.stock_reason_other} onOtherChange={(v) => set({ stock_reason_other: v })}
                 inputCls={inputCls} />
      <ChipGroup title="为什么是现在" hint="选 1 个：真正触发这一笔的事件" options={po.trigger.options} max={1}
                 selected={answers.entry_trigger_code ? [answers.entry_trigger_code] : []}
                 onChange={(v) => set({ entry_trigger_code: v[0] ?? null })}
                 otherValue={answers.entry_trigger_other} onOtherChange={(v) => set({ entry_trigger_other: v })}
                 inputCls={inputCls} />
      <ChipGroup title="什么情况说明我错了" hint={`选 1～${po.invalidation.max} 个 · 价位类没填失效价时直接当失效位算风险`}
                 options={po.invalidation.options} max={po.invalidation.max} otherCode="CUSTOM"
                 selected={answers.invalidation_codes} onChange={(v) => set({ invalidation_codes: v })}
                 otherValue={answers.invalidation_other} onOtherChange={(v) => set({ invalidation_other: v })}
                 inputCls={inputCls} />
    </div>
  )
}
