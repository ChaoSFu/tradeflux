import { Check, X, Clock, Info } from 'lucide-react'
import { cn } from '@/utils/cn'
import type { W2SChecklistGroup } from '@/types'

const GROUP_LABEL: Record<W2SChecklistGroup['group'], string> = {
  MARKET: '大盘闸门',
  SECTOR: '板块闸门',
  LEADER: '龙头闸门',
  DIVERGENCE: '分歧健康度',
  SETUP: '结构确认',
  SPACE: '空间闸门',
  CHIPS: '筹码/获利盘',
  RISK: '风险收益比',
}

/** 8组闸门检查结果：SPACE/CHIPS/RISK 在 Phase 1 一律显示灰色"Phase 2"标签，
 *  绝不伪造 ✓ ——贯彻"弱转强成立≠值得买入"，宁可看起来不完整，不能造假。
 *  advisory（2026-08-23新增）：该组在真实闸门逻辑里从不硬拦截/软上限（比如
 *  RISK/Stress R/R、LEADER=undetermined），只是供参考，用琥珀色跟红色fail
 *  区分开，避免"候选已经BUYABLE，Checklist却显示某组fail"这种自相矛盾。
 *  pass/fail 用 safe/danger（红绿灯语义）不是 up/down（A股价格涨跌方向）——
 *  这两套红绿含义刚好相反，此前误用 up/down 导致 ✓ 通过项渲染成红色、✗ 拦截项
 *  渲染成绿色，跟checkmark/X这种全球通用符号的含义直接矛盾（2026-08-24修复）。 */
export function ChecklistPanel({ checklist }: { checklist: W2SChecklistGroup[] }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
      {checklist.map((item) => (
        <div
          key={item.group}
          className={cn(
            'flex items-start gap-2 rounded px-3 py-2 border text-xs',
            item.status === 'pass' && 'bg-safe-dim border-safe/20',
            item.status === 'fail' && 'bg-danger-dim border-danger/20',
            item.status === 'advisory' && 'bg-warn-dim border-warn/20',
            item.status === 'phase2' && 'bg-bg-elevated border-bg-border text-text-muted',
          )}
        >
          <div className="mt-0.5 shrink-0">
            {item.status === 'pass' && <Check className="w-3.5 h-3.5 text-safe" />}
            {item.status === 'fail' && <X className="w-3.5 h-3.5 text-danger" />}
            {item.status === 'advisory' && <Info className="w-3.5 h-3.5 text-warn" />}
            {item.status === 'phase2' && <Clock className="w-3.5 h-3.5 text-text-muted" />}
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className={cn(
                'font-semibold',
                item.status === 'pass' && 'text-safe',
                item.status === 'fail' && 'text-danger',
                item.status === 'advisory' && 'text-warn',
                item.status === 'phase2' && 'text-text-muted',
              )}>
                {GROUP_LABEL[item.group] ?? item.group}
              </span>
              {item.status === 'phase2' && (
                <span className="px-1 py-0.5 rounded bg-bg-border/60 text-[10px] text-text-muted">Phase 2</span>
              )}
              {item.status === 'advisory' && (
                <span className="px-1 py-0.5 rounded bg-warn/10 text-[10px] text-warn">仅供参考</span>
              )}
            </div>
            <div className="text-text-secondary mt-0.5 break-words">{item.detail}</div>
          </div>
        </div>
      ))}
    </div>
  )
}
