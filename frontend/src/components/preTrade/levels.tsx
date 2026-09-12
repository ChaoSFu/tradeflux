import { AlertTriangle, CheckCircle2, HelpCircle, Info, XCircle } from 'lucide-react'
import { cn } from '@/utils/cn'
import type { Level, Quality } from '@/api/preTradeCheck'

/**
 * 判定等级的展示口径——整页只有这一份。
 * 否决用 danger（不是 up：up/down 只表示价格方向，见 tailwind.config）；
 * 通过用 accent 而不用 safe 绿——READY 只是「没发现冲突」，不该长得像绿灯放行。
 */
export function LevelIcon({ level, className }: { level: Level; className?: string }) {
  const cls = cn('w-3.5 h-3.5 shrink-0 mt-0.5', className)
  switch (level) {
    case 'PASS': return <CheckCircle2 className={cn(cls, 'text-accent')} />
    case 'WARN': return <AlertTriangle className={cn(cls, 'text-warn')} />
    case 'FAIL': return <XCircle className={cn(cls, 'text-danger')} />
    case 'UNKNOWN': return <HelpCircle className={cn(cls, 'text-text-muted')} />
    default: return <Info className={cn(cls, 'text-text-muted/70')} />
  }
}

const Q_STYLE: Record<string, string> = {
  EXACT: 'border-accent/40 text-accent',
  APPROX: 'border-warn/30 text-warn/80',
  STALE: 'border-warn/50 text-warn',
  UNKNOWN: 'border-bg-border text-text-muted',
}
const Q_ZH: Record<string, string> = { EXACT: '精确', APPROX: '近似', STALE: '过时', UNKNOWN: '未知' }

export function QualityBadge({ q }: { q: Quality | string }) {
  return (
    <span className={cn('rounded border px-1 text-[10px] leading-4', Q_STYLE[q] ?? Q_STYLE.UNKNOWN)}
          title={q}>
      {Q_ZH[q] ?? q}
    </span>
  )
}
