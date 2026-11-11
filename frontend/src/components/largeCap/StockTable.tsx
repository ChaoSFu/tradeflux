import { useMemo } from 'react'
import type { LargeCapStock } from '@/api/largeCap'
import { cn } from '@/utils/cn'
import { NUM, SortTh, StockLink, fmtAmt, fmtCap, fmtNum, fmtPct, pctCls, sortRows, useDayLabels, useSort } from './shared'

type Col = 'total_cap' | 'pct' | 'pct_5d' | 'pct_10d' | 'pct_20d' | 'pct_60d' | 'amount'
  | 'volume_ratio' | 'turnover_rate' | 'amount_multiple'

const WINDOW_DAYS = { pct_5d: 5, pct_10d: 10, pct_20d: 20, pct_60d: 60 } as const

/** 区间涨跌幅为空的格子：上市不满 N 天的写明原因，其余就是没数据 */
function PctCell({ s, k }: { s: LargeCapStock; k: 'pct' | keyof typeof WINDOW_DAYS }) {
  const v = s[k]
  if (v == null && k !== 'pct' && s.listing_date) {
    return (
      <td className="px-2 py-1.5 text-right text-[10px] text-text-muted"
          title={`${s.listing_date} 上市，上市以来不满（或无法确认满）${WINDOW_DAYS[k]} 个交易日，没有这个区间的涨跌幅（东财这时给的是上市以来涨幅，已剔除）`}>
        新股
      </td>
    )
  }
  return <td className={cn('px-2 py-1.5 text-right', NUM, pctCls(v))}>{fmtPct(v)}</td>
}

export function StockTable({ rows, showIndustry = false, initialSort = 'total_cap', maxHeight, shares }: {
  rows: LargeCapStock[]
  showIndustry?: boolean
  initialSort?: Col
  maxHeight?: number
  /** 组内权重（0~1），给了就多一列「组内权重」——一组的涨跌是不是一只巨头说了算，看这列 */
  shares?: Map<string, number>
}) {
  const { day } = useDayLabels()
  const sort = useSort<Col>(initialSort)
  const sorted = useMemo(() => sortRows(rows, (r) => r[sort.key], sort.dir), [rows, sort.key, sort.dir])
  if (!rows.length) return <div className="px-3 py-4 text-xs text-text-muted">没有成员</div>
  return (
    <div className="overflow-auto" style={maxHeight ? { maxHeight } : undefined}>
      <table className="w-full text-xs [&_td]:whitespace-nowrap">
        <thead className="sticky top-0 bg-bg-card z-10">
          <tr className="border-b border-bg-border text-text-muted">
            <th className="px-2 py-1.5 text-left font-medium">股票</th>
            {showIndustry && <th className="px-2 py-1.5 text-left font-medium">行业</th>}
            <SortTh label="总市值" col="total_cap" sort={sort} />
            {shares && (
              <th className="px-2 py-1.5 text-right font-medium whitespace-nowrap text-text-muted"
                  title={`这只在组内加权涨幅里的权重（跟「${day}加权」用同一套权重）`}>组内权重</th>
            )}
            <SortTh label={day} col="pct" sort={sort} />
            <SortTh label="5D" col="pct_5d" sort={sort} />
            <SortTh label="10D" col="pct_10d" sort={sort} />
            <SortTh label="20D" col="pct_20d" sort={sort} />
            <SortTh label="60D" col="pct_60d" sort={sort} />
            <SortTh label="成交额" col="amount" sort={sort} />
            <SortTh label="量比" col="volume_ratio" sort={sort} title="东财量比：收盘后 = 当日成交量 ÷ 前 5 日均量" />
            <SortTh label="换手" col="turnover_rate" sort={sort} />
            <SortTh label="额/20日均" col="amount_multiple" sort={sort}
                    title="当日成交额 ÷ 前 20 个交易日平均成交额。前 20 天任何一天缺快照就是 —" />
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => (
            <tr key={s.code} className="border-b border-bg-border/50 last:border-0 hover:bg-bg-elevated/40">
              <td className="px-2 py-1.5 whitespace-nowrap"><StockLink code={s.code} name={s.name} hasDetail={s.has_detail} /></td>
              {showIndustry && (
                <td className="px-2 py-1.5 whitespace-nowrap text-text-secondary max-w-[9rem] truncate"
                    title={`${s.industry_l1} / ${s.industry_l2}`}>{s.industry_l2}</td>
              )}
              <td className={cn('px-2 py-1.5 text-right text-text-primary', NUM)}
                  title={s.float_cap != null ? `流通市值 ${fmtCap(s.float_cap)}` : undefined}>{fmtCap(s.total_cap)}</td>
              {shares && (() => {
                const w = shares.get(s.code)
                return (
                  <td className={cn('px-2 py-1.5 text-right', NUM, w != null && w >= 0.4 ? 'text-warn' : 'text-text-secondary')}>
                    {w == null ? '—' : `${(w * 100).toFixed(1)}%`}
                  </td>
                )
              })()}
              {(['pct', 'pct_5d', 'pct_10d', 'pct_20d', 'pct_60d'] as const).map((k) => <PctCell key={k} s={s} k={k} />)}
              <td className={cn('px-2 py-1.5 text-right text-text-secondary', NUM)}>{fmtAmt(s.amount)}</td>
              <td className={cn('px-2 py-1.5 text-right', NUM, s.volume_ratio != null && s.volume_ratio >= 2 ? 'text-warn' : 'text-text-secondary')}>
                {fmtNum(s.volume_ratio)}
              </td>
              <td className={cn('px-2 py-1.5 text-right text-text-secondary', NUM)}>
                {s.turnover_rate == null ? '—' : `${s.turnover_rate.toFixed(2)}%`}
              </td>
              <td className={cn('px-2 py-1.5 text-right', NUM, s.amount_multiple != null && s.amount_multiple >= 2 ? 'text-warn' : 'text-text-secondary')}
                  title={s.amount_avg_20d != null ? `前 20 日均额 ${fmtAmt(s.amount_avg_20d)}` : '前 20 个交易日快照不全'}>
                {s.amount_multiple == null ? '—' : `${s.amount_multiple.toFixed(2)}×`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
