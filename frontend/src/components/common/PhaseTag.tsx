import { PHASE_COLORS, PHASE_LABELS_ZH, PHASE_NAME_TO_NUM } from '@/utils/format'
import { cn } from '@/utils/cn'

interface PhaseTagProps {
  phase: number | string | null
  className?: string
}

export function PhaseTag({ phase, className }: PhaseTagProps) {
  if (phase === null || phase === undefined) return <span className="text-text-muted text-xs">--</span>

  const phaseNum = typeof phase === 'string' ? (PHASE_NAME_TO_NUM[phase] ?? 0) : phase
  const color = PHASE_COLORS[phaseNum] ?? '#505570'
  const label = PHASE_LABELS_ZH[phaseNum] ?? '未知'

  return (
    <span
      className={cn('inline-flex items-center px-1.5 py-0.5 rounded text-xs font-mono font-medium', className)}
      style={{ color, backgroundColor: `${color}22`, border: `1px solid ${color}44` }}
    >
      {label}
    </span>
  )
}
