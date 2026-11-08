import { Fragment, useEffect, useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import type { LargeCapGroup, LargeCapStock } from '@/api/largeCap'
import { cn } from '@/utils/cn'
import { StockTable } from './StockTable'
import { NUM, SortTh, UpFlatDown, fmtAmt, fmtCap, fmtPct, pctCls, sortRows, useSort } from './shared'

export type Window = 'pct' | '5d' | '10d' | '20d' | '60d'
type Col = 'count' | 'total_cap' | 'weighted_pct' | 'median_pct' | 'median_5d' | 'median_10d'
  | 'median_20d' | 'median_60d' | 'amount' | 'surge' | 'entered' | 'exited'

type MedCol = 'median_pct' | 'median_5d' | 'median_10d' | 'median_20d' | 'median_60d'

const windowCol = (w: Window): Col => (w === 'pct' ? 'median_pct' : `median_${w}`)

function surgeOf(g: LargeCapGroup) {
  return g.surge_multiple ?? g.surge_volume_ratio
}

export function GroupTable({ groups, stocksByCode, window, eventsAvailable, multipleReady, label, maxHeight, emptyText }: {
  groups: LargeCapGroup[]
  stocksByCode: Map<string, LargeCapStock>
  window: Window
  eventsAvailable: boolean
  multipleReady: boolean
  label: string
  maxHeight?: number
  emptyText?: string
}) {
  const sort = useSort<Col>(windowCol(window))
  useEffect(() => { sort.setKey(windowCol(window)) }, [window]) // eslint-disable-line react-hooks/exhaustive-deps
  const [open, setOpen] = useState<string | null>(null)

  const sorted = useMemo(() => sortRows(groups, (g) => (sort.key === 'surge' ? surgeOf(g) : g[sort.key as Exclude<Col, 'surge'>]), sort.dir),
    [groups, sort.key, sort.dir])

  const hl = (c: Col) => (windowCol(window) === c ? 'bg-accent/5' : '')
  const medCell = (g: LargeCapGroup, c: Col, n: number) => (
    <td className={cn('px-2 py-1.5 text-right', NUM, pctCls(g[c as MedCol]), hl(c))}
        title={`有效样本 ${n}/${g.count}`}>{fmtPct(g[c as MedCol])}</td>
  )

  if (!groups.length) return <div className="px-3 py-6 text-xs text-text-muted text-center">{emptyText ?? '没有数据'}</div>
  return (
    <div className="overflow-auto" style={maxHeight ? { maxHeight } : undefined}>
      <table className="w-full text-xs [&_td]:whitespace-nowrap">
        <thead className="sticky top-0 bg-bg-card z-10">
          <tr className="border-b border-bg-border text-text-muted">
            <th className="px-2 py-1.5 text-left font-medium">{label}</th>
            <SortTh label="公司数" col="count" sort={sort} title="达到市值门槛的大市值公司数，不是行业全部上市公司" />
            <SortTh label="总市值" col="total_cap" sort={sort} />
            <SortTh label="今日加权" col="weighted_pct" sort={sort}
                    title="Σ(权重 × 今日涨幅) / Σ权重。权重口径见页面顶部说明；缺涨幅或缺权重的不参与" />
            <th className="px-2 py-1.5 text-left font-medium whitespace-nowrap"
                title="对今日加权涨幅贡献最大的成员和它贡献的百分点。加权涨幅大部分来自它、而中位数方向相反时，就是一只权重股在拉">
              主要贡献
            </th>
            <SortTh label="今日中位" col="median_pct" sort={sort} className={hl('median_pct')} />
            <th className="px-2 py-1.5 text-right font-medium whitespace-nowrap" title="今日上涨 / 平盘 / 下跌家数">上/平/下</th>
            <SortTh label="5D中位" col="median_5d" sort={sort} className={hl('median_5d')}
                    title="当前成员回看近 5 个交易日涨跌幅的中位数（不是板块指数收益）" />
            <SortTh label="10D中位" col="median_10d" sort={sort} className={hl('median_10d')} />
            <SortTh label="20D中位" col="median_20d" sort={sort} className={hl('median_20d')}
                    title="当前成员回看近 20 个交易日涨跌幅的中位数（不是板块指数收益）" />
            <SortTh label="60D中位" col="median_60d" sort={sort} className={hl('median_60d')} />
            <SortTh label="成交额" col="amount" sort={sort} />
            <SortTh label="放量" col="surge" sort={sort}
                    title={multipleReady ? '成交额 ≥ 前 20 日均额 × 设定倍数的公司数' : '量比 ≥ 设定值的公司数（20 日成交额倍数历史还没攒够）'} />
            <SortTh label="跨入" col="entered" sort={sort} />
            <SortTh label="跨出" col="exited" sort={sort} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((g) => {
            const isOpen = open === g.key
            const members = g.members.map((c) => stocksByCode.get(c)).filter((s): s is LargeCapStock => !!s)
            return (
              <Fragment key={g.key}>
                <tr onClick={() => setOpen(isOpen ? null : g.key)}
                    className={cn('border-b border-bg-border/50 cursor-pointer hover:bg-bg-elevated/40',
                      isOpen && 'bg-bg-elevated/60')}>
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    <span className="inline-flex items-center gap-1">
                      <ChevronRight className={cn('w-3 h-3 text-text-muted transition-transform', isOpen && 'rotate-90')} />
                      <span className="text-text-primary">{g.name}</span>
                      {g.watched && <span className="text-[10px] text-accent" title="Sector Config 里的关注概念">关注</span>}
                    </span>
                  </td>
                  <td className={cn('px-2 py-1.5 text-right text-text-primary', NUM)}>{g.count}</td>
                  <td className={cn('px-2 py-1.5 text-right text-text-secondary', NUM)}>{fmtCap(g.total_cap)}</td>
                  <td className={cn('px-2 py-1.5 text-right', NUM, pctCls(g.weighted_pct))}
                      title={`参与加权 ${g.n_weighted}/${g.count}`}>{fmtPct(g.weighted_pct)}</td>
                  <td className="px-2 py-1.5 text-left max-w-[9rem] truncate">
                    {g.top_contributor && g.count > 1 ? (
                      <span title={`${g.top_contributor.name} 贡献 ${g.top_contributor.pp > 0 ? '+' : ''}${g.top_contributor.pp.toFixed(2)} 个百分点`}>
                        <span className="text-text-secondary">{g.top_contributor.name}</span>
                        <span className={cn('ml-1', NUM, pctCls(g.top_contributor.pp))}>
                          {g.top_contributor.pp > 0 ? '+' : ''}{g.top_contributor.pp.toFixed(2)}
                        </span>
                      </span>
                    ) : <span className="text-text-muted/50">—</span>}
                  </td>
                  {medCell(g, 'median_pct', g.n_pct)}
                  <td className="px-2 py-1.5 text-right"><UpFlatDown up={g.up} flat={g.flat} down={g.down} /></td>
                  {medCell(g, 'median_5d', g.n_5d)}
                  {medCell(g, 'median_10d', g.n_10d)}
                  {medCell(g, 'median_20d', g.n_20d)}
                  {medCell(g, 'median_60d', g.n_60d)}
                  <td className={cn('px-2 py-1.5 text-right text-text-secondary', NUM)}>{fmtAmt(g.amount)}</td>
                  <td className={cn('px-2 py-1.5 text-right', NUM, surgeOf(g) ? 'text-warn' : 'text-text-muted')}>{surgeOf(g)}</td>
                  <td className={cn('px-2 py-1.5 text-right', NUM, g.entered ? 'text-up' : 'text-text-muted')}>
                    {eventsAvailable ? g.entered : '—'}
                  </td>
                  <td className={cn('px-2 py-1.5 text-right', NUM, g.exited ? 'text-down' : 'text-text-muted')}>
                    {eventsAvailable ? g.exited : '—'}
                  </td>
                </tr>
                {isOpen && (
                  <tr className="border-b border-bg-border">
                    <td colSpan={15} className="p-0 bg-bg-base/40">
                      <div className="pl-5 pr-2 py-1">
                        <StockTable rows={members} />
                        {g.count === 0 && <div className="px-2 pb-2 text-[11px] text-text-muted">今天没有成员（只有跨出）</div>}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
