import { cn } from '@/utils/cn'
import type { HeightPoint } from '@/api/marketTrend'

/**
 * 今日连板梯队。数据直接用 speculation-radar 的最后一个点——**不在前端重算
 * "谁是几板"**，那是后端的口径。
 *
 * `ladderCount` 跟 `limitUpCount` 不一致时如实并列：梯队按 board_count>0 统计，
 * 涨停数按 is_limit_up 统计，两个口径本来就可能差几只。
 */
export function BoardLadderPanel({ point }: { point: HeightPoint | null }) {
  if (!point) {
    return <div className="text-xs text-text-muted py-6 text-center">暂无梯队数据</div>
  }
  const rank = (k: string) => parseInt(k) + (k.endsWith('+') ? 0.5 : 0)
  const levels = Object.keys(point.ladder).sort((a, b) => rank(b) - rank(a))
  const max = Math.max(1, ...levels.map((lv) => point.ladder[lv] ?? 0))

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <span className="text-xs font-semibold text-text-primary">今日连板梯队</span>
        <span className="text-[10px] text-text-muted">
          {point.date} · 覆盖 {point.ladder_count} 只
          {point.limit_up_count !== point.ladder_count && (
            <span className="text-warn ml-1"
                  title="梯队按 board_count>0 统计，涨停数按 is_limit_up 统计">
              · 涨停 {point.limit_up_count}，差 {point.limit_up_count - point.ladder_count} 只无连板数据
            </span>
          )}
        </span>
      </div>
      <div className="space-y-1">
        {levels.map((lv) => {
          const n = point.ladder[lv] ?? 0
          const high = rank(lv) >= 3
          return (
            <div key={lv} className="flex items-center gap-2">
              <span className={cn('text-[11px] font-mono tabular-nums w-8 text-right shrink-0',
                high ? 'text-text-primary' : 'text-text-muted')}>{lv}板</span>
              <div className="flex-1 h-3.5 bg-bg-elevated rounded-sm overflow-hidden">
                <div className="h-full rounded-sm"
                     style={{ width: `${(n / max) * 100}%`,
                              background: high ? '#FF4560' : 'rgba(255,69,96,0.45)' }} />
              </div>
              <span className="text-[11px] font-mono tabular-nums w-8 text-text-secondary">
                {n}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
