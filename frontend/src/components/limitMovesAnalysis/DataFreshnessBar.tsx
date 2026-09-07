import { AlertTriangle, Clock } from 'lucide-react'
import { cn } from '@/utils/cn'

export interface SourceStatus {
  /** 模块名，如「涨跌停总览」 */
  label: string
  /** 这份数据是哪个交易日的。null = 接口没给，**不猜** */
  tradeDate: string | null
  /** 真实刷新时刻。只有涨停板块雷达有；其余为 undefined = 这个源没有这个概念 */
  refreshedAt?: string | null
  source: string
  /** 额外说明，例如历史指标落后几天 */
  note?: string | null
  /** false = 盘中值，不是收盘终值；null/undefined = 不知道 */
  settled?: boolean | null
  error?: boolean
}

const dt = (s?: string | null) => (s ? s.replace('T', ' ').slice(0, 16) : null)

/**
 * 数据新鲜度条。
 *
 * **不同模块来自不同表、不同更新时刻，页面把它们并排摆出来的那一刻，就在暗示
 * 它们是同一个时点的事实。** 这一条的唯一职责是把那个暗示拆掉。
 *
 * 能拿到什么就显示什么：
 *   涨跌停总览      trade_date + is_settled（本轮新加，之前接口压根不返回）
 *   涨停板块雷达    trade_date + refreshed_at + source + history_as_of
 *   市场高度        序列最后一个点的日期
 *   市场效应        trade_date（没有刷新时刻这个概念）
 * 拿不到的一律写「更新时间未知」，**不编一个**。
 */
export function DataFreshnessBar({ sources }: { sources: SourceStatus[] }) {
  const dates = sources.map((s) => s.tradeDate).filter(Boolean) as string[]
  const newest = dates.length ? dates.slice().sort().at(-1)! : null
  const lagging = sources.filter((s) => s.tradeDate && newest && s.tradeDate < newest)

  return (
    <div className="card p-2.5">
      <div className="flex items-center gap-1.5 text-[11px] text-text-secondary font-medium mb-1.5">
        <Clock className="w-3.5 h-3.5 text-text-muted" />
        数据状态
        {newest && <span className="text-text-muted font-normal">最新 {newest}</span>}
      </div>

      <div className="grid gap-x-4 gap-y-1 sm:grid-cols-2 lg:grid-cols-4">
        {sources.map((s) => {
          const behind = !!(s.tradeDate && newest && s.tradeDate < newest)
          return (
            <div key={s.label} className="text-[11px] leading-relaxed">
              <span className={cn('font-medium',
                s.error ? 'text-warn' : behind ? 'text-warn' : 'text-text-primary')}>
                {s.label}
              </span>
              <span className="text-text-muted ml-1.5">
                {s.error ? '获取失败'
                  : s.tradeDate ?? <span className="text-warn">日期未知</span>}
              </span>
              {s.settled === false && (
                <span className="text-warn ml-1.5" title="快照里还有盘中值，不是收盘终值">
                  盘中
                </span>
              )}
              <div className="text-text-muted/70">
                {/* 拿不到刷新时刻就直说。这四个源里只有涨停板块雷达有 */}
                {dt(s.refreshedAt) ?? '更新时间未知'}
                <span className="ml-1.5">· {s.source}</span>
              </div>
              {s.note && <div className="text-warn/80">{s.note}</div>}
            </div>
          )
        })}
      </div>

      {/* **日期不齐必须说出来。** 不说的话，看的人会默认整页是同一天 */}
      {lagging.length > 0 && (
        <div className="flex items-start gap-1.5 mt-1.5 pt-1.5 border-t border-bg-border
                        text-[11px] text-warn">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
          <span>
            {lagging.map((s) => `${s.label}截至 ${s.tradeDate}`).join('、')}
            ，比其余模块旧。这一页不是同一个时点的事实。
          </span>
        </div>
      )}
    </div>
  )
}
