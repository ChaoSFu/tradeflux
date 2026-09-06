/**
 * 核心股 —— 三种不同的「核心」，分三个 Tab，不混成一个榜。
 *
 *   活跃龙头  高辨识度连板龙头，用生命周期状态机描述它走到哪
 *   涨停核心  今天在涨停梯队里的，看当下高度和**可交易性**
 *   趋势龙头  成交额承接得住的，看容量而不是弹性
 *
 * 三者的选股逻辑、风险、买点完全不同。V1 把它们混在一个池子里排序，
 * 结果是「最强」这个词失去了含义。
 *
 * ## Leadership 与 Tradability 分开
 *
 * 一字涨停可以是当天最强的票，同时是**买不进去的票**。把这两件事合成一个
 * 「强度」，等于让人对着一个成交不了的信号做决策。
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { fetchLeaderCycle, fetchLimitMoves } from '@/api/stocks'
import { fetchTurnoverOverview } from '@/api/turnover'
import { fetchRegulatoryWatchlist } from '@/api/watchlist'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import { DataGap, Empty } from '../components/DataGap'
import { GROUP_META, LIFECYCLE_ZH, groupOf, type CoreGroup } from '../domain/coreStock'

const Q = { staleTime: 10 * 60 * 1000 }
const NUM = 'font-mono tabular-nums'
type Tab = 'active' | 'limit' | 'trend'

export default function CoreStocks() {
  const [tab, setTab] = useState<Tab>('active')
  const reg = useQuery({ queryKey: ['regulatory-watchlist'],
                         queryFn: fetchRegulatoryWatchlist, ...Q })
  const regCodes = useMemo(
    () => new Set((reg.data?.monitoring ?? []).map((r) => r.security_code)), [reg.data])

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        {([['active', '活跃龙头'], ['limit', '涨停核心'], ['trend', '趋势龙头']] as const)
          .map(([k, label]) => (
            <button key={k} onClick={() => setTab(k)}
              className={cn('text-xs px-3 py-1.5 rounded border transition-colors',
                tab === k ? 'border-accent/50 text-accent bg-accent/10'
                          : 'border-bg-border text-text-secondary hover:text-text-primary')}>
              {label}
            </button>
          ))}
      </div>
      {tab === 'active' && <ActiveLeaders regCodes={regCodes} />}
      {tab === 'limit' && <LimitCore regCodes={regCodes} />}
      {tab === 'trend' && <TrendLeaders regCodes={regCodes} />}
    </div>
  )
}

/* ── 活跃龙头 ────────────────────────────────────────────────────────── */

function ActiveLeaders({ regCodes }: { regCodes: Set<string> }) {
  const [expand, setExpand] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['leader-cycle'], queryFn: () => fetchLeaderCycle(), ...Q })

  const grouped = useMemo(() => {
    const all = [...(data?.running ?? []), ...(data?.broken ?? [])]
    const g: Record<CoreGroup, typeof all> = {
      focus: [], turning: [], downgraded: [], pending: [] }
    all.forEach((r) => g[groupOf(r.lifecycle_state)].push(r))
    return g
  }, [data])

  if (isLoading) return <LoadingRows />

  const cols = expand
    ? ['股票', '生命周期', '主板块', '本轮', 'D+', '距MA5', '距MA10', '距阶段高',
       '峰值回撤', 'RS市场20', 'ΔRS 3日', '量比5日', '换手', '监管']
    : ['股票', '生命周期', '主板块', '本轮', 'D+', '距MA5', '距阶段高',
       '峰值回撤', 'RS市场20', 'ΔRS 3日', '换手', '监管']

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-[11px] text-text-muted">
          生命周期是透明状态机（price_v1_1），不是黑箱评分：每个状态都能还原到
          收盘价与均线的关系。判定在后端，前端只做中文显示和分组。
        </p>
        <button onClick={() => setExpand((v) => !v)}
          className="text-[11px] text-accent shrink-0 ml-3">
          {expand ? '收起' : '展开全部事实'}
        </button>
      </div>

      {(['focus', 'turning', 'downgraded', 'pending'] as CoreGroup[]).map((g) => {
        const rows = grouped[g]
        if (!rows.length) return null
        const meta = GROUP_META[g]
        return (
          <section key={g} className="card p-4">
            <div className="flex items-baseline gap-2">
              <h3 className={cn('text-sm',
                g === 'focus' ? 'text-accent' : 'text-text-primary')}>{meta.label}</h3>
              <span className={cn('text-[11px]', NUM, 'text-text-muted')}>{rows.length}</span>
            </div>
            <p className="text-[11px] text-text-muted mt-0.5">{meta.hint}</p>
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-xs" style={{ minWidth: expand ? 1100 : 880 }}>
                <thead>
                  <tr className="text-[10px] text-text-muted">
                    {cols.map((h) => (
                      <th key={h} className="px-2 py-1.5 text-left font-medium
                                             border-b border-bg-border whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => <LeaderRow key={r.code} r={r} expand={expand}
                                              reg={regCodes.has(r.code)} />)}
                </tbody>
              </table>
            </div>
          </section>
        )
      })}
    </div>
  )
}

