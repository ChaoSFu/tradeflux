import { STOCK_PHASE_COLORS, STOCK_PHASE_LABELS } from '@/utils/format'
import { cn } from '@/utils/cn'

interface StockPhaseTagProps {
  phase: string | null
  className?: string
}

/**
 * 展示股票阶段标签：破位龙头 / 走弱龙头。
 * "normal" 阶段不显示标签（返回 null）。
 */
export function StockPhaseTag({ phase, className }: StockPhaseTagProps) {
  if (!phase || phase === 'normal') return null

  const color = STOCK_PHASE_COLORS[phase] ?? '#505570'
  const label = STOCK_PHASE_LABELS[phase] ?? phase

  return (
    <span
      className={cn('inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium', className)}
      style={{ color, backgroundColor: `${color}22`, border: `1px solid ${color}44` }}
    >
      {label}
    </span>
  )
}
