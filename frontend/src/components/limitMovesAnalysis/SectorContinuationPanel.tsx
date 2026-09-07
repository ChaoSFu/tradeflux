import { useMemo, useState } from 'react'
import { cn } from '@/utils/cn'
import { SortTh, compareWithNullsLast, type SortState } from '@/components/common/SortTh'
import type { SectorContinuationResponse, SectorContinuationRow } from '@/api/stocks'

const NUM = 'font-mono tabular-nums'
const TD = 'px-2 py-1.5 whitespace-nowrap'

type Key = 'sector_name' | 'yesterday' | 'continued' | 'newly' | 'broken' | 'down' | 'ratio'
const COLS: { key: Key; label: string }[] = [
  { key: 'sector_name', label: '板块' },
  { key: 'yesterday', label: '昨日涨停' },
  { key: 'continued', label: '今日继续' },
  { key: 'newly', label: '今日新增' },
  { key: 'broken', label: '今日炸板' },
  { key: 'down', label: '今日跌停' },
  { key: 'ratio', label: '延续率' },
]

const valueOf = (r: SectorContinuationRow, k: Key): number | string | null => ({
  sector_name: r.sector_name,
  yesterday: r.yesterday_limit_up_count || null,
  continued: r.today_continued_limit_up_count || null,
  newly: r.today_new_limit_up_count || null,
  broken: r.today_broken_count || null,
  down: r.today_limit_down_count || null,
  ratio: r.continuation_ratio,
}[k])

/**
 * 板块跨日延续。**只有计数,没有 continuation_score。**
 *
 * 「延续 3 只、新增 5 只」跟「延续 5 只、新增 3 只」哪个更强,依赖当天的位置和
 * 情绪,不是一个固定权重能定下来的——所以这里把两个数分开摆,由看的人判断。
 *
 * 排序键就是对应列本身,空值沉底:「这个板块昨天没有涨停」和「它今天延续了 0 只」
 * 不是一回事。
 */
export function SectorContinuationPanel({ data }: { data: SectorContinuationResponse }) {
  const [sort, setSort] = useState<SortState<Key>>({ key: 'continued', dir: 'desc' })
  const onSort = (k: Key) =>
    setSort((p) => (p.key === k ? { key: k, dir: p.dir === 'desc' ? 'asc' : 'desc' }
                                : { key: k, dir: 'desc' }))

  const rows = useMemo(() => {
    const key = sort.key
    if (!key) return data.rows
    return [...data.rows].sort((a, b) =>
      compareWithNullsLast(valueOf(a, key), valueOf(b, key), sort.dir)
      || compareWithNullsLast(valueOf(a, 'yesterday'), valueOf(b, 'yesterday'), 'desc'))
  }, [data.rows, sort])

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <span className="text-xs font-semibold text-text-primary">板块跨日延续</span>
        <span className="text-[10px] text-text-muted">
          {data.prev_date && data.trade_date
            ? `${data.prev_date} → ${data.trade_date} · ${rows.length} 个板块`
            : <span className="text-warn">拿不到前一个交易日</span>}
        </span>
      </div>
      {rows.length === 0 ? (
        <div className="text-xs text-text-muted py-2">{data.notes[0] ?? '没有可统计的板块'}</div>
      ) : (
        <div className="overflow-x-auto max-h-[28rem] overflow-y-auto">
          {/* 关注板块可能有两百多个，给个高度上限免得这张表把整页撑开。
              不截断行数——少给几行跟「就这么多」看起来一样 */}
          <table className="w-full text-xs" style={{ minWidth: 560 }}>
            <thead>
              <tr className="text-[10px]">
                {COLS.map((c) => (
                  <SortTh key={c.key} col={c.key} label={c.label} align="left"
                          sort={sort} onSort={onSort}
                          className="px-2 py-1 border-b border-bg-border" />
                ))}
                <th className="px-2 py-1 text-left text-[10px] font-medium
                               text-text-secondary/55 border-b border-bg-border
                               whitespace-nowrap">未知</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.sector_id} className="border-b border-bg-border/40 last:border-0">
                  <td className={cn(TD, 'text-text-primary')}>{r.sector_name}</td>
                  <td className={cn(TD, NUM, 'text-text-secondary')}>
                    {r.yesterday_limit_up_count || '—'}
                  </td>
                  <td className={cn(TD, NUM, 'text-up')}>
                    {r.today_continued_limit_up_count || '—'}
                  </td>
                  <td className={cn(TD, NUM, r.today_new_limit_up_count ? 'text-up/80' : 'text-text-muted/50')}>
                    {r.today_new_limit_up_count || '—'}
                  </td>
                  <td className={cn(TD, NUM, r.today_broken_count ? 'text-warn/80' : 'text-text-muted/50')}>
                    {r.today_broken_count || '—'}
                  </td>
                  <td className={cn(TD, NUM, r.today_limit_down_count ? 'text-down' : 'text-text-muted/50')}>
                    {r.today_limit_down_count || '—'}
                  </td>
                  <td className={cn(TD, NUM,
                    r.continuation_ratio === null ? 'text-text-muted/50' : 'text-text-secondary')}>
                    {r.continuation_ratio === null
                      ? '—' : `${(r.continuation_ratio * 100).toFixed(0)}%`}
                  </td>
                  <td className={cn(TD, NUM,
                    r.today_unknown_count ? 'text-warn/80' : 'text-text-muted/50')}
                      title="昨日涨停但今天没有快照：停牌 / 退市 / 未抓到。不并进炸板">
                    {r.today_unknown_count || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ul className="text-[10px] text-text-muted space-y-0.5">
        <li>延续率 = 今日继续 /（今日继续 + 今日炸板）。
          <span className="text-text-secondary">昨日涨停但今天没快照的不进分母。</span></li>
        {data.notes.map((n, i) => <li key={i}>{n}</li>)}
      </ul>
    </div>
  )
}