const dist = (close: number | null, ma: number | null) =>
  close && ma && ma > 0 ? `${((close / ma - 1) * 100).toFixed(1)}%` : '—'
const num = (v: number | null | undefined, d = 1, suf = '') =>
  v === null || v === undefined ? '—' : `${v.toFixed(d)}${suf}`

function LeaderRow({ r, expand, reg }: { r: any; expand: boolean; reg: boolean }) {
  return (
    <tr className="border-b border-bg-border/50 last:border-0 hover:bg-bg-elevated/40">
      <td className="px-2 py-1.5 whitespace-nowrap">
        <Link to={`/stocks/${r.code}`} className="text-text-primary hover:text-accent">
          {r.name || r.code}
        </Link>
        <span className={cn('ml-1 text-[10px] text-text-muted', NUM)}>{r.code}</span>
      </td>
      <td className="px-2 py-1.5 whitespace-nowrap">
        {LIFECYCLE_ZH[r.lifecycle_state ?? 'UNKNOWN']}
        {r.transitioned_today && (
          <span className="ml-1 text-[9px] px-1 rounded bg-current/15">今日</span>
        )}
      </td>
      <td className="px-2 py-1.5 text-text-secondary truncate max-w-[7rem]">
        {r.sector_name || '—'}
      </td>
      <td className={cn('px-2 py-1.5', NUM)}>{r.peak_board_count ?? '—'}</td>
      <td className={cn('px-2 py-1.5', NUM)}>
        {r.days_since_break === null ? '—' : `D+${r.days_since_break}`}
      </td>
      <td className={cn('px-2 py-1.5', NUM)}>{dist(r.latest_close, r.ma5)}</td>
      {expand && <td className={cn('px-2 py-1.5', NUM)}>{dist(r.latest_close, r.ma10)}</td>}
      <td className={cn('px-2 py-1.5', NUM)}>{num(r.dist_to_post_break_high, 1, '%')}</td>
      <td className={cn('px-2 py-1.5', NUM)}>{num(r.peak_drawdown, 1, '%')}</td>
      <td className={cn('px-2 py-1.5', NUM)}>{num(r.rs_market_20)}</td>
      <td className={cn('px-2 py-1.5', NUM)}>{num(r.rs_market_20_delta_3d)}</td>
      {expand && <td className={cn('px-2 py-1.5', NUM)}>{num(r.volume_ratio_5d, 2)}</td>}
      <td className={cn('px-2 py-1.5', NUM)}>{num(r.turnover_rate, 1, '%')}</td>
      <td className="px-2 py-1.5">
        {reg ? <span className="text-down">监管中</span>
             : <span className="text-text-muted">—</span>}
      </td>
    </tr>
  )
}

/* ── 涨停核心 ────────────────────────────────────────────────────────── */

