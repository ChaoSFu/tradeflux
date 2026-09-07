import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '@/utils/cn'
import { fetchCohortMembers } from '@/api/marketEffects'
import { QueryState } from './QueryState'
import type { CohortOutcome, CohortType, MarketEffectDailyResponse } from '@/types'

const NUM = 'font-mono tabular-nums'
const ORDER: CohortType[] = [
  'limit_up', 'first_board', 'multi_board', 'limit_down', 'broken_board', 'strong_proxy',
]

const pct = (v: number | null) =>
  v === null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
const rate = (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(0)}%`)
const tone = (v: number | null) =>
  v === null ? 'text-text-muted' : v > 0 ? 'text-up' : v < 0 ? 'text-down' : ''

/**
 * 昨日群体的今日反馈。
 *
 * **第一屏就是 cohort 表本身**，不放 profit_strength / loss_strength / quadrant /
 * lifecycle_state——那四个是固定权重派生出来的，这一页只摆被冻结的群体今天实际
 * 走成什么样。
 *
 * `median_pct_change === null`（valid_count 不足）显示 —，不是 0%。
 */
export function CohortFeedbackTable({ data }: { data: MarketEffectDailyResponse }) {
  const [open, setOpen] = useState<CohortType | null>(null)
  const cohorts = ORDER.map((t) => data.cohorts?.[t]).filter(Boolean) as CohortOutcome[]

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <span className="text-xs font-semibold text-text-primary">昨日群体 · 今日反馈</span>
        <span className="text-[10px] text-text-muted">
          反馈日 {data.trade_date} · 群体在前一交易日收盘时冻结 ·
          宽度口径 {data.breadth_source === 'full_market' ? '全市场' : '跟踪池'}
          （覆盖 {(data.coverage_ratio * 100).toFixed(0)}%）
        </span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs" style={{ minWidth: 620 }}>
          <thead>
            <tr className="text-[10px] text-text-muted">
              <th className="w-6 border-b border-bg-border" />
              {['昨日群体', '样本', '今日中位收益', '红盘率', '晋级率', '断板率', '大亏率']
                .map((h) => (
                  <th key={h} className="px-2 py-1 text-left font-medium
                                         border-b border-bg-border whitespace-nowrap">{h}</th>
                ))}
            </tr>
          </thead>
          <tbody>
            {cohorts.map((c) => (
              <CohortRow key={c.cohort_type} c={c} tradeDate={data.trade_date}
                         open={open === c.cohort_type}
                         onToggle={() => setOpen(open === c.cohort_type ? null : c.cohort_type)} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-text-muted">
        样本列 = 有次日结果的只数 / 群体总数。中位收益在有效样本不足时显示 —，
        <span className="text-text-secondary">不是 0%</span>。
      </p>
    </div>
  )
}

function CohortRow({ c, tradeDate, open, onToggle }: {
  c: CohortOutcome; tradeDate: string; open: boolean; onToggle: () => void
}) {
  return (
    <>
      <tr onClick={onToggle}
          className={cn('border-b border-bg-border/40 cursor-pointer',
            open ? 'bg-bg-elevated/50' : 'hover:bg-bg-elevated/30')}>
        <td className="pl-2 text-text-muted">
          {open ? <ChevronDown className="w-3.5 h-3.5" />
                : <ChevronRight className="w-3.5 h-3.5" />}
        </td>
        <td className="px-2 py-1.5 text-text-primary whitespace-nowrap">{c.label}</td>
        <td className={cn('px-2 py-1.5', NUM, 'text-text-secondary')}>
          {c.valid_count}/{c.member_count}
        </td>
        <td className={cn('px-2 py-1.5', NUM, tone(c.median_pct_change))}>
          {pct(c.median_pct_change)}
        </td>
        <td className={cn('px-2 py-1.5', NUM)}>{rate(c.red_ratio)}</td>
        <td className={cn('px-2 py-1.5', NUM)}>{rate(c.advance_ratio)}</td>
        <td className={cn('px-2 py-1.5', NUM)}>{rate(c.broken_ratio)}</td>
        <td className={cn('px-2 py-1.5', NUM)}>{rate(c.large_loss_ratio)}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={8} className="p-0">
            <CohortMembers tradeDate={tradeDate} cohortType={c.cohort_type} />
          </td>
        </tr>
      )}
    </>
  )
}

function CohortMembers({ tradeDate, cohortType }: {
  tradeDate: string; cohortType: CohortType
}) {
  const q = useQuery({
    queryKey: ['cohort-members', tradeDate, cohortType],
    queryFn: () => fetchCohortMembers(tradeDate, cohortType),
    staleTime: 10 * 60 * 1000,
  })
  return (
    <div className="px-3 py-2 bg-bg-base/40">
      <QueryState qs={[q]} isEmpty={!q.data?.length}
                  emptyText="这个群体没有成员" rows={2}>
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]" style={{ minWidth: 420 }}>
            <thead>
              <tr className="text-text-muted">
                {['股票', '昨日板数', '今日涨跌幅', '今日板数'].map((h) => (
                  <th key={h} className="px-2 py-1 text-left font-medium
                                         border-b border-bg-border whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(q.data ?? []).map((m, i) => (
                <tr key={`${m.code}-${i}`} className="border-b border-bg-border/40 last:border-0">
                  <td className="px-2 py-1 whitespace-nowrap">
                    <span className="text-text-primary">{m.name ?? '—'}</span>
                    <span className={cn('ml-1 text-text-muted', NUM)}>{m.code ?? ''}</span>
                  </td>
                  <td className={cn('px-2 py-1', NUM)}>{m.board_count_before ?? '—'}</td>
                  {/* has_outcome=false → 停牌/退市等，没有次日结果。不是 0% */}
                  <td className={cn('px-2 py-1', NUM, tone(m.outcome_pct_change))}>
                    {m.has_outcome ? pct(m.outcome_pct_change)
                      : <span className="text-text-muted/60">无次日数据</span>}
                  </td>
                  <td className={cn('px-2 py-1', NUM)}>{m.outcome_board_count ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </QueryState>
    </div>
  )
}
