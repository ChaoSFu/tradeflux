/**
 * 交易台 —— V2 首页。
 *
 * 目的：每天打开后用最少的信息回答三件事——今天有没有交易许可、主线在哪、
 * 该盯哪几只。**它必须直接能用，不能变成旧页面的链接集合。**
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { fetchMarketTrend } from '@/api/marketTrend'
import { fetchSectors, SECTORS_LITE_KEY } from '@/api/sectors'
import { fetchLeaderCycle } from '@/api/stocks'
import { fetchRegulatoryWatchlist } from '@/api/watchlist'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import { GateCard } from '../components/GateCard'
import { marketGate } from '../domain/marketGate'
import { rankMainlines, sectorGate } from '../domain/sectorGate'
import { LIFECYCLE_ZH, groupOf } from '../domain/coreStock'
import { gateTone, gateText } from '../domain/gate'

const Q = { staleTime: 10 * 60 * 1000 }

export default function TradingConsole() {
  const market = useQuery({ queryKey: ['market-trend'], queryFn: () => fetchMarketTrend(), ...Q })
  const sectors = useQuery({ queryKey: [...SECTORS_LITE_KEY], queryFn: () => fetchSectors(false), ...Q })
  const cycle = useQuery({ queryKey: ['leader-cycle'], queryFn: () => fetchLeaderCycle(), ...Q })
  const reg = useQuery({ queryKey: ['regulatory-watchlist'], queryFn: fetchRegulatoryWatchlist, ...Q })

  const mGate = marketGate(market.data)
  const mainlines = useMemo(
    () => rankMainlines(sectors.data?.items ?? []).slice(0, 5), [sectors.data])
  const sGate = sectorGate(mainlines[0]?.sector)

  const focus = useMemo(() => {
    const all = [...(cycle.data?.running ?? []), ...(cycle.data?.broken ?? [])]
    return all.filter((r) => groupOf(r.lifecycle_state) === 'focus')
  }, [cycle.data])

  const monitoring = reg.data?.monitoring.length ?? 0
  const regCodes = new Set((reg.data?.monitoring ?? []).map((r) => r.security_code))

  return (
    <div className="space-y-4">
      {/* ── 今日交易许可 ────────────────────────────────────────────── */}
      {/* 768~1024 之间四列会把 gate 卡挤到文字竖排，中间加一档两列 */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
        <GateCard name="市场" gate={mGate} compact />
        <GateCard name="主线" gate={sGate} compact />
        <div className="card p-3">
          <div className="flex items-baseline gap-2">
            <span className="text-sm text-text-primary">核心股</span>
            <span className={cn('text-sm font-medium',
              gateTone(focus.length ? 'PASS' : 'WAIT'))}>
              {gateText(focus.length ? 'PASS' : 'WAIT')}
            </span>
            <span className="text-[11px] text-text-secondary ml-auto">
              重点跟踪 {focus.length} 只
            </span>
          </div>
          <div className="mt-2 text-[11px] text-text-muted">
            「重点跟踪」只表示值得占用今天的注意力，不等于可以买
          </div>
        </div>
        <div className="card p-3">
          <div className="flex items-baseline gap-2">
            <span className="text-sm text-text-primary">监管</span>
            <span className="text-[11px] text-text-secondary ml-auto">
              监管中 {monitoring} 只
            </span>
          </div>
          <div className="mt-2 text-[11px] text-text-muted">
            市场级监管态度：<span className="text-warn">暂无结构化数据</span>
            <div>不能用个股监管数量推断管理层态度</div>
          </div>
        </div>
      </div>

      {/* ── 主线 ──────────────────────────────────────────────────── */}
      <section className="card p-4">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm text-text-primary">主线</h2>
          <Link to="/v2/mainlines" className="text-[11px] text-accent">全部 →</Link>
        </div>
        <p className="text-[11px] text-warn mt-1">
          板块没有均线数据（板块指数历史未回填），趋势判不出来。
          下面只是「领先证据」的排序，不是趋势排名——涨幅为正不等于均线多头。
        </p>
        {sectors.isLoading ? <LoadingRows /> : (
          <div className="mt-3 space-y-2">
            {mainlines.map((e, i) => (
              <div key={e.sector.code} className="flex items-center gap-3 text-xs">
                <span className={cn('w-5 text-center', i === 0 ? 'text-accent' : 'text-text-muted')}>
                  {i + 1}
                </span>
                <span className="text-text-primary min-w-[5rem] shrink-0 truncate">{e.sector.name}</span>
                <span className="text-text-secondary flex-1 truncate">
                  {e.hits.join('、') || '—'}
                </span>
                <span className="text-text-muted font-mono tabular-nums">
                  涨停{e.sector.limit_up_count} · 强势{e.sector.strong_stock_count}
                </span>
              </div>
            ))}
            {!mainlines.length && <div className="text-text-muted text-xs">今日无领先板块</div>}
          </div>
        )}
      </section>

      {/* ── 核心股 ────────────────────────────────────────────────── */}
      <section className="card p-4">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm text-text-primary">核心股 · 重点跟踪</h2>
          <Link to="/v2/core" className="text-[11px] text-accent">全部 →</Link>
        </div>
        {cycle.isLoading ? <LoadingRows /> : focus.length === 0 ? (
          <div className="text-text-muted text-xs mt-3">今日没有处于重点跟踪状态的核心股</div>
        ) : (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-xs" style={{ minWidth: 640 }}>
              <thead>
                <tr className="text-[10px] text-text-muted">
                  {['股票', '生命周期', '主板块', 'D+', '距MA5', 'RS市场20', '监管'].map((h) => (
                    <th key={h} className="px-2 py-1.5 text-left font-medium
                                           border-b border-bg-border">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {focus.slice(0, 8).map((r) => {
                  const maPos = r.latest_close && r.ma5
                    ? ((r.latest_close / r.ma5 - 1) * 100).toFixed(1) + '%' : '—'
                  return (
                    <tr key={r.code} className="border-b border-bg-border/50 last:border-0">
                      <td className="px-2 py-1.5">
                        <Link to={`/stocks/${r.code}`} className="text-text-primary hover:text-accent">
                          {r.name || r.code}
                        </Link>
                        <span className="ml-1 text-[10px] text-text-muted font-mono">{r.code}</span>
                      </td>
                      <td className="px-2 py-1.5 text-accent">
                        {LIFECYCLE_ZH[r.lifecycle_state ?? 'UNKNOWN']}
                      </td>
                      <td className="px-2 py-1.5 text-text-secondary truncate max-w-[7rem]">
                        {r.sector_name || '—'}
                      </td>
                      <td className="px-2 py-1.5 font-mono tabular-nums text-text-secondary">
                        {r.days_since_break === null ? '—' : `D+${r.days_since_break}`}
                      </td>
                      <td className="px-2 py-1.5 font-mono tabular-nums">{maPos}</td>
                      <td className="px-2 py-1.5 font-mono tabular-nums">
                        {r.rs_market_20 === null ? '—' : r.rs_market_20.toFixed(1)}
                      </td>
                      <td className="px-2 py-1.5">
                        {regCodes.has(r.code)
                          ? <span className="text-down">监管中</span>
                          : <span className="text-text-muted">—</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <p className="text-[11px] text-text-muted">
        SIGNAL READY ≠ 自动交易 ≠ 预测上涨 ≠ 盈利保证。它只表示四道 gate 没有互相
        矛盾，可以进入人工买点确认。判定规则见 <Link to="/v2/model" className="text-accent">模型说明</Link>。
      </p>
    </div>
  )
}
