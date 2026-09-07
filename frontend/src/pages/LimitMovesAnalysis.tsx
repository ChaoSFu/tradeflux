/**
 * 涨跌停分析 Limit Moves Analysis
 *
 * 把原来四个页面（涨跌停概览 / 破局雷达 / 涨停板块雷达 / 市场效应）整合成一个，
 * 回答四个问题、一条信息链：
 *
 *     今天有多少涨跌停 → 高度和梯队什么结构 → 集中在哪些板块 → 昨天的强势今天怎么样
 *     市场宽度         → 高度与梯队           → 板块集中       → 跨日反馈
 *
 * **不新增任何评分。** 没有情绪温度、涨停强度分、投机指数、板块热度分。每一列
 * 都是一个能追到后端字段的市场事实，排序就是那一列的数值。
 *
 * 四个数据源来自四张不同的表、四个不同的更新时刻。把它们并排摆出来这件事本身
 * 就在暗示"这是同一个时点的事实"，所以顶部那条 DataFreshnessBar 不是装饰——
 * 它的唯一职责是把这个暗示拆掉。
 *
 * 旧的四个页面一行没动，路由都还在。
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { cn } from '@/utils/cn'
import {
  fetchLimitMoves, fetchLimitMovesTrend,
  fetchAdvanceLadder, fetchSectorContinuation,
} from '@/api/stocks'
import { fetchHeightSeries } from '@/api/marketTrend'
import { fetchLimitUpRadar } from '@/api/limitUpRadar'
import { fetchMarketEffectLatest } from '@/api/marketEffects'
import { QueryState, queryFailed } from '@/components/limitMovesAnalysis/QueryState'
import { DataFreshnessBar, type SourceStatus } from '@/components/limitMovesAnalysis/DataFreshnessBar'
import { MarketSnapshot, type Kpi } from '@/components/limitMovesAnalysis/MarketSnapshot'
import { HeightFrontierPanel } from '@/components/limitMovesAnalysis/HeightFrontierPanel'
import { BoardLadderPanel } from '@/components/limitMovesAnalysis/BoardLadderPanel'
import { LadderHeatmap } from '@/components/limitMovesAnalysis/LadderHeatmap'
import { SectorLimitTable } from '@/components/limitMovesAnalysis/SectorLimitTable'
import { LadderStockList } from '@/components/limitMovesAnalysis/LadderStockList'
import { CohortFeedbackTable } from '@/components/limitMovesAnalysis/CohortFeedbackTable'
import { LimitHistoryChart } from '@/components/limitMovesAnalysis/LimitHistoryChart'
import { AdvanceLadderPanel } from '@/components/limitMovesAnalysis/AdvanceLadderPanel'
import { SectorContinuationPanel } from '@/components/limitMovesAnalysis/SectorContinuationPanel'
import { buildSectorRows } from '@/components/limitMovesAnalysis/sectorRows'

type Tab = 'overview' | 'sectors' | 'history'
const TABS: { k: Tab; label: string }[] = [
  { k: 'overview', label: '全景' },
  { k: 'sectors', label: '板块与个股' },
  { k: 'history', label: '历史反馈' },
]

export default function LimitMovesAnalysis() {
  const [tab, setTab] = useState<Tab>('overview')

  const up = useQuery({
    queryKey: ['lma-limit-up'],
    queryFn: () => fetchLimitMoves({ move_type: 'limit_up', page_size: 500 }),
    staleTime: 5 * 60 * 1000,
  })
  const down = useQuery({
    queryKey: ['lma-limit-down'],
    queryFn: () => fetchLimitMoves({ move_type: 'limit_down', page_size: 500 }),
    staleTime: 5 * 60 * 1000,
  })
  const height = useQuery({
    queryKey: ['lma-height'],
    queryFn: () => fetchHeightSeries(66),
    staleTime: 30 * 60 * 1000,
  })
  const radar = useQuery({
    queryKey: ['lma-radar'],
    queryFn: () => fetchLimitUpRadar({ include_core: true }),
    staleTime: 10 * 60 * 1000,
  })
  const effect = useQuery({
    queryKey: ['lma-effect'],
    queryFn: fetchMarketEffectLatest,
    staleTime: 10 * 60 * 1000,
  })
  const ladder = useQuery({
    queryKey: ['lma-advance-ladder'],
    queryFn: () => fetchAdvanceLadder(),
    staleTime: 10 * 60 * 1000,
  })
  const continuation = useQuery({
    queryKey: ['lma-sector-continuation'],
    queryFn: () => fetchSectorContinuation(),
    staleTime: 10 * 60 * 1000,
  })
  const trend = useQuery({
    queryKey: ['lma-trend'],
    queryFn: () => fetchLimitMovesTrend(60),
    staleTime: 30 * 60 * 1000,
  })

  const points = height.data?.points ?? []
  const last = points.length ? points[points.length - 1] : null
  const upStocks = up.data?.items ?? []
  const downStocks = down.data?.items ?? []
  const summary = radar.data?.summary

  const sources: SourceStatus[] = [
    {
      label: '涨跌停总览',
      tradeDate: up.data?.trade_date ?? null,
      // StockDailySnapshot 没有"这一行什么时候刷新的"，只有 is_settled
      refreshedAt: null,
      settled: up.data?.is_settled ?? null,
      source: 'StockDailySnapshot',
      error: queryFailed(up),
    },
    {
      label: '市场高度',
      tradeDate: last?.date ?? null,
      refreshedAt: null,
      source: 'StockDailySnapshot · height series',
      error: queryFailed(height),
    },
    {
      label: '涨停板块明细',
      tradeDate: radar.data?.trade_date ?? null,
      refreshedAt: radar.data?.refreshed_at ?? null,
      source: radar.data?.source ?? 'Eastmoney limit-up detail',
      note: radar.data && radar.data.history_lag_days >= 2
        ? `10/20/60日涨停次数只算到 ${radar.data.history_as_of}，落后 ${radar.data.history_lag_days} 个交易日`
        : null,
      error: queryFailed(radar),
    },
    {
      label: '市场效应',
      tradeDate: effect.data?.trade_date ?? null,
      refreshedAt: null,
      source: 'MarketEffectDaily',
      error: queryFailed(effect),
    },
  ]

  // ── KPI：**只摆事实，缺就是 —** ───────────────────────────────────────────
  // **没拿到名单时是 null，不是 0。** upStocks 为空数组既可能是"今天真没有"，
  // 也可能是"请求还没回来 / 挂了"，.length 会把后两种算成 0
  const oneWordUp = up.data ? upStocks.filter((s) => s.today_is_one_word_limit_up).length : null
  const oneWordDown = down.data ? downStocks.filter((s) => s.today_is_one_word_limit_down).length : null
  const kpis: Kpi[] = [
    { label: '涨停', value: up.data?.total ?? null, suffix: '只', tone: 'up',
      hint: '涨跌停总览口径（StockDailySnapshot.is_limit_up）' },
    { label: '跌停', value: down.data?.total ?? null, suffix: '只', tone: 'down' },
    { label: '首板', value: summary?.first_board_count ?? null, suffix: '只',
      hint: '来自涨停板块雷达明细池，跟左边的涨停总数不是同一个口径' },
    { label: '连板', value: summary?.continuation_count ?? null, suffix: '只' },
    { label: '最高板', value: last?.height ?? null, suffix: '板', tone: 'up',
      hint: '破局雷达口径：不含 ST / 退市整理期 / 北交所' },
    { label: '3板以上', value: last?.multi_board_count ?? null, suffix: '只' },
    { label: '炸板', value: summary?.broken_count ?? null, suffix: '只' },
    // seal_rate 后端已经是百分数（0~100），不要再乘 100
    { label: '封板率',
      value: summary?.seal_rate == null ? null : `${summary.seal_rate.toFixed(1)}%` },
    { label: '一字涨停', value: oneWordUp, suffix: '只', tone: 'up' },
    { label: '一字跌停', value: oneWordDown, suffix: '只', tone: 'down' },
  ]

  // 两个涨停口径对不上时并列显示，**不拿一个覆盖另一个**
  const poolCount = summary?.limit_up_count
  const totalCount = up.data?.total
  const countMismatch = poolCount != null && totalCount != null && poolCount !== totalCount

  const sectorRows = useMemo(
    () => buildSectorRows(radar.data?.sectors ?? [], downStocks),
    [radar.data?.sectors, downStocks],
  )

  const anyPending = up.isPending || down.isPending || height.isPending || radar.isPending



  return (
    <div className="space-y-3 animate-fade-in">
      <DataFreshnessBar sources={sources} />

      <div className="flex gap-1">
        {TABS.map((t) => (
          <button key={t.k} onClick={() => setTab(t.k)}
                  className={cn('px-3 py-1.5 rounded text-xs font-medium transition-colors',
                    tab === t.k ? 'bg-accent/15 text-accent'
                                : 'text-text-muted hover:text-text-secondary hover:bg-bg-elevated/50')}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="space-y-3">
          <div className="card p-3 space-y-2">
            <div className="flex items-baseline justify-between flex-wrap gap-2">
              <span className="text-xs font-semibold text-text-primary">今日市场涨跌停结构</span>
              {countMismatch && (
                <span className="text-[10px] text-warn"
                      title="总览按 StockDailySnapshot.is_limit_up 统计全市场；明细池来自东财涨停明细接口，两者观测时点和收录范围都不同">
                  涨停总览 {totalCount} · 明细池 {poolCount} —— 两者观测时点不同
                </span>
              )}
            </div>
            <QueryState qs={[up, down]}>
              <MarketSnapshot items={kpis} />
            </QueryState>
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <div className="card p-3">
              <QueryState qs={[height]}
                          isEmpty={!points.length}>
                <HeightFrontierPanel points={points}
                                     frontierWindow={height.data?.frontier_window ?? 20} />
              </QueryState>
            </div>
            <div className="card p-3 space-y-3">
              <QueryState qs={[height]}
                          isEmpty={!last}>
                <BoardLadderPanel point={last} />
              </QueryState>
              {/* 梯队是截面，晋级是这个截面怎么来的——放一起才读得出"厚了还是薄了" */}
              <div className="pt-2 border-t border-bg-border/60">
                <QueryState qs={[ladder]} rows={2}>
                  {ladder.data && <AdvanceLadderPanel data={ladder.data} />}
                </QueryState>
              </div>
            </div>
          </div>

          <div className="card p-3">
            <QueryState qs={[height]}
                        isEmpty={!points.length} rows={2}>
              <LadderHeatmap points={points} />
            </QueryState>
          </div>

          <div className="card p-3">
            <QueryState qs={[radar, down]}
                        isEmpty={!sectorRows.length}
                        emptyText="今天没有板块达到涨停雷达门槛，也没有跌停股">
              <SectorLimitTable rows={sectorRows}
                                radarDate={radar.data?.trade_date ?? null}
                                downDate={down.data?.trade_date ?? null} />
            </QueryState>
          </div>

          <div className="card p-3">
            <QueryState qs={[effect]}
                        isEmpty={!effect.data}>
              {effect.data && <CohortFeedbackTable data={effect.data} />}
            </QueryState>
          </div>
        </div>
      )}

      {tab === 'sectors' && (
        <div className="space-y-3">
          <div className="card p-3">
            <QueryState qs={[up]} isEmpty={!upStocks.length}
                        emptyText="今天没有涨停股">
              <div className="space-y-2">
                <span className="text-xs font-semibold text-text-primary">连板梯队 · 个股</span>
                <LadderStockList upStocks={upStocks} sectors={radar.data?.sectors ?? []} />
              </div>
            </QueryState>
          </div>
          <div className="card p-3">
            <QueryState qs={[continuation]} rows={3}>
              {continuation.data && <SectorContinuationPanel data={continuation.data} />}
            </QueryState>
          </div>
          <div className="card p-3">
            <QueryState qs={[radar, down]}
                        isEmpty={!sectorRows.length}>
              <SectorLimitTable rows={sectorRows}
                                radarDate={radar.data?.trade_date ?? null}
                                downDate={down.data?.trade_date ?? null} />
            </QueryState>
          </div>
        </div>
      )}

      {tab === 'history' && (
        <div className="space-y-3">
          <div className="card p-3">
            <QueryState qs={[effect]} isEmpty={!effect.data}>
              {effect.data && <CohortFeedbackTable data={effect.data} />}
            </QueryState>
          </div>
          <div className="card p-3">
            <QueryState qs={[trend, height]}
                        isEmpty={!trend.data?.length}>
              <LimitHistoryChart trend={trend.data ?? []} heights={points} />
            </QueryState>
          </div>
        </div>
      )}

      {!anyPending && (
        <p className="text-[10px] text-text-muted px-1">
          这一页只展示市场事实，不合成任何评分，也不给买卖建议。
          原来的涨跌停概览 / 破局雷达 / 涨停板块雷达 / 市场效应四个页面都还在，路由未变。
        </p>
      )}
    </div>
  )
}
