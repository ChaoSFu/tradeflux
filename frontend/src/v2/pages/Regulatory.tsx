/**
 * 监管 —— 两部分，其中一部分现在**没有数据，不伪造**。
 *
 *   个股监管风险   有：直接复用 watchlist（监管中 / 即将解除 / 最近解除 / 逼近阈值）
 *   市场监管环境   没有：管理层态度需要结构化的政策/表态数据，我们一条都没有
 *
 * **不能用个股监管数量推断管理层态度。** 监管个股多，可能是行情热，也可能是
 * 交易所口径变了——这两件事对下一步的含义完全相反。所以这里明确写「暂无结构化
 * 数据」，而不是造一个「监管压力指数 78」。
 */
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { fetchRegulatoryWatchlist } from '@/api/watchlist'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import { DataGap, Empty } from '../components/DataGap'

const NUM = 'font-mono tabular-nums'

export default function Regulatory() {
  const { data, isLoading } = useQuery({
    queryKey: ['regulatory-watchlist'], queryFn: fetchRegulatoryWatchlist,
    staleTime: 10 * 60 * 1000 })

  if (isLoading) return <LoadingRows />

  const groups: Array<[string, any[], string]> = [
    ['监管中', data?.monitoring ?? [], '已被交易所公告监管，买入前必须确认'],
    ['即将解除', data?.ending_soon ?? [], '剩余天数少，解除后可能重新活跃'],
    ['最近解除', data?.recently_released ?? [], '刚脱离监管'],
  ]

  return (
    <div className="space-y-4">
      <DataGap
        what="市场级监管环境：暂无结构化数据"
        why="管理层监管态度需要政策/表态一类的结构化数据源，目前一条都没有。
             不能用个股监管数量推断管理层态度 —— 监管个股多可能是行情热，
             也可能是交易所口径变了，这两件事对下一步的含义完全相反。"
        plan="将来若接入，模型至少需要 date / authority / event_type / stance /
              severity / scope / source。在真正有数据之前，这一栏保持空白。"
      />

      {groups.map(([title, rows, hint]) => (
        <section key={title} className="card p-4">
          <div className="flex items-baseline gap-2">
            <h2 className="text-sm text-text-primary">{title}</h2>
            <span className={cn('text-[11px] text-text-muted', NUM)}>{rows.length}</span>
            <span className="text-[11px] text-text-muted ml-2">{hint}</span>
          </div>
          {!rows.length ? <div className="text-xs text-text-muted mt-2">无</div> : (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-xs" style={{ minWidth: 720 }}>
                <thead>
                  <tr className="text-[10px] text-text-muted">
                    {['股票', '触发原因', '方向', '起始', '结束', '剩余天数'].map((h) => (
                      <th key={h} className="px-2 py-1.5 text-left font-medium
                                             border-b border-bg-border whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.info_code} className="border-b border-bg-border/50 last:border-0">
                      <td className="px-2 py-1.5 whitespace-nowrap">
                        <Link to={`/stocks/${r.security_code}`}
                              className="text-text-primary hover:text-accent">
                          {r.security_name || r.security_code}
                        </Link>
                        <span className={cn('ml-1 text-[10px] text-text-muted', NUM)}>
                          {r.security_code}
                        </span>
                      </td>
                      <td className="px-2 py-1.5 text-text-secondary max-w-[20rem] truncate"
                          title={r.reason || ''}>{r.reason || r.reason_type || '—'}</td>
                      <td className="px-2 py-1.5">
                        {r.direction === 'up' ? <span className="text-up">涨</span>
                          : r.direction === 'down' ? <span className="text-down">跌</span>
                          : <span className="text-text-muted">—</span>}
                      </td>
                      <td className={cn('px-2 py-1.5', NUM)}>{r.start_date || '—'}</td>
                      <td className={cn('px-2 py-1.5', NUM)}>{r.end_date || '—'}</td>
                      <td className={cn('px-2 py-1.5', NUM)}>
                        {r.days_remaining === null ? '—' : r.days_remaining}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ))}

      <section className="card p-4">
        <div className="flex items-baseline gap-2">
          <h2 className="text-sm text-text-primary">逼近严重异动阈值</h2>
          <span className={cn('text-[11px] text-text-muted', NUM)}>
            {data?.approaching.length ?? 0}
          </span>
          <span className="text-[11px] text-text-muted ml-2">
            还差多少涨幅就会触发。查历史日期时这一栏为空 —— 阈值是相对「今天」算的
          </span>
        </div>
        {!data?.approaching.length ? <Empty text="今日无逼近阈值的股票" /> : (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs" style={{ minWidth: 700 }}>
              <thead>
                <tr className="text-[10px] text-text-muted">
                  {['股票', '规则', '累计偏离', '阈值', '接近度', '今日还需'].map((h) => (
                    <th key={h} className="px-2 py-1.5 text-left font-medium
                                           border-b border-bg-border whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.approaching.map((a) => (
                  <tr key={a.security_code + a.window}
                      className="border-b border-bg-border/50 last:border-0">
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      <Link to={`/stocks/${a.security_code}`}
                            className="text-text-primary hover:text-accent">
                        {a.security_name || a.security_code}
                      </Link>
                    </td>
                    <td className="px-2 py-1.5 text-text-secondary">{a.rule_label}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{a.cum_deviation.toFixed(1)}%</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{a.threshold.toFixed(0)}%</td>
                    <td className={cn('px-2 py-1.5', NUM,
                      a.approach >= 0.8 ? 'text-warn' : '')}>
                      {(a.approach * 100).toFixed(0)}%
                    </td>
                    <td className={cn('px-2 py-1.5', NUM)}>
                      {a.target_rate === null ? '—' : `${a.target_rate.toFixed(1)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
