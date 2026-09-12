import { cn } from '@/utils/cn'
import type { PlanOption } from '@/api/preTradeCheck'

/**
 * 一组点选按钮（v3）。顺序固定、不随推荐变——位置不动才点得快。
 * 「✓事实」= 系统核对过有事实支持；「✗冲突」= 跟事实矛盾（仍可选，检查时会判出来）；灰掉 = 现在不成立。
 * 系统只标、不替你选：选不选永远是你点的。选了「其他 / 自定义」才出现文字框。
 */
export function ChipGroup({ title, hint, options, max, selected, onChange, otherCode = 'OTHER',
                            otherValue, onOtherChange, otherPlaceholder, inputCls }: {
  title?: string
  hint?: string
  options: PlanOption[]
  max: number
  selected: string[]
  onChange: (codes: string[]) => void
  otherCode?: string
  otherValue: string
  onOtherChange: (v: string) => void
  otherPlaceholder?: string
  inputCls: string
}) {
  const single = max === 1
  const toggle = (code: string) => {
    if (selected.includes(code)) onChange(selected.filter((c) => c !== code))
    else if (single) onChange([code])
    else if (selected.length < max) onChange([...selected, code])
  }
  return (
    <div className="space-y-1.5">
      {title && (
        <div className="flex flex-wrap items-baseline gap-2">
          <span className="text-[11px] font-medium text-text-secondary">{title}</span>
          {hint && <span className="text-[10px] text-text-muted">{hint}</span>}
        </div>
      )}
      <div className="flex flex-wrap gap-1.5">
        {options.map((o) => {
          const on = selected.includes(o.code)
          const conflict = o.available && !!o.conflict_reason
          const full = !on && !single && selected.length >= max
          const tip = [o.desc,
            o.suggestion_reason && `✓ 事实支持：${o.suggestion_reason}`,
            o.conflict_reason && (o.available ? `✗ 跟事实冲突：${o.conflict_reason}` : `现在不成立：${o.conflict_reason}`),
            o.fact && `系统看到：${o.fact}`,
            full && `最多选 ${max} 个——先取消一个`].filter(Boolean).join('\n')
          return (
            // 可访问名称用短标签（+ 参考价 / 状态），长解释只放 title 做悬停提示——否则读屏会整段念说明
            <button key={o.code} type="button" title={tip} disabled={!o.available || full} onClick={() => toggle(o.code)}
                    aria-pressed={on}
                    aria-label={[o.label, o.ref_price != null ? String(o.ref_price) : '', o.suggested ? '有事实支持' : '',
                                 conflict ? '跟事实冲突' : ''].filter(Boolean).join(' ')}
                    className={cn('inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs transition-colors',
                      'disabled:cursor-not-allowed disabled:opacity-35',
                      on ? (conflict ? 'border-danger/60 bg-danger/15 text-danger' : 'border-accent/60 bg-accent/20 text-accent')
                        : conflict ? 'border-danger/25 text-text-muted hover:border-danger/50'
                          : o.suggested ? 'border-accent/45 text-text-primary hover:bg-accent/10'
                            : 'border-bg-border text-text-secondary hover:border-accent/40 hover:text-text-primary')}>
              {o.label}
              {o.ref_price != null && <span className="font-mono text-[10px] opacity-80">{o.ref_price}</span>}
              {o.suggested && <span className="text-[9px] text-accent">✓事实</span>}
              {conflict && <span className="text-[9px] text-danger">✗冲突</span>}
            </button>
          )
        })}
      </div>
      {selected.includes(otherCode) && (
        <input autoFocus value={otherValue} placeholder={otherPlaceholder ?? '写一句是什么'} className={inputCls}
               onChange={(e) => onOtherChange(e.target.value)} />
      )}
    </div>
  )
}
