/**
 * 一个 gate 的展示：结论 + 事实 + **判不出来的部分**。
 *
 * `unknowns` 单独一块、用警示色，是刻意的：人看到「主线 等待」时必须能立刻
 * 分清那是"板块不够强"还是"板块趋势我们根本算不了"。这两件事对下一步动作的
 * 含义完全不同，混在一个灰色的"等待"里等于没说。
 */
import { AlertTriangle } from 'lucide-react'
import { cn } from '@/utils/cn'
import { gateText, gateTone, type GateResult } from '../domain/gate'

export function GateCard({ name, gate, compact }: {
  name: string; gate: GateResult; compact?: boolean
}) {
  return (
    <div className="card p-3">
      <div className="flex items-baseline gap-2">
        <span className="text-sm text-text-primary">{name}</span>
        <span className={cn('text-sm font-medium', gateTone(gate.status))}>
          {gateText(gate.status)}
        </span>
        <span className="text-[11px] text-text-secondary ml-auto">{gate.label}</span>
      </div>
      {!compact && gate.evidence.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-[11px] text-text-secondary">
          {gate.evidence.map((e) => <li key={e}>· {e}</li>)}
        </ul>
      )}
      {gate.unknowns.length > 0 && (
        <div className="mt-2 pt-2 border-t border-bg-border flex items-start gap-1
                        text-[11px] text-warn">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
          <div>
            {gate.unknowns.map((u) => <div key={u}>{u}</div>)}
            <div className="text-text-muted/80 mt-0.5">
              这是「判不出来」，不是「不满足」——缺数据不能当成否定。
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
