import { cn } from '@/utils/cn'
import type { AdvanceLadderResponse } from '@/api/stocks'

const NUM = 'font-mono tabular-nums'
const TH = 'px-2 py-1 text-left font-medium text-text-muted whitespace-nowrap border-b border-bg-border'
const TD = 'px-2 py-1 whitespace-nowrap'

/**
 * 分板位晋级。**只有晋级率，没有接力分。**
 *
 * 三个数字必须分开摆：晋级 / 断板 / 今天没有这只票的行。把第三种并进断板，
 * 停牌和退市就会被算成断板，晋级率的分母也跟着虚高——而输出上完全看不出来。
 */
export function AdvanceLadderPanel({ data }: { data: AdvanceLadderResponse }) {
  const rows = data.rows
  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <span className="text-xs font-semibold text-text-primary">分板位晋级</span>
        <span className="text-[10px] text-text-muted">
          {data.prev_date && data.trade_date
            ? `${data.prev_date} → ${data.trade_date}`
            : <span className="text-warn">拿不到前一个交易日</span>}
        </span>
      </div>
      {rows.length === 0 ? (
        <div className="text-xs text-text-muted py-2">
          {data.notes[0] ?? '没有可统计的板位'}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs" style={{ minWidth: 460 }}>
            <thead><tr>
              {['晋级', '晋级率', '晋级 / 观测', '断板', '未知'].map((h) => (
                <th key={h} className={TH}>{h}</th>))}
            </tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.from_board} className="border-b border-bg-border/40 last:border-0">
                  <td className={cn(TD, 'text-text-primary')}>
                    {r.from_board} → <span className="text-up">{r.to_board}</span> 板
                  </td>
                  {/* 观测数为 0 时是 —，不是 0% */}
                  <td className={cn(TD, NUM,
                    r.advance_ratio === null ? 'text-text-muted/50'
                      : r.advance_ratio >= 0.5 ? 'text-up' : 'text-text-secondary')}>
                    {r.advance_ratio === null ? '—' : `${(r.advance_ratio * 100).toFixed(0)}%`}
                  </td>
                  <td className={cn(TD, NUM, 'text-text-secondary')}>
                    {r.advanced_count}/{r.observed_count}
                  </td>
                  <td className={cn(TD, NUM, 'text-down')}>{r.broken_count}</td>
                  <td className={cn(TD, NUM,
                    r.unknown_count ? 'text-warn/80' : 'text-text-muted/50')}
                      title="今天没有这只票的快照：停牌 / 退市 / 未抓到。既不是晋级也不是断板">
                    {r.unknown_count || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ul className="text-[10px] text-text-muted space-y-0.5">
        <li>晋级率的分母是<span className="text-text-secondary">今天有快照的那些</span>，
          不是昨天的总数。两个数都在表里，想换口径自己算得出来。</li>
        {data.notes.map((n, i) => <li key={i}>{n}</li>)}
      </ul>
    </div>
  )
}
