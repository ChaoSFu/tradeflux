import { cn } from '@/utils/cn'

interface ProgressProps {
  value: number        // 0–100
  max?: number
  className?: string
  color?: string
  showLabel?: boolean
}

export function Progress({ value, max = 100, className, color, showLabel }: ProgressProps) {
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  const barColor = color ?? (pct >= 70 ? '#26C281' : pct >= 40 ? '#F59E0B' : '#FF4560')

  return (
    <div className={cn('flex items-center gap-2', className)}>
      <div className="flex-1 h-1.5 bg-bg-elevated rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-300"
          style={{ width: `${pct}%`, backgroundColor: barColor }}
        />
      </div>
      {showLabel && (
        <span className="text-xs font-mono text-text-muted w-8 text-right">{Math.round(pct)}</span>
      )}
    </div>
  )
}
