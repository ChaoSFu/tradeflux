import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import { cn } from '@/utils/cn'
import type { Stock, LimitUpRadarSector } from '@/types'

const NUM = 'font-mono tabular-nums'
const TD = 'px-2 py-1 whitespace-nowrap'
const TH = 'px-2 py-1 text-left font-medium text-text-muted whitespace-nowrap border-b border-bg-border'

/** 与后端 eastmoney_fetcher.get_limit_pct 的前缀口径一致 */
type Market = 'all' | 'main' | 'chinext' | 'star' | 'bj'
const marketOf = (code: string): Exclude<Market, 'all'> =>
  /^(4|8|92)/.test(code) ? 'bj'
    : code.startsWith('688') ? 'star'
      : code.startsWith('300') || code.startsWith('301') ? 'chinext' : 'main'
const MARKETS: { k: Market; label: string }[] = [
  { k: 'all', label: '全部' }, { k: 'main', label: '主板' },
  { k: 'chinext', label: '创业板' }, { k: 'star', label: '科创板' },
  { k: 'bj', label: '北交所' },
]
const MARKET_ZH: Record<Exclude<Market, 'all'>, string> = {
  main: '主板', chinext: '创业板', star: '科创板', bj: '北交所',
}

const pct = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`
const tone = (v: number | null | undefined) =>
  v === null || v === undefined ? 'text-text-muted' : v > 0 ? 'text-up' : v < 0 ? 'text-down' : ''

/**
 * 连板梯队个股清单。板数分组由后端的 `today_board_count` 决定，前端不重算。
 *
 * 高弹性市场（创业板/科创板/北交所）**不再单独占一个大区块**，改成筛选器——
 * 它们的涨跌幅阈值不同（±20% / ±30%），跟主板的"连板"不可直接比较，所以标签
 * 保留在每一行上。
 *
 * 换手率 Stock 上没有，从涨停板块雷达按代码补；补不到就是 —，不猜。
 */
export function LadderStockList({ upStocks, sectors }: {
  upStocks: Stock[]
  sectors: LimitUpRadarSector[]
}) {
  const [market, setMarket] = useState<Market>('all')
  const [q, setQ] = useState('')

  /** code → 换手率。同一只票可能挂在多个板块下，取第一个非空 */
  const turnover = useMemo(() => {
    const m = new Map<string, number>()
    for (const s of sectors)
      for (const st of s.today_limit_up_stocks)
        if (st.turnover_rate !== null && !m.has(st.code)) m.set(st.code, st.turnover_rate)
    return m
  }, [sectors])

  const groups = useMemo(() => {
    const kw = q.trim().toLowerCase()
    const rows = upStocks.filter((s) =>
      (market === 'all' || marketOf(s.code) === market) &&
      (!kw || s.code.includes(kw) || (s.name ?? '').toLowerCase().includes(kw)))
    const by = new Map<number, Stock[]>()
    for (const s of rows) {
      // 连板数缺失 → 归到 0 这一组并单独标出来，**不当成首板**
      const b = s.today_board_count ?? 0
      by.set(b, [...(by.get(b) ?? []), s])
    }
    return [...by.entries()].sort((a, b) => b[0] - a[0])
  }, [upStocks, market, q])

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex gap-1">
          {MARKETS.map((m) => (
            <button key={m.k} onClick={() => setMarket(m.k)}
                    className={cn('px-2 py-0.5 rounded text-[11px] border transition-colors',
                      market === m.k
                        ? 'border-accent/60 bg-accent/10 text-accent'
                        : 'border-bg-border text-text-muted hover:text-text-secondary')}>
              {m.label}
            </button>
          ))}
        </div>
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3 h-3 text-text-muted" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="代码 / 名称"
                 className="pl-6 pr-2 py-0.5 text-[11px] bg-bg-elevated border border-bg-border
                            rounded w-40 text-text-primary placeholder:text-text-muted" />
        </div>
      </div>

      {groups.length === 0
        ? <div className="text-xs text-text-muted py-4">没有符合条件的股票</div>
        : groups.map(([board, rows]) => (
          <div key={board} className="space-y-1">
            <div className="text-[11px] font-medium">
              <span className={board >= 3 ? 'text-up' : 'text-text-secondary'}>
                {board > 0 ? `${board}板` : '连板数未知'}
              </span>
              <span className="text-text-muted ml-1.5">{rows.length} 只</span>
              {board === 0 && (
                <span className="text-text-muted/70 ml-1.5">
                  （快照里没有连板数，不当成首板）
                </span>
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]" style={{ minWidth: 720 }}>
                <thead><tr>
                  {['股票', '市场', '所属板块', '今日', '10日', '20日', '60日',
                    '换手', '一字', '异动空间'].map((h) => <th key={h} className={TH}>{h}</th>)}
                </tr></thead>
                <tbody>
                  {rows.map((s) => (
                    <tr key={s.code} className="border-b border-bg-border/40 last:border-0">
                      <td className={TD}>
                        <span className="text-text-primary">{s.name}</span>
                        <span className={cn('ml-1 text-text-muted', NUM)}>{s.code}</span>
                      </td>
                      <td className={cn(TD, 'text-text-muted')}>
                        {MARKET_ZH[marketOf(s.code)]}
                      </td>
                      <td className={cn(TD, 'max-w-[12rem] truncate text-text-muted')}
                          title={(s.sectors ?? []).join('、')}>
                        {(s.sectors ?? []).join('、') || '—'}
                      </td>
                      <td className={cn(TD, NUM, tone(s.today_pct_change))}>
                        {pct(s.today_pct_change)}
                      </td>
                      <td className={cn(TD, NUM, tone(s.pct_change_10d))}>{pct(s.pct_change_10d, 1)}</td>
                      <td className={cn(TD, NUM, tone(s.pct_change_20d))}>{pct(s.pct_change_20d, 1)}</td>
                      <td className={cn(TD, NUM, tone(s.pct_change_60d))}>{pct(s.pct_change_60d, 1)}</td>
                      <td className={cn(TD, NUM, 'text-text-secondary')}>
                        {turnover.has(s.code) ? `${turnover.get(s.code)!.toFixed(1)}%` : '—'}
                      </td>
                      <td className={cn(TD, NUM)}>{s.today_is_one_word_limit_up ? '一字' : '—'}</td>
                      {/* severe_up_room：距「涨幅严重异动」还剩多少空间；null=已触发或算不出 */}
                      <td className={cn(TD, NUM, 'text-warn/80')}>
                        {s.severe_up_room === null ? '—' : `${s.severe_up_room.toFixed(0)}%`}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      <p className="text-[10px] text-text-muted">
        换手率来自涨停板块雷达（按代码补），补不到显示 —。
        高弹性市场涨跌幅阈值不同（创业板/科创板 ±20%、北交所 ±30%），
        它们的连板数跟主板不可直接比较，所以市场标签保留在每一行。
      </p>
    </div>
  )
}
