import { cn } from '@/utils/cn'

export function LoadingSpinner({ className }: { className?: string }) {
  return (
    <div className={cn('flex items-center justify-center p-8', className)}>
      <div className="w-6 h-6 border-2 border-bg-border border-t-accent rounded-full animate-spin" />
    </div>
  )
}

export function LoadingRows({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2">
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="h-8 bg-bg-elevated rounded animate-pulse" />
      ))}
    </div>
  )
}
