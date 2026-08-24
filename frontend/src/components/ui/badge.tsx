import { cn } from '@/utils/cn'

interface BadgeProps {
  children: React.ReactNode
  variant?: 'default' | 'up' | 'down' | 'safe' | 'danger' | 'warn' | 'dragon' | 'accent' | 'muted'
  className?: string
}

export function Badge({ children, variant = 'default', className }: BadgeProps) {
  const variants = {
    default: 'bg-bg-elevated text-text-secondary border border-bg-border',
    up: 'bg-up-dim text-up',
    down: 'bg-down-dim text-down',
    // 红绿灯/通过-拦截语义（安全=绿/危险=红），跟上面up/down的价格涨跌方向语义
    // 是两回事，不要混用（2026-08-24新增，修复此前BUYABLE/BLOCK误用up/down
    // 导致"拦截"渲染成绿色的真实bug）
    safe: 'bg-safe-dim text-safe',
    danger: 'bg-danger-dim text-danger',
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
