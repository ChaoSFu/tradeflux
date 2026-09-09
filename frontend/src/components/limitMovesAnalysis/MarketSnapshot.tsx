import { cn } from '@/utils/cn'

export interface Kpi {
  label: string
  /** null = 不知道。**不要传 0 代替** */
  value: number | string | null
  suffix?: string
  hint?: string
  tone?: 'up' | 'down' | 'neutral'
}

const TONE = { up: 'text-up', down: 'text-down', neutral: 'text-text-primary' }

/**
 * 今日市场事实的 KPI 行。**只摆事实，不合成任何分数。**
 *
 * `value === null` 渲染成 —，不是 0：「今天没有跌停」和「跌停数据没拿到」
 * 在盘面上是两件相反的事。
 */
export function MarketSnapshot({ items }: { items: Kpi[] }) {
  return (
    <div className="grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-10 gap-px
                    bg-bg-border rounded overflow-hidden">
      {items.map((k) => (
        <div key={k.label} className="bg-bg-elevated px-2.5 py-2" title={k.hint}>
          <div className="text-[10px] text-text-muted whitespace-nowrap">{k.label}</div>
          <div className="mt-0.5 flex items-baseline gap-0.5">
            <span className={cn('text-lg font-mono font-semibold tabular-nums',
              k.value === null ? 'text-text-muted/50' : TONE[k.tone ?? 'neutral'])}>
              {k.value === null ? '—' : k.value}
            </span>
            {k.suffix && <span className="text-[10px] text-text-muted">{k.suffix}</span>}
          </div>
        </div>
      ))}
    </div>
  )
}
