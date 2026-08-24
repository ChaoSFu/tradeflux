import { Badge } from '@/components/ui/badge'
import type { W2SState } from '@/types'

// BUYABLE/BLOCK 是"能不能买"的红绿灯语义（安全=绿/危险=红），不是价格涨跌
// 方向，用 safe/danger 而不是 up/down——此前误用 up/down（A股涨=红/跌=绿），
// 导致 BUYABLE 渲染成红色、BLOCK 渲染成绿色，字面意思完全反了（2026-08-24修复）。
const STATE_BADGE: Record<W2SState, 'safe' | 'danger' | 'warn' | 'accent' | 'muted'> = {
  BUYABLE: 'safe',
  CONFIRMING: 'warn',
  REPAIRING: 'warn',
  READY: 'accent',
  WATCH: 'muted',
  WAIT: 'muted',
  BLOCK: 'danger',
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
