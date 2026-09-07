/**
 * 主线 —— 找「多头趋势 + 市场注意力集中」的板块。
 *
 * ## 这一页现在只能做一半，必须说清楚
 *
 * 「多头趋势」判不了：板块没有均线。SectorIndexDaily 只有当天一根，历史回填被
 * 数据源限流拦着。**绝不用 5/10/20 日涨幅冒充均线多头**——涨幅为正只说明这段
 * 涨了，说不出结构，一只从高位掉下来的板块 20 日涨幅仍可能是正的。
 *
 * 所以这一页现在给的是「领先证据」，跟「趋势」在界面上严格分开摆。
 *
 * ## 排序是字典序，不是分数
 *
 * 证据条数 → 最高连板 → 成交额。每一行的位次都能用一句话解释，
 * 不生成任何 0~100 的主线分。
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { fetchSectors, SECTORS_LITE_KEY } from '@/api/sectors'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import { DataGap, Empty } from '../components/DataGap'
import { TOP_N, rankMainlines } from '../domain/sectorGate'

const NUM = 'font-mono tabular-nums'
const rank = (r: number | null) =>
  r === null ? '—' : <span className={r <= TOP_N ? 'text-accent' : ''}>#{r}</span>

export default function Mainlines() {
  const { data, isLoading } = useQuery({
    queryKey: [...SECTORS_LITE_KEY], queryFn: () => fetchSectors(false), staleTime: 10 * 60 * 1000 })
  const ranked = useMemo(() => rankMainlines(data?.items ?? []), [data])

  return (
    <div className="space-y-4">
      <DataGap
        what="板块趋势暂时判不出来"
        why="板块没有均线数据 —— SectorIndexDaily 只有当天一根，历史 K 线回填被数据源
             （push2his）限流拦着。按 Unknown≠False，这里给「数据不足」而不是猜一个方向。"
        plan="补上板块指数历史之后，这一页会增加 MA5/10/20/60、均线排列、MA20 斜率，
              届时主线判定才谈得上「趋势 gate」。在那之前下面只是领先证据，不是趋势排名。"
      />

      <section className="card p-4">
        <div className="flex items-baseline gap-3">
          <h2 className="text-sm text-text-primary">领先证据排序</h2>
          <span className="text-[11px] text-text-muted">
            字典序：证据条数 → 最高连板 → 成交额，不是主线分
          </span>
        </div>
        {isLoading ? <LoadingRows /> : !ranked.length ? <Empty text="今日无领先板块" /> : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-xs" style={{ minWidth: 900 }}>
              <thead>
                <tr className="text-[10px] text-text-muted">
                  {['#', '板块', '趋势', '领先证据', '5日', '10日', '20日',
                    '涨停', '最高板', '强势股', '成交额(亿)'].map((h) => (
                    <th key={h} className="px-2 py-1.5 text-left font-medium
                                           border-b border-bg-border whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ranked.map((e, i) => (
                  <tr key={e.sector.code} className="border-b border-bg-border/50 last:border-0">
                    <td className={cn('px-2 py-1.5', NUM,
                      i < 3 ? 'text-accent' : 'text-text-muted')}>{i + 1}</td>
                    <td className="px-2 py-1.5">
                      <Link to={`/sector-trend?code=${e.sector.code}`}
                            className="text-text-primary hover:text-accent">{e.sector.name}</Link>
                    </td>
                    <td className="px-2 py-1.5 text-warn">数据不足</td>
                    <td className="px-2 py-1.5 text-text-secondary">
                      {e.hits.join('、') || '—'}
                    </td>
                    <td className={cn('px-2 py-1.5', NUM)}>{rank(e.sector.rank_5d)}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{rank(e.sector.rank_10d)}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{rank(e.sector.rank_20d)}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{e.sector.limit_up_count}</td>
                    <td className={cn('px-2 py-1.5', NUM,
                      e.boardHeight >= 4 ? 'text-up' : '')}>{e.boardHeight || '—'}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{e.sector.strong_stock_count}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>
                      {/* Sector.amount 单位**本来就是亿元**（backend models/sector.py:20），
                          首版又除了一次 1e8，整列显示成 0 */}
                      {e.sector.amount ? e.sector.amount.toFixed(0) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-[11px] text-text-muted mt-3">
          「趋势」那一列现在全部是「数据不足」，这是刻意的。等板块均线接上之前，
          任何把涨幅解释成趋势的做法都是在制造一个看起来精确、语义却错的数字。
        </p>
      </section>
    </div>
  )
}
