import { cn } from '@/utils/cn'
import type { ManualAnswers, PreTradeContext } from '@/api/preTradeCheck'

/**
 * 你的计划（pretrade_v2）：三句理由 + 失效条件。一句话写不出来，就是还没想清楚——没写全到不了 READY。
 *
 * 失效 ≠ 止损价：短线的失效常常是结构 / 板块 / 时间。选了价格就用上面的「计划失效价」。
 * 历史复盘时这里写的是事后补的，跟当时交易记录里的理由分开放——事后的解释不能冒充当时的想法。
 */
export function PlanInputs({ ctx, answers, onChange, historical, code, inputCls }: {
  ctx: PreTradeContext
  answers: ManualAnswers
  onChange: (a: ManualAnswers) => void
  historical: boolean
  code: string
  inputCls: string
}) {
  const je = ctx.journal_entry && ctx.journal_entry.stock_code === code ? ctx.journal_entry : null
  const type = ctx.invalidation_types.find((t) => t.key === answers.invalidation_type)
  return (
    <div className="card p-4 space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs font-semibold text-text-primary">
          你的计划
          {historical && <span className="ml-2 font-normal text-warn">复盘时补写——不代表当时的想法</span>}
        </span>
        <span className="text-[11px] text-text-muted">三句理由和失效条件都要写——没写全到不了 READY</span>
      </div>
      {je && (
        <div className="rounded border border-bg-border/60 px-3 py-2 text-xs text-text-secondary">
          <span className="text-text-muted">当时交易记录里写的（{je.trade_time.slice(5, 16).replace('T', ' ')} {je.action}）：</span>
          {je.reason ? `「${je.reason}」` : '没写理由'}
          {je.emotion_tag && <span className="ml-2 text-text-muted">情绪：{je.emotion_tag}</span>}
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-3">
        {ctx.reason_fields.map((f) => (
          <label key={f.key} className="block space-y-1">
            <span className="text-[11px] text-text-muted">{f.label}</span>
            <input value={answers[f.key]} placeholder={f.placeholder} className={inputCls}
                   onChange={(e) => onChange({ ...answers, [f.key]: e.target.value })} />
          </label>
        ))}
      </div>
      <div className="space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-text-muted">什么事实出现代表我错了？</span>
          {ctx.invalidation_types.map((t) => (
            <button key={t.key} type="button" onClick={() => onChange({ ...answers, invalidation_type: t.key })}
                    className={cn('rounded border px-2 py-0.5 text-xs',
                      answers.invalidation_type === t.key ? 'border-accent/50 bg-accent/15 text-accent'
                        : 'border-bg-border text-text-muted hover:text-text-secondary')}>
              {t.label}
            </button>
          ))}
        </div>
        <input value={answers.invalidation_text} disabled={!answers.invalidation_type} className={inputCls}
               placeholder={answers.invalidation_type === 'price'
                 ? '价格失效用上面的「计划失效价」；这里可以补一句为什么是这个价（可选）'
                 : type?.hint ?? '先选失效方式'}
               onChange={(e) => onChange({ ...answers, invalidation_text: e.target.value })} />
      </div>
    </div>
  )
}
