import { cn } from '@/utils/cn'
import type { CheckModule } from '@/api/preTradeCheck'
import { LevelIcon } from './levels'

const STATUS: Record<CheckModule['status'], { zh: string; cls: string }> = {
  PASS: { zh: '通过', cls: 'border-accent/40 text-accent' },
  WARN: { zh: '警示', cls: 'border-warn/40 text-warn' },
  FAIL: { zh: '否决', cls: 'border-danger/40 text-danger' },
  UNKNOWN: { zh: '未知', cls: 'border-bg-border text-text-muted' },
  NEUTRAL: { zh: '背景', cls: 'border-bg-border text-text-muted' },
}

/** 按规则链的顺序逐个模块摆证据：市场 → 主线 → 个股 → 结构 → 时间 → 行为 → 人工 → 风险 */
export function CheckModules({ modules }: { modules: CheckModule[] }) {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {modules.map((m, idx) => (
        <div key={m.key} className="card p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-text-primary">
              <span className="mr-1 font-mono text-text-muted">{idx + 1}</span>{m.title}
            </span>
            <span className="flex items-center gap-2 text-[10px] text-text-muted">
              依据 {m.known} 项{m.unknown > 0 && ` · 未知 ${m.unknown}`}
              <span className={cn('rounded border px-1.5 leading-4', STATUS[m.status].cls)}>
                {STATUS[m.status].zh}
              </span>
            </span>
          </div>
          <div className="space-y-1">
            {m.items.map((i, n) => (
              <div key={`${i.key}-${n}`}
                   className={cn('flex gap-1.5 text-xs',
                     i.level === 'INFO' || i.level === 'UNKNOWN' ? 'text-text-muted' : 'text-text-secondary')}>
                <LevelIcon level={i.level} />
                <span>{i.text}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
