import { cn } from '@/utils/cn'

interface StatCardProps {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  icon?: React.ReactNode
  color?: 'default' | 'up' | 'down' | 'warn' | 'dragon' | 'accent'
  className?: string
}

const colorMap = {
  default: 'border-bg-border',
  up: 'border-up/30',
  down: 'border-down/30',
  warn: 'border-warn/30',
  dragon: 'border-dragon/30',
  accent: 'border-accent/30',
}

export function StatCard({ label, value, sub, icon, color = 'default', className }: StatCardProps) {
  return (
    <div className={cn('card p-4 border-l-2', colorMap[color], className)}>
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <p className="label mb-1.5">{label}</p>
          <div className="text-lg font-semibold mono text-text-primary truncate">{value}</div>
          {sub && <div className="mt-1 text-xs text-text-muted">{sub}</div>}
        </div>
        {icon && <div className="text-text-muted shrink-0">{icon}</div>}
      </div>
    </div>
  )
}
