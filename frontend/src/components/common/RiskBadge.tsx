import { RISK_COLORS, RISK_LABELS } from '@/utils/format'
import { cn } from '@/utils/cn'
import type { RiskLevel } from '@/types'

interface RiskBadgeProps {
  level: RiskLevel
  className?: string
}

const bgMap: Record<RiskLevel, string> = {
  low: 'bg-up-dim',
  medium: 'bg-warn-dim',
  high: 'bg-down-dim',
}

export function RiskBadge({ level, className }: RiskBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center px-1.5 py-0.5 rounded text-xs font-mono font-medium',
        bgMap[level],
        RISK_COLORS[level],
        className
      )}
    >
      {RISK_LABELS[level] ?? level}
    </span>
  )
}
