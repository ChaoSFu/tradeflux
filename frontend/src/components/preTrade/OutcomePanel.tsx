import { cn } from '@/utils/cn'
import type { PreTradeOutcome, PriceRet } from '@/api/preTradeCheck'

const pct = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
const tone = (v: number | null | undefined) => (v == null ? 'text-text-muted' : v > 0 ? 'text-up' : v < 0 ? 'text-down' : '')

function Cell({ label, r }: { label: string; r?: PriceRet }) {
  return (
    <div className="rounded border border-bg-border/60 px-3 py-2">
      <div className="text-[10px] text-text-muted">{label}{r?.date ? ` · ${r.date}` : ''}</div>
      <div className="font-mono text-sm text-text-primary">{r?.price ?? '—'}</div>
      <div className={cn('font-mono text-xs', tone(r?.ret))}>{pct(r?.ret)}</div>
    </div>
  )
}

/**
 * 后续走势。**跟上面的判定隔开摆**：它是 as_of 之后才发生的事，不回写结论——
 * 复盘练的是当时的决策质量，不是拿结果倒推当时对不对。
 */
export function OutcomePanel({ o }: { o: PreTradeOutcome }) {
  return (
    <div className="card p-4 space-y-3 border-dashed">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <span className="text-xs font-semibold text-text-primary">后续走势（as_of 之后发生的）</span>
        <span className="text-[11px] text-warn">不影响上面的判定——判定只看当时能知道的</span>
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Cell label="30 分钟后" r={o.after_30m} />
        <Cell label="当日收盘" r={o.day_close} />
        <Cell label="T+1 收盘" r={o.t1} />
        <Cell label="T+3 收盘" r={o.t3} />
      </div>
      <div className="text-[11px] text-text-muted">
        基准价 {o.base_price ?? '—'} · 当日 as_of 之后最高 {pct(o.mfe)} / 最低 {pct(o.mae)}
        {o.notes.length > 0 && ` · ${o.notes.join('；')}`}
      </div>
    </div>
  )
}
