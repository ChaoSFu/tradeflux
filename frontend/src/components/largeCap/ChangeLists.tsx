import { AlertTriangle } from 'lucide-react'
import type { LargeCapCrossEvent, LargeCapEvents, LargeCapStock } from '@/api/largeCap'
import { Card } from '@/components/ui/card'
import { cn } from '@/utils/cn'
import { NUM, StockLink, fmtAmt, fmtCap, fmtNum, fmtPct, pctCls } from './shared'

function CrossTable({ rows }: { rows: LargeCapCrossEvent[] }) {
  if (!rows.length) return <div className="text-xs text-text-muted py-3">无</div>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-bg-border text-text-muted">
            <th className="px-1.5 py-1 text-left font-medium">股票</th>
            <th className="px-1.5 py-1 text-right font-medium">昨日→今日市值</th>
            <th className="px-1.5 py-1 text-right font-medium">今日</th>
            <th className="px-1.5 py-1 text-right font-medium">5D</th>
            <th className="px-1.5 py-1 text-right font-medium">20D</th>
            <th className="px-1.5 py-1 text-right font-medium">成交额</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.code} className="border-b border-bg-border/50 last:border-0">
              <td className="px-1.5 py-1 whitespace-nowrap">
                <StockLink code={e.code} name={e.name} />
                {e.industry && <span className="ml-1 text-[10px] text-text-muted">{e.industry}</span>}
              </td>
              <td className={cn('px-1.5 py-1 text-right whitespace-nowrap text-text-secondary', NUM)}>
                {fmtCap(e.prev_cap)}→<span className="text-text-primary">{fmtCap(e.cap)}</span>
                {e.cap_price_mismatch && (
                  <span title={`市值变化 ${fmtPct(e.cap_change_pct)} 与当日涨跌幅 ${fmtPct(e.pct)} 不一致，可能涉及股本变动或口径变化`}
                        className="ml-1 inline-flex align-middle text-warn"><AlertTriangle className="w-3 h-3" /></span>
                )}
              </td>
              <td className={cn('px-1.5 py-1 text-right', NUM, pctCls(e.pct))}>{fmtPct(e.pct)}</td>
              <td className={cn('px-1.5 py-1 text-right', NUM, pctCls(e.pct_5d))}>{fmtPct(e.pct_5d)}</td>
              <td className={cn('px-1.5 py-1 text-right', NUM, pctCls(e.pct_20d))}>{fmtPct(e.pct_20d)}</td>
              <td className={cn('px-1.5 py-1 text-right text-text-secondary', NUM)}>{fmtAmt(e.amount)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function ChangeLists({ events, threshold, prevDate }: {
  events: LargeCapEvents
  threshold: number
  prevDate: string | null
}) {
  const t = fmtCap(threshold)
  if (!events.available) {
    return (
      <Card title="今日重要变化">
        <div className="text-xs text-text-muted leading-relaxed">
          前一交易日{prevDate ? `（${prevDate}）` : ''}没有完整收盘快照，跨入 / 跨出无法判定——不拿更早的市值冒充昨日。
        </div>
      </Card>
    )
  }
  return (
    <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <Card title={`跨入 ${t}（${events.entered.length}）`}>
        <CrossTable rows={events.entered} />
      </Card>
      <Card title={`跨出 ${t}（${events.exited.length}）`}>
        <CrossTable rows={events.exited} />
      </Card>
      {(events.first_observed.length > 0 || events.missing_today.length > 0 || events.st_change.length > 0) && (
        <Card title="不算跨越的成员变化" className="xl:col-span-2">
          <div className="space-y-1.5 text-xs">
            {events.first_observed.length > 0 && (
              <div>
                <span className="text-text-muted">首次观测（昨日没有有效市值，不判跨入）：</span>
                {events.first_observed.map((e) => (
                  <span key={e.code} className="mr-3 whitespace-nowrap" title={e.reason}>
                    <StockLink code={e.code} name={e.name} /> <span className={NUM}>{fmtCap(e.cap)}</span>
                  </span>
                ))}
              </div>
            )}
            {events.missing_today.length > 0 && (
              <div>
                <span className="text-text-muted">今日无有效市值（停牌或没返回，不判跨出）：</span>
                {events.missing_today.map((e) => (
                  <span key={e.code} className="mr-3 whitespace-nowrap" title={e.reason}>
                    <StockLink code={e.code} name={e.name} /> <span className={NUM}>昨 {fmtCap(e.prev_cap)}</span>
                  </span>
                ))}
              </div>
            )}
            {events.st_change.length > 0 && (
              <div>
                <span className="text-text-muted">ST 状态变化：</span>
                {events.st_change.map((e) => (
                  <span key={e.code} className="mr-3 whitespace-nowrap">
                    <StockLink code={e.code} name={e.name} /> {e.change}
                  </span>
                ))}
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  )
}

export function VolumeSurges({ rows, multipleReady, multiple, ratio }: {
  rows: LargeCapStock[]
  multipleReady: boolean
  multiple: number
  ratio: number
}) {
  return (
    <Card title={`明显放量（${rows.length}）`} action={
      <span className="text-[11px] text-text-muted">
        {multipleReady ? `成交额 ≥ 20日均额×${multiple} 或 量比 ≥ ${ratio}` : `量比 ≥ ${ratio}（20日成交额倍数历史未攒够）`}
      </span>
    }>
      {!rows.length ? <div className="text-xs text-text-muted py-3">无</div> : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-bg-border text-text-muted">
                <th className="px-1.5 py-1 text-left font-medium">股票</th>
                <th className="px-1.5 py-1 text-right font-medium">总市值</th>
                <th className="px-1.5 py-1 text-right font-medium">今日</th>
                <th className="px-1.5 py-1 text-right font-medium">成交额</th>
                <th className="px-1.5 py-1 text-right font-medium">量比</th>
                <th className="px-1.5 py-1 text-right font-medium">20日均额</th>
                <th className="px-1.5 py-1 text-right font-medium">倍数</th>
                <th className="px-1.5 py-1 text-right font-medium">换手</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.code} className="border-b border-bg-border/50 last:border-0">
                  <td className="px-1.5 py-1 whitespace-nowrap">
                    <StockLink code={s.code} name={s.name} />
                    <span className="ml-1 text-[10px] text-text-muted">{s.industry_l2}</span>
                  </td>
                  <td className={cn('px-1.5 py-1 text-right text-text-secondary', NUM)}>{fmtCap(s.total_cap)}</td>
                  <td className={cn('px-1.5 py-1 text-right', NUM, pctCls(s.pct))}>{fmtPct(s.pct)}</td>
                  <td className={cn('px-1.5 py-1 text-right text-text-secondary', NUM)}>{fmtAmt(s.amount)}</td>
                  <td className={cn('px-1.5 py-1 text-right', NUM)}>{fmtNum(s.volume_ratio)}</td>
                  <td className={cn('px-1.5 py-1 text-right text-text-secondary', NUM)}>{fmtAmt(s.amount_avg_20d)}</td>
                  <td className={cn('px-1.5 py-1 text-right', NUM)}>
                    {s.amount_multiple == null ? '—' : `${s.amount_multiple.toFixed(2)}×`}
                  </td>
                  <td className={cn('px-1.5 py-1 text-right text-text-secondary', NUM)}>
                    {s.turnover_rate == null ? '—' : `${s.turnover_rate.toFixed(2)}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="mt-2 text-[11px] text-text-muted">放量只是成交事实，上涨放量和下跌放量都列出，不代表资金净流入。</div>
    </Card>
  )
}
