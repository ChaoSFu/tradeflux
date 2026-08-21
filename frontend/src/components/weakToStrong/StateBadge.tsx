import { Badge } from '@/components/ui/badge'
import type { W2SState } from '@/types'

const STATE_BADGE: Record<W2SState, 'up' | 'down' | 'warn' | 'accent' | 'muted'> = {
  BUYABLE: 'up',
  CONFIRMING: 'warn',
  REPAIRING: 'warn',
  READY: 'accent',
  WATCH: 'muted',
  WAIT: 'muted',
  BLOCK: 'down',
}

const STATE_LABEL: Record<W2SState, string> = {
  WATCH: '观察',
  READY: '竞价达标',
  REPAIRING: '修复中',
  CONFIRMING: '确认中',
  BUYABLE: '结构确认',
  WAIT: '等待',
  BLOCK: '拦截',
}

export function StateBadge({ state }: { state: W2SState }) {
  return <Badge variant={STATE_BADGE[state] ?? 'muted'}>{STATE_LABEL[state] ?? state}</Badge>
}