function LimitCore({ regCodes }: { regCodes: Set<string> }) {
  const { data, isLoading } = useQuery({
    queryKey: ['limit-moves-v2'], queryFn: () => fetchLimitMoves({ page_size: 200, move_type: 'limit_up' }), ...Q })
  if (isLoading) return <LoadingRows />
  const items = (data?.items ?? []) as any[]
  const up = items.filter((s) => s.today_is_limit_up)
  if (!up.length) return <Empty text="今日无涨停" />

  // **按连板数分梯队**，不做加权排序：梯队本身就是市场给的结构
  const ladder = new Map<number, any[]>()
  up.forEach((s) => {
    const n = s.board_count_current ?? 1
    if (!ladder.has(n)) ladder.set(n, [])
    ladder.get(n)!.push(s)
  })
  const tiers = [...ladder.entries()].sort((a, b) => b[0] - a[0])

  return (
    <div className="space-y-3">
      <p className="text-[11px] text-text-muted">
        按连板梯队排，不做加权排序 —— 梯队是市场自己给的结构。
        Leadership（板数高）和 Tradability（能不能买到）分开显示：
        一字涨停可以同时是最强的和买不进去的。
      </p>
      {tiers.map(([n, rows]) => (
        <section key={n} className="card p-4">
          <div className="flex items-baseline gap-2">
            <h3 className="text-sm text-text-primary">{n} 板</h3>
            <span className={cn('text-[11px] text-text-muted', NUM)}>{rows.length}</span>
          </div>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs" style={{ minWidth: 760 }}>
              <thead>
                <tr className="text-[10px] text-text-muted">
                  {['股票', '主板块', '可交易性', '今日涨幅', '换手', '60日最高板',
                    '近10日涨停', '监管'].map((h) => (
                    <th key={h} className="px-2 py-1.5 text-left font-medium
                                           border-b border-bg-border whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((s) => (
                  <tr key={s.code} className="border-b border-bg-border/50 last:border-0">
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      <Link to={`/stocks/${s.code}`}
                            className="text-text-primary hover:text-accent">{s.name}</Link>
                      <span className={cn('ml-1 text-[10px] text-text-muted', NUM)}>{s.code}</span>
                    </td>
                    <td className="px-2 py-1.5 text-text-secondary truncate max-w-[8rem]">
                      {s.sectors?.[0] ?? '—'}
                    </td>
                    <td className="px-2 py-1.5">
                      {s.today_is_one_word_limit_up
                        ? <span className="text-warn">一字·难成交</span>
                        : <span className="text-text-secondary">可成交</span>}
                    </td>
                    <td className={cn('px-2 py-1.5', NUM, 'text-up')}>
                      {num(s.today_pct_change, 2, '%')}
                    </td>
                    <td className={cn('px-2 py-1.5', NUM)}>{num(s.turnover_rate, 1, '%')}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{s.board_count_60d ?? '—'}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{s.limit_up_days_10d ?? '—'}</td>
                    <td className="px-2 py-1.5">
                      {regCodes.has(s.code) ? <span className="text-down">监管中</span>
                                            : <span className="text-text-muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  )
}

/* ── 趋势龙头 ────────────────────────────────────────────────────────── */

function TrendLeaders({ regCodes }: { regCodes: Set<string> }) {
  const { data, isLoading } = useQuery({
    queryKey: ['turnover-overview'], queryFn: () => fetchTurnoverOverview(), ...Q })
  if (isLoading) return <LoadingRows />
  const stocks = ((data as any)?.stocks ?? (data as any)?.items ?? []) as any[]

  return (
    <div className="space-y-3">
      <DataGap
        what="趋势龙头的「趋势」暂时判不出来"
        why="成交额榜的股票没有均线字段（成交额概览接口只给成交额、换手、涨跌幅）。
             成交额大 ≠ 趋势多头，所以这里不给趋势标签，只给容量事实。"
        plan="下一步复用已有的个股日线补 MA5/10/20/60、均线排列、MA20 斜率，
              那时才谈得上「趋势龙头」这个名字。"
      />
      {!stocks.length ? <Empty text="暂无成交额数据" /> : (
        <section className="card p-4">
          <h3 className="text-sm text-text-primary">成交额领先（容量 Leadership）</h3>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs" style={{ minWidth: 700 }}>
              <thead>
                <tr className="text-[10px] text-text-muted">
                  {['股票', '成交额(亿)', '今日涨幅', '换手', '趋势', '监管'].map((h) => (
                    <th key={h} className="px-2 py-1.5 text-left font-medium
                                           border-b border-bg-border whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {stocks.slice(0, 30).map((s: any) => (
                  <tr key={s.code} className="border-b border-bg-border/50 last:border-0">
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      <Link to={`/stocks/${s.code}`}
                            className="text-text-primary hover:text-accent">
                        {s.name || s.code}
                      </Link>
                    </td>
                    <td className={cn('px-2 py-1.5', NUM)}>
                      {s.amount ? (s.amount / 1e8).toFixed(1) : '—'}
                    </td>
                    <td className={cn('px-2 py-1.5', NUM)}>{num(s.pct_change, 2, '%')}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{num(s.turnover_rate, 1, '%')}</td>
                    <td className="px-2 py-1.5 text-warn">数据不足</td>
                    <td className="px-2 py-1.5">
                      {regCodes.has(s.code) ? <span className="text-down">监管中</span>
                                            : <span className="text-text-muted">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  )
}
