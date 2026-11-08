import { useQuery } from '@tanstack/react-query'
import { fetchSectorPeers, type Plan, type SystemResponse } from '@/api/marketRecap'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import { NUM, StockLink, fmtPct, pctCls } from './shared'

const ACTIVE_ZH = { strong: '强势池', limit_up: '涨停', limit_down: '跌停' } as const
const SYS_TONE: Record<SystemResponse, string> = {
  limit_up: 'text-up', up_response: 'text-up', independent_strong: 'text-accent', not_met: 'text-text-muted', insufficient: 'text-warn',
}
const TYPE_ZH: Record<string, string> = { industry: '行业', concept: '概念', region: '地区' }

/**
 * 板块关联（自动）：高标所在的每个板块里，当天的活跃股（强势池 / 涨停 / 跌停）。
 * 一只票在高标的几个板块里，就在每个板块下都出现，标「也在」。这是当天的事实，不进关联知识库；
 * 想长期记住某条，点「记为关联」。
 */
export function SectorPeersPanel({ code, date, adopted, onWatch, onPlan, onAdopt, readOnly }: {
  code: string
  date: string
  adopted: Set<string>               // `${板块名}|${代码}`：已经记进关联知识库的
  onWatch: (c: string, name: string | null) => void
  onPlan: (p: Partial<Plan>) => void
  onAdopt: (sector: { name: string; type: string }, c: string, name: string | null, active: string) => void
  readOnly: boolean
}) {
  const { data, isLoading, isError, error } = useQuery({ queryKey: ['recap-sector-peers', code, date], queryFn: () => fetchSectorPeers(code, date) })
  if (isLoading) return <LoadingSpinner />
  if (isError) return <div className="text-xs text-down">板块关联读取失败：{(error as Error)?.message}</div>
  if (!data || data.status !== 'ok') return <div className="text-xs text-text-muted">{data?.note ?? '没有板块关联'}</div>
  return (
    <div className="space-y-2">
      <div className="text-[11px] text-text-muted">
        高标所在 <b className="text-text-secondary">{data.groups.length}</b> 个板块，{date} 当天同板块的活跃股 <b className="text-text-secondary">{data.peers_total}</b> 只（按股票去重）。
        {(data.notes ?? []).join('；')}。
      </div>
      <div className="grid grid-cols-1 2xl:grid-cols-2 gap-2">
        {data.groups.map((g) => (
          <div key={g.sector.code} className="card-elevated p-2">
            <div className="flex items-center gap-1.5 text-xs mb-1">
              <span className="text-text-primary font-medium">{g.sector.name}</span>
              <span className="text-[10px] text-text-muted">{TYPE_ZH[g.sector.type] ?? g.sector.type}</span>
              {g.sector.primary && <span className="text-[10px] px-1 rounded bg-accent/15 text-accent">主板块</span>}
              <span className="text-[10px] text-text-muted ml-auto">
                活跃 {g.stocks.length} 只 · 涨停 {g.stocks.filter((s) => s.active.includes('limit_up')).length}
              </span>
            </div>
            {!g.stocks.length ? <div className="text-[11px] text-text-muted">当天这个板块没有活跃股</div> : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs [&_td]:whitespace-nowrap">
                  <thead>
                    <tr className="text-text-muted border-b border-bg-border">
                      {['股票', '活跃', '今日', '5日', '20日', '涨停·首封', '成交额', '系统响应', ''].map((h, i) => (
                        <th key={h + i} className={cn('px-1.5 py-0.5 font-medium', i >= 2 && i <= 6 ? 'text-right' : 'text-left')}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {g.stocks.map((s) => {
                      const f = s.facts
                      const key = `${g.sector.name}|${s.code}`
                      return (
                        <tr key={s.code} className="border-b border-bg-border/40 last:border-0">
                          <td className="px-1.5 py-0.5">
                            <StockLink code={s.code} name={f?.name ?? null} hasDetail={f?.has_detail ?? true} />
                            {!!s.also_in.length && <span className="ml-1 text-[10px] text-text-muted" title={`也在：${s.also_in.join('、')}`}>+{s.also_in.length}板块</span>}
                          </td>
                          <td className="px-1.5 py-0.5">
                            {s.active.map((a) => (
                              <span key={a} className={cn('mr-0.5 px-1 rounded text-[10px]', a === 'limit_up' ? 'bg-up/15 text-up' : a === 'limit_down' ? 'bg-down/15 text-down' : 'bg-bg-elevated text-text-secondary')}>{ACTIVE_ZH[a]}</span>
                            ))}
                          </td>
                          <td className={cn('px-1.5 py-0.5 text-right', NUM, pctCls(f?.pct))}>{fmtPct(f?.pct)}</td>
                          <td className={cn('px-1.5 py-0.5 text-right', NUM, pctCls(f?.pct_5d))}>{fmtPct(f?.pct_5d)}</td>
                          <td className={cn('px-1.5 py-0.5 text-right', NUM, pctCls(f?.pct_20d))}>{fmtPct(f?.pct_20d)}</td>
                          <td className={cn('px-1.5 py-0.5 text-right', NUM)}>
                            {f?.is_limit_up ? <span className="text-up">{f.board_count ? `${f.board_count}板` : '涨停'} {f.first_limit_time?.slice(0, 5) ?? ''}</span> : <span className="text-text-muted">—</span>}
                          </td>
                          <td className={cn('px-1.5 py-0.5 text-right text-text-secondary', NUM)}>{f?.amount_yi != null ? `${f.amount_yi.toFixed(1)}亿` : '—'}</td>
                          <td className={cn('px-1.5 py-0.5 text-[11px]', SYS_TONE[s.system_response])}>{data.response_labels[s.system_response]}</td>
                          <td className="px-1.5 py-0.5">
                            {!readOnly && (
                              <div className="flex items-center gap-1.5 text-[11px]">
                                <button onClick={() => onWatch(s.code, f?.name ?? null)} className="text-accent">观察</button>
                                <button onClick={() => onPlan({ stock_code: s.code, stock_name: f?.name ?? null, leader_code: code,
                                                                relation_note: `同属${TYPE_ZH[g.sector.type] ?? ''}板块「${g.sector.name}」（${date} 当天${s.active.map((a) => ACTIVE_ZH[a]).join('、')}）` })}
                                        className="text-purple-300">拟交易</button>
                                {adopted.has(key) ? <span className="text-text-muted">已记</span> : (
                                  <button onClick={() => onAdopt(g.sector, s.code, f?.name ?? null, s.active.map((a) => ACTIVE_ZH[a]).join('、'))}
                                          className="text-text-secondary hover:text-text-primary" title="记进这只高标的关联知识库（跨日保留）">记为关联</button>
                                )}
                              </div>
                            )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
