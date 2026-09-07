import { Fragment, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { cn } from '@/utils/cn'
import { SortTh, compareWithNullsLast, type SortState } from '@/components/common/SortTh'
import { SectorDetailDrawer } from './SectorDetailDrawer'
import { sectorSortValue, type SectorRow, type SectorSortKey } from './sectorRows'

const NUM = 'font-mono tabular-nums'
const TD = 'px-2 py-1.5 whitespace-nowrap'

const n0 = (v: number | null | undefined) =>
  v === null || v === undefined ? <span className="text-text-muted/50">—</span> : v
/**
 * 封板率。**后端给的已经是百分数（0~100），不是分数。**
 * 首版当成分数又乘了 100，页面上出现「封板率 10000%」。
 * 注意别跟 market-effects 的 red_ratio 等混：那些才是 0~1。
 */
const sealRate = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `${v.toFixed(1)}%`
const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`

const COLS: { key: SectorSortKey; label: string }[] = [
  { key: 'name', label: '板块' },
  { key: 'limit_up', label: '涨停' },
  { key: 'continuation', label: '连板' },
  { key: 'height', label: '最高板' },
  { key: 'broken', label: '炸板' },
  { key: 'limit_down', label: '跌停' },
  { key: 'seal_rate', label: '封板率' },
  { key: 'core', label: '核心反馈' },
]

/**
 * 板块涨跌停结构表。
 *
 * **没有综合评分。** 每一列就是它自己的那个事实，排序规则就是对应列的数值。
 * 想按哪个维度看就点哪一列——不存在一个替你决定"哪个板块更强"的分数。
 *
 * 空值沉底（compareWithNullsLast）：「这个板块没进涨停雷达」不是「它涨停 0 只」。
 */
export function SectorLimitTable({ rows, radarDate, downDate }: {
  rows: SectorRow[]
  radarDate: string | null
  downDate: string | null
}) {
  const [sort, setSort] = useState<SortState<SectorSortKey>>({ key: 'height', dir: 'desc' })
  const [openRow, setOpenRow] = useState<string | null>(null)
  const onSort = (k: SectorSortKey) =>
    setSort((p) => (p.key === k ? { key: k, dir: p.dir === 'desc' ? 'asc' : 'desc' }
                                : { key: k, dir: 'desc' }))

  const sorted = useMemo(() => {
    const key = sort.key
    if (!key) return rows
    return [...rows].sort((a, b) => {
      const c = compareWithNullsLast(sectorSortValue(a, key), sectorSortValue(b, key), sort.dir)
      // 同分时按涨停数，再按名字——顺序至少是稳定的
      return c !== 0 ? c
        : compareWithNullsLast(sectorSortValue(a, 'limit_up'),
                               sectorSortValue(b, 'limit_up'), 'desc')
          || a.name.localeCompare(b.name)
    })
  }, [rows, sort])

  const onlyDown = rows.filter((r) => !r.up).length

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <span className="text-xs font-semibold text-text-primary">板块涨跌停结构</span>
        <span className="text-[10px] text-text-muted">
          {rows.length} 个板块 · 点行展开明细
          {onlyDown > 0 && (
            <span className="text-warn ml-1"
                  title="涨停侧来自涨停板块雷达的 watched sector 分组，跌停侧来自个股上按展示口径过滤的多板块标签，两套归组规则不同">
              · {onlyDown} 个只有跌停侧数据
            </span>
          )}
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs" style={{ minWidth: 760 }}>
          <thead>
            <tr className="text-[10px]">
              <th className="w-6 border-b border-bg-border" />
              {COLS.map((c) => (
                <SortTh key={c.key} col={c.key} label={c.label} align="left"
                        sort={sort} onSort={onSort}
                        className="px-2 py-1 border-b border-bg-border" />
              ))}
              <th className="px-2 py-1 text-left text-[10px] font-medium
                             text-text-secondary/55 border-b border-bg-border
                             whitespace-nowrap">首封</th>
              <th className="px-2 py-1 text-left text-[10px] font-medium
                             text-text-secondary/55 border-b border-bg-border
                             whitespace-nowrap">断板最高</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => {
              const open = openRow === r.name
              const u = r.up
              return (
                <Fragment key={r.name}>
                  <tr
                      onClick={() => setOpenRow(open ? null : r.name)}
                      className={cn('border-b border-bg-border/40 cursor-pointer',
                        open ? 'bg-bg-elevated/50' : 'hover:bg-bg-elevated/30')}>
                    <td className="pl-2 text-text-muted">
                      {open ? <ChevronDown className="w-3.5 h-3.5" />
                            : <ChevronRight className="w-3.5 h-3.5" />}
                    </td>
                    <td className={cn(TD, 'text-text-primary')}>
                      {r.name}
                      {!u && (
                        <span className="ml-1 text-[10px] text-warn/70"
                              title="未进涨停板块雷达：可能未达门槛，也可能两侧板块归组口径不同">
                          仅跌停侧
                        </span>
                      )}
                    </td>
                    <td className={cn(TD, NUM, 'text-up')}>{n0(u?.today_limit_up_count)}</td>
                    <td className={cn(TD, NUM)}>{n0(u?.continuation_count)}</td>
                    <td className={cn(TD, NUM, 'text-up font-semibold')}>
                      {u ? `${u.board_height}板` : <span className="text-text-muted/50">—</span>}
                    </td>
                    <td className={cn(TD, NUM)}>{n0(u?.broken_count)}</td>
                    <td className={cn(TD, NUM, r.limitDown ? 'text-down' : 'text-text-muted/50')}>
                      {r.limitDown || '—'}
                      {r.downMaxBoard > 1 && (
                        <span className="text-[10px] text-text-muted ml-1">
                          最高{r.downMaxBoard}
                        </span>
                      )}
                    </td>
                    <td className={cn(TD, NUM, 'text-text-secondary')}>{sealRate(u?.seal_rate)}</td>
                    {/* core_avg_pct_change 为 null = 核心锚当日涨跌幅还没更新，不是 0 */}
                    <td className={cn(TD, NUM,
                      u?.core_avg_pct_change == null ? 'text-text-muted'
                        : u.core_avg_pct_change > 0 ? 'text-up' : 'text-down')}>
                      {pct(u?.core_avg_pct_change)}
                      {u && u.core_pct_known_count === 0 && u.core_count > 0 && (
                        <span className="text-[10px] text-warn ml-1" title="核心锚今日涨跌幅尚未更新">
                          未更新
                        </span>
                      )}
                    </td>
                    <td className={cn(TD, NUM, 'text-text-secondary')}>
                      {u?.earliest_limit_time ?? '—'}
                    </td>
                    <td className={cn(TD, NUM, 'text-text-secondary')}>
                      {u?.broken_streak_height ? `${u.broken_streak_height}板` : '—'}
                    </td>
                  </tr>
                  {open && (
                    <tr>
                      <td colSpan={COLS.length + 3} className="p-0">
                        <SectorDetailDrawer row={r} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>

      <p className="text-[10px] text-text-muted">
        涨停侧（涨停/连板/最高板/炸板/封板率/首封/核心反馈）来自涨停板块雷达
        {radarDate && ` ${radarDate}`}；跌停侧来自涨跌停总览
        {downDate && ` ${downDate}`}。
        <span className="text-warn/80">
          {' '}两侧板块归组规则不同（前者按 watched sector 分组，后者按个股上过滤后的
          多板块标签），所以一个板块可能只出现在一侧。
        </span>
      </p>
    </div>
  )
}
