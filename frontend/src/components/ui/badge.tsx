import { cn } from '@/utils/cn'

interface BadgeProps {
  children: React.ReactNode
  variant?: 'default' | 'up' | 'down' | 'warn' | 'dragon' | 'accent' | 'muted'
  className?: string
}

export function Badge({ children, variant = 'default', className }: BadgeProps) {
  const variants = {
    default: 'bg-bg-elevated text-text-secondary border border-bg-border',
    up: 'bg-up-dim text-up',
    down: 'bg-down-dim text-down',
    warn: 'bg-warn-dim text-warn',
    dragon: 'bg-dragon-dim text-dragon',
    accent: 'bg-accent-dim text-accent',
    muted: 'bg-bg-elevated text-text-muted',
  }
  return (
    <span
      className={cn(
        'inline-flex items-center px-1.5 py-0.5 rounded text-xs font-mono font-medium',
        variants[variant],
        className
      )}
    >
      {children}
    </span>
  )
}
