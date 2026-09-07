import { Fragment, useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { fetchMarketState, fetchMarketHistory, fetchProfitEffect } from '@/api/marketState'
import { fetchStrongPool, fetchLifecycleEffect } from '@/api/stocks'
import { LIFECYCLE_ZH } from '@/lib/lifecycle'
import LeaderCyclePanel from '@/components/stockPool/LeaderCyclePanel'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { EmotionChart } from '@/components/charts/EmotionChart'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { PhaseTag } from '@/components/common/PhaseTag'
import { RiskBadge } from '@/components/common/RiskBadge'
import { SectorSection, buildSectorGroups } from '@/components/common/SectorSection'
import {
  MARKET_PHASE_LABELS, EMOTION_CYCLE_LABELS, ACTION_LABELS,
  ACTION_COLORS, LEADER_TYPE_LABELS, SIGNAL_TYPE_LABELS,
  PHASE_COLORS, pct, PHASE_NAME_TO_NUM,
} from '@/utils/format'
import { cn } from '@/utils/cn'
import { TrendingUp, Zap, ChevronDown, ChevronUp, Activity } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { RiskLevel, ProfitEffectGroup, SectorProfitEffect, Stock } from '@/types'
import { useSectorTags, type SectorTagData } from '@/hooks/useSectorTags'
import { useDragonStocks } from '@/hooks/useDragonStocks'
import { useLeaderUniverseMaxes, getLeaderTags } from '@/hooks/useLeaderUniverseMaxes'
import { SectorRankTags, LeaderTag, RegulatoryTag, YesterdayLimitTag, SevereTargetTag } from '@/components/common/SectorTags'
import { useRegulatoryStatus } from '@/hooks/useRegulatoryStatus'
import { useSevereTargets } from '@/hooks/useSevereTargets'

const PHASE_BADGE: Record<string, 'up' | 'down' | 'warn' | 'dragon' | 'accent'> = {
  bull_frenzy: 'dragon',
  warm: 'up',
  neutral: 'accent',
  caution: 'warn',
  bear_fear: 'down',
}

// ─── Profit effect helpers ────────────────────────────────────────────────────

function pctColor(v: number) {
  if (v > 0) return 'text-up'
  if (v < 0) return 'text-down'
  return 'text-text-secondary'
}

function pctSign(v: number) {
  return v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`
}

/** Horizontal stacked bar: up (green) | flat (muted) | down (red) */
function UpDownBar({ up, flat, down }: { up: number; flat: number; down: number }) {
  const total = up + flat + down
  if (total === 0) return <div className="h-2 rounded-full bg-bg-elevated w-full" />
  const upPct = (up / total) * 100
  const flatPct = (flat / total) * 100
  const downPct = (down / total) * 100
  return (
    <div className="flex h-2 rounded-full overflow-hidden w-full gap-px">
      {upPct > 0 && (
        <div className="bg-up rounded-l-full" style={{ width: `${upPct}%` }} />
      )}
      {flatPct > 0 && (
        <div className="bg-text-secondary/50" style={{ width: `${flatPct}%` }} />
      )}
      {downPct > 0 && (
        <div className="bg-down rounded-r-full" style={{ width: `${downPct}%` }} />
      )}
    </div>
  )
}

/** 有意义的空状态：解释「为什么没有」，而非裸露的「暂无」 */
function EmptyHint({ icon: Icon, title, hint }: { icon: LucideIcon; title: string; hint: string }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-8 px-4">
      <div className="w-10 h-10 rounded-full bg-bg-elevated flex items-center justify-center mb-2.5">
        <Icon className="w-5 h-5 text-text-muted" />
      </div>
      <div className="text-sm font-medium text-text-secondary">{title}</div>
      <div className="text-xs text-text-muted mt-1 max-w-[280px] leading-relaxed">{hint}</div>
    </div>
  )
}

const GROUP_STYLES: Record<string, { border: string; dot: string }> = {
  limit_up:    { border: 'border-up/30',   dot: 'bg-up' },
  oscillation: { border: 'border-accent/30', dot: 'bg-accent' },
  weakening:   { border: 'border-warn/30',  dot: 'bg-warn' },
  broken:      { border: 'border-down/30',  dot: 'bg-down' },
}

function SectorRow({
  s,
  active,
  onClick,
  tagData,
}: {
  s: SectorProfitEffect
  active?: boolean
  onClick?: () => void
  tagData?: SectorTagData
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-3 px-2 py-1.5 rounded transition-colors text-left',
        active ? 'bg-accent/10 ring-1 ring-accent/30' : 'hover:bg-bg-elevated',
      )}
    >
      <div className="w-24 shrink-0">
        <div className="text-sm text-text-primary font-medium truncate">{s.sector_name}</div>
        {tagData && (
          <div className="flex flex-wrap gap-0.5 mt-0.5">
            <SectorRankTags tagData={tagData} />
          </div>
        )}
      </div>
      <div className="flex-1">
        <UpDownBar up={s.up_count} flat={s.stock_count - s.up_count - s.down_count} down={s.down_count} />
      </div>
      <span className={cn('text-sm font-mono font-medium w-16 text-right shrink-0', pctColor(s.sector_pct_today))}>
        {pctSign(s.sector_pct_today)}
      </span>
      <span className="text-xs text-text-muted w-20 text-center shrink-0 font-mono">
        <span className="text-up">{s.up_count}</span>
        <span className="text-text-muted/60 mx-0.5">/</span>
        <span className="text-text-muted">{s.stock_count - s.up_count - s.down_count}</span>
        <span className="text-text-muted/60 mx-0.5">/</span>
        <span className="text-down">{s.down_count}</span>
      </span>
      <span className={cn('text-sm font-mono font-medium w-16 text-right shrink-0', pctColor(s.avg_pct))}>
        {pctSign(s.avg_pct)}
      </span>
      {active
        ? <ChevronUp   className="w-3.5 h-3.5 text-accent shrink-0" />
        : <ChevronDown className="w-3.5 h-3.5 text-text-muted/40 shrink-0" />
      }
    </button>
  )
}

// ─── 板块排序（赚钱效应 / 板块涨幅 / 个股数）──────────────────────────────────
type SectorSortKey = 'avg_pct' | 'sector_pct_today' | 'stock_count'
const SECTOR_SORTS: { key: SectorSortKey; label: string; title: string }[] = [
  { key: 'avg_pct',          label: '效应', title: '按赚钱效应（龙头/成员均涨幅）排序' },
  { key: 'sector_pct_today', label: '涨幅', title: '按板块涨幅排序' },
  { key: 'stock_count',      label: '只数', title: '按板块个股数排序' },
]

/** 卡片内排序：个股数恒降序；赚钱效应/板块涨幅 在赚钱卡降序、亏钱卡升序（各自展示最强效应在前）。 */
function sortSectors(list: SectorProfitEffect[], key: SectorSortKey, isLoss: boolean): SectorProfitEffect[] {
  return [...list].sort((a, b) => {
    if (key === 'stock_count') return b.stock_count - a.stock_count || b.avg_pct - a.avg_pct
    const av = a[key], bv = b[key]
    return isLoss ? av - bv : bv - av
  })
}

function SectorSortControl({ value, onChange }: { value: SectorSortKey; onChange: (k: SectorSortKey) => void }) {
  return (
    <div className="flex items-center gap-0.5">
      {SECTOR_SORTS.map((o) => (
        <button
          key={o.key}
          title={o.title}
          onClick={() => onChange(o.key)}
          className={cn(
            'px-1.5 py-0.5 rounded text-xs transition-colors',
            value === o.key ? 'bg-accent/15 text-accent font-medium' : 'text-text-muted hover:text-text-secondary',
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** 板块赚钱/亏钱效应卡片：自带排序状态（默认赚钱效应），渲染 SectorRow 列表。 */
function SectorEffectCard({
  title, sectors, isLoss, expandedName, onToggleRow, tagFor, emptyText,
}: {
  title: string
  sectors: SectorProfitEffect[]
  isLoss: boolean
  expandedName: string | null
  onToggleRow: (name: string) => void
  tagFor: (code: string) => SectorTagData | undefined
  emptyText: string
}) {
  const [sortKey, setSortKey] = useState<SectorSortKey>('avg_pct')
  const sorted = useMemo(() => sortSectors(sectors, sortKey, isLoss), [sectors, sortKey, isLoss])
  return (
    <Card
      title={`${title} (${sectors.length})`}
      action={sectors.length > 0 ? <SectorSortControl value={sortKey} onChange={setSortKey} /> : undefined}
    >
      {sorted.length > 0 ? (
        <div className="space-y-1 max-h-72 overflow-y-auto pr-1">
          {sorted.map((s) => (
            <SectorRow
              key={s.sector_code}
              s={s}
              active={expandedName === s.sector_name}
              onClick={() => onToggleRow(s.sector_name)}
              tagData={tagFor(s.sector_code)}
            />
          ))}
        </div>
      ) : (
        <div className="text-center text-text-muted text-sm py-6">{emptyText}</div>
      )}
    </Card>
  )
}

/**
 * 强势池的生命周期 —— **完整视图，不是摘要**。
 *
 * 2026-09-07 从活跃股池搬过来的。强势池该回答的问题是"过去打开过高度的龙头
 * 现在处于什么阶段"，那是概览级的问题，不是股池筛选；而活跃股池那边生命周期
 * 仍然作为**分组维度**存在（连板中/修复中/穿越成功…那排 tab），两边不重复。
 *
 * 直接挂 LeaderCyclePanel，不在这里重做一个简版——一旦有简版和完整版两套，
 * 它们迟早显示不一样的数字，这个仓库刚为此栽过。
 */
/**
 * 生命周期口径的赚钱效应。
 *
 * 上面一格是**当日**：昨天处于某状态的票，今天中位涨幅多少、红盘率多少。
 * 下面一格是**历史**：过去 60 天处于该状态之后 T+1/T+3/T+5 普遍怎么走。
 *
 * 历史那部分刻意标成「线索」而不是结论——它没做同日同池对照，也没有置信区间。
 * `scripts/evaluate_lifecycle.py` 里有完整的版本（同日同池超额、可执行超额、
 * 按周期整段重抽的 95% 区间），而那里多数状态的区间是**跨 0 的**。
 * 在界面上把这几个数字摆成结论，就等于又造了一个看起来精确的黑箱。
 */
function LifecycleEffect() {
  const { data } = useQuery({
    queryKey: ['lifecycle-effect'], queryFn: fetchLifecycleEffect,
    staleTime: 10 * 60 * 1000,
  })
  const cohorts = (data?.cohorts ?? []).filter((c) => c.count > 0)
  if (!cohorts.length && !(data?.history ?? []).length) return null

  const pct = (v: number | null) => (v === null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)
  const rate = (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(0)}%`)

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {cohorts.map((c) => (
          <div key={c.state} className="card p-3">
            <p className="text-xs text-text-secondary font-medium truncate">
              昨日{LIFECYCLE_ZH[c.state] ?? c.state}
            </p>
            <div className="flex items-baseline gap-1 mt-1.5">
              <span className={cn('text-xl font-mono font-bold',
                c.median_pct_change === null ? 'text-text-muted'
                  : c.median_pct_change >= 0 ? 'text-up' : 'text-down')}>
                {pct(c.median_pct_change)}
              </span>
              <span className="text-xs text-text-muted">{c.count}只</span>
            </div>
            <div className="text-[11px] text-text-muted mt-1">
              今日中位涨幅 · 红盘率 {rate(c.red_ratio)}
              {c.median_pct_change === null && '（样本不足，不给中位数）'}
            </div>
          </div>
        ))}
      </div>

      {(data?.history ?? []).length > 0 && (
        <div className="card p-3">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-xs text-text-secondary font-medium">
              历史前瞻（近 60 个交易日）
            </span>
            <span className="text-[11px] text-warn">
              只是线索，不是结论 —— 没做同日同池对照，也没有置信区间
            </span>
          </div>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs" style={{ minWidth: 560 }}>
              <thead>
                <tr className="text-[10px] text-text-muted">
                  {['状态', 'T+1', '胜率', 'T+3', '胜率', 'T+5', '胜率', '样本'].map((h, k) => (
                    <th key={k} className="px-2 py-1 text-left font-medium
                                           border-b border-bg-border">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(data?.history ?? []).map((h) => (
                  <tr key={h.state} className="border-b border-bg-border/40 last:border-0">
                    <td className="px-2 py-1 text-text-primary whitespace-nowrap">
                      {LIFECYCLE_ZH[h.state] ?? h.state}
                    </td>
                    {([['t1', 't1_win'], ['t3', 't3_win'], ['t5', 't5_win']] as const)
                      .map(([a, b]) => (
                        <Fragment key={a}>
                          <td className={cn('px-2 py-1 font-mono tabular-nums',
                            h[a] === null ? 'text-text-muted'
                              : h[a]! >= 0 ? 'text-up' : 'text-down')}>{pct(h[a])}</td>
                          <td className="px-2 py-1 font-mono tabular-nums text-text-muted">
                            {rate(h[b])}
                          </td>
                        </Fragment>
                      ))}
                    <td className="px-2 py-1 font-mono tabular-nums text-text-muted">
                      {h.t1_n}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-text-muted mt-2">
            胜率在 50% 附近 = 跟随机没区别。完整版（同日同池超额、可执行超额、
            按「股票×周期」整段重抽的 95% 区间）在
            <span className="text-text-secondary"> scripts/evaluate_lifecycle.py</span>
            ，那里多数状态的区间是<span className="text-warn">跨 0 的</span>。
          </p>
        </div>
      )}
    </div>
  )
}


function LifecycleSection() {
  return (
    <div className="space-y-2">
      <div className="flex items-baseline gap-2 flex-wrap">
        <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wider">
          生命周期
        </h2>
        <span className="text-[11px] text-text-muted">
          Price Lifecycle v1.1 · 只描述价格结构，不代表交易许可
        </span>
        <Link to="/stocks" className="ml-auto text-[11px] text-accent">
          活跃股池 →
        </Link>
      </div>
      <LeaderCyclePanel />
    </div>
  )
}


export default function Dashboard() {
  const navigate = useNavigate()
  const [expandedSector, setExpandedSector] = useState<string | null>(null)
  const [expandedActive, setExpandedActive] = useState<string | null>(null)

  const { data: state, isLoading: loadingState } = useQuery({
    queryKey: ['market-state'],
    queryFn: fetchMarketState,
  })
  const { data: history, isLoading: loadingHistory } = useQuery({
    queryKey: ['market-history', 30],
    queryFn: () => fetchMarketHistory(30),
  })
  const { data: pe } = useQuery({
    queryKey: ['profit-effect'],
    queryFn: fetchProfitEffect,
  })
  // Reuse same cache key as SectorPool — no extra network request when user visited SectorPool first
  const { data: poolData } = useQuery({
    queryKey: ['strong-pool-all-for-sector'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
  } as any)

  const allStocks: Stock[] = (poolData as any)?.items ?? []

  // sector name → SectorGroup (for expanding a clicked row)
  const sectorGroupMap = useMemo(() => {
    const groups = buildSectorGroups(allStocks)
    return new Map(groups.map((g) => [g.name, g]))
  }, [allStocks])

  const { byCode: sectorTagsByCode, byName: sectorTagsByName } = useSectorTags()

  // ── 总龙头·板块分布（功能同板块赚钱效应，数据范围限定为总龙头）──────────────
  const dragonStocks = useDragonStocks()
  const leaderMaxes = useLeaderUniverseMaxes()
  const regStatus = useRegulatoryStatus()  // code → 监管状态（警示徽章）
  const severeTargets = useSevereTargets()  // code → 还需涨幅%触发严重异动
  // code → Stock（用于龙头股/弱转强卡片补「昨涨停/昨跌停」标签）
  const stockByCode = useMemo(() => {
    const m = new Map<string, Stock>()
    for (const s of [...allStocks, ...dragonStocks]) m.set(s.code, s)
    return m
  }, [allStocks, dragonStocks])
  // code → 龙头标签（仅总龙头有；用于龙头股/弱转强等卡片行内补标签）
  const dragonTagsByCode = useMemo(
    () => new Map(dragonStocks.map((s) => [s.code, getLeaderTags(s, leaderMaxes)])),
    [dragonStocks, leaderMaxes],
  )
  const [expandedDragonSector, setExpandedDragonSector] = useState<string | null>(null)
  const toggleDragonSector = (name: string) =>
    setExpandedDragonSector((prev) => (prev === name ? null : name))

  // 龙头按板块聚合为 SectorProfitEffect（板块涨幅沿用 pe 的板块指数行情）
  const dragonSectors = useMemo<SectorProfitEffect[]>(() => {
    const buckets = new Map<string, { up: number; down: number; n: number; sum: number }>()
    for (const st of dragonStocks) {
      const p = st.today_pct_change ?? 0
      for (const name of st.sectors ?? []) {
        let b = buckets.get(name)
        if (!b) { b = { up: 0, down: 0, n: 0, sum: 0 }; buckets.set(name, b) }
        b.n++; b.sum += p
        if (p > 0) b.up++; else if (p < 0) b.down++
      }
    }
    const out: SectorProfitEffect[] = []
    for (const [name, b] of buckets) {
      if (b.n < 2) continue  // 个股数<2 的板块无参考价值，不展示
      // 板块涨幅取板块指数真实今日涨幅（覆盖全板块），与赚钱效应(成员均涨幅)区分
      const td = sectorTagsByName.get(name)
      out.push({
        sector_code: td?.code ?? name,
        sector_name: name,
        stock_count: b.n,
        up_count: b.up,
        down_count: b.down,
        avg_pct: b.n ? b.sum / b.n : 0,
        sector_pct_today: td?.pct_today ?? 0,
      })
    }
    return out
  }, [dragonStocks, sectorTagsByName])

  // 展开龙头板块时，只展示该板块的龙头成员
  const dragonSectorGroupMap = useMemo(() => {
    const groups = buildSectorGroups(dragonStocks)
    return new Map(groups.map((g) => [g.name, g]))
  }, [dragonStocks])

  const toggleSector = (name: string) =>
    setExpandedSector((prev) => (prev === name ? null : name))

  const toggleActive = (name: string) =>
    setExpandedActive((prev) => (prev === name ? null : name))

  if (loadingState) return <LoadingSpinner />

  return (
    <div className="space-y-5 animate-fade-in">

      {/* 市场状态条已抽到全局 Layout（MarketStateBar），各页顶部统一展示 */}

      <LifecycleSection />

      {/* ════════════════════════════════════════════════════════════════════════
          赚钱效应模块
      ════════════════════════════════════════════════════════════════════════ */}
      {pe && (
        <div className="space-y-4">
          <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wider">
            赚钱效应
          </h2>

          {!pe.has_data ? (
            <div className="card p-6 text-center text-text-muted text-sm">暂无当日数据</div>
          ) : (
            <>
              {/* ── 整体赚钱效应 ── */}
              <div className="card p-4">
                <div className="flex flex-wrap items-start gap-6">
                  {/* 大数字 */}
                  <div>
                    <p className="label mb-1">当日均涨幅</p>
                    <span className={cn('text-3xl font-mono font-bold', pctColor(pe.overall_avg_pct))}>
                      {pctSign(pe.overall_avg_pct)}
                    </span>
                  </div>

                  {/* 涨跌分布 */}
                  <div className="flex-1 min-w-[200px]">
                    <div className="flex justify-between text-xs mb-1.5">
                      <span className="text-up">↑ {pe.overall_up_count} 涨</span>
                      <span className="text-text-muted">{pe.overall_flat_count} 平</span>
                      <span className="text-down">{pe.overall_down_count} 跌 ↓</span>
                    </div>
                    <UpDownBar
                      up={pe.overall_up_count}
                      flat={pe.overall_flat_count}
                      down={pe.overall_down_count}
                    />
                    <div className="flex gap-3 mt-2 text-xs text-text-muted">
                      <span>共 {pe.overall_up_count + pe.overall_flat_count + pe.overall_down_count} 只强势股</span>
                    </div>
                  </div>

                  {/* 涨跌停数 */}
                  <div className="flex gap-4 items-center">
                    <div className="text-center">
                      <p className="label mb-0.5">涨停</p>
                      <span className="text-xl font-mono font-bold text-up">{pe.overall_limit_up_count}</span>
                    </div>
                    <div className="w-px h-8 bg-border" />
                    <div className="text-center">
                      <p className="label mb-0.5">跌停</p>
                      <span className="text-xl font-mono font-bold text-down">{pe.overall_limit_down_count}</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* ── 情绪曲线 ── */}
              <div className="card p-3 h-52">
                {loadingHistory ? (
                  <LoadingSpinner />
                ) : history?.length ? (
                  <EmotionChart data={history} />
                ) : (
                  <div className="text-center text-text-muted text-sm py-10">暂无历史数据</div>
                )}
              </div>

              {/* ── 分组赚钱效应：**按生命周期分组** ──
                  2026-09-07 换掉旧的四张卡（昨日涨停龙头/震荡/走弱/破位）。
                  那四组按 Stock.phase 分，而 phase 只是"收盘价在哪条均线下面"
                  的单日快照——一只刚断板正在修复的票和一只连跌十天的老龙都可能
                  被叫「震荡龙头」，分出来的组回答不了任何问题。 */}
              <LifecycleEffect />

              {/* ── 板块赚钱 / 亏钱效应（并列） ── */}
              {pe.sectors.length > 0 && (() => {
                const profitSectors = pe.sectors.filter((s: SectorProfitEffect) => s.avg_pct >= 0)
                const lossSectors = pe.sectors.filter((s: SectorProfitEffect) => s.avg_pct < 0)
                const expandedGroup = expandedSector ? sectorGroupMap.get(expandedSector) : null

                return (
                  <>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                      <SectorEffectCard
                        title="板块赚钱效应"
                        sectors={profitSectors}
                        isLoss={false}
                        expandedName={expandedSector}
                        onToggleRow={toggleSector}
                        tagFor={(code) => sectorTagsByCode.get(code)}
                        emptyText="暂无上涨板块"
                      />
                      <SectorEffectCard
                        title="板块亏钱效应"
                        sectors={lossSectors}
                        isLoss={true}
                        expandedName={expandedSector}
                        onToggleRow={toggleSector}
                        tagFor={(code) => sectorTagsByCode.get(code)}
                        emptyText="暂无下跌板块"
                      />
                    </div>

                    {/* ── 展开的板块详情 ── */}
                    {expandedGroup && (
                      <SectorSection
                        group={expandedGroup}
                        collapsed={false}
                        onToggle={() => setExpandedSector(null)}
                        onClickStock={(code) => navigate(`/stocks/${code}`)}
                      />
                    )}
                  </>
                )
              })()}
            </>
          )}
        </div>
      )}

      {/* ── Active Sectors ── */}
      <Card title="活跃板块" className="overflow-auto max-h-64">
        {state?.active_sectors.length ? (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
            {state.active_sectors.map((s) => {
              const active = expandedActive === s.sector_name
              const hasMembers = sectorGroupMap.has(s.sector_name)
              return (
                <div
                  key={s.sector_code}
                  onClick={() => hasMembers && toggleActive(s.sector_name)}
                  className={cn(
                    'flex items-center justify-between gap-2 p-2 rounded bg-bg-elevated transition-colors',
                    hasMembers && 'cursor-pointer hover:bg-bg-border',
                    active && 'ring-1 ring-accent/50',
                  )}
                >
                  <div>
                    <div className="text-sm font-medium text-text-primary">{s.sector_name}</div>
                    <div className="text-xs text-text-muted mt-0.5">
                      强股 {s.strong_stock_count} · 连板高度 {s.board_height}
                    </div>
                  </div>
                  <div className="text-right">
                    <PhaseTag phase={s.phase} />
                    <div className="text-xs font-mono text-accent mt-0.5">{s.emotion_score.toFixed(0)}</div>
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <EmptyHint
            icon={Activity}
            title="当前无扩张期板块"
            hint={`市场处于「${MARKET_PHASE_LABELS[state?.market_phase ?? ''] ?? '弱势'}」，最强板块尚在启动/分歧阶段，未形成连板扩张梯队。`}
          />
        )}
      </Card>

      {/* 活跃板块展开：复用 SectorSection（与赚钱效应点击展开一致） */}
      {expandedActive && sectorGroupMap.get(expandedActive) && (
        <SectorSection
          group={sectorGroupMap.get(expandedActive)!}
          collapsed={false}
          onToggle={() => setExpandedActive(null)}
          onClickStock={(code) => navigate(`/stocks/${code}`)}
        />
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* ── Dragon Leaders ── */}
        <Card title="龙头股" action={<TrendingUp className="w-3.5 h-3.5 text-dragon" />}>
          {state?.dragon_leaders.length ? (
            <div className="space-y-2">
              {state.dragon_leaders.slice(0, 5).map((l) => (
                <div key={l.stock_code} className="flex items-center justify-between gap-2 p-2 rounded bg-bg-elevated">
                  <div className="flex items-center gap-2 min-w-0">
                    <Badge variant="dragon">{LEADER_TYPE_LABELS[l.leader_type] ?? l.leader_type}</Badge>
                    <div>
                      <div className="flex items-center gap-1">
                        <span className="font-medium text-sm text-text-primary">{l.stock_name}</span>
                        <span className="text-xs text-text-muted">{l.stock_code}</span>
                        {regStatus.get(l.stock_code) && <RegulatoryTag status={regStatus.get(l.stock_code)!} />}
                        <SevereTargetTag target={severeTargets.get(l.stock_code)?.target_rate} approach={severeTargets.get(l.stock_code)?.approach} room={stockByCode.get(l.stock_code)?.severe_up_room ?? null} />
                        {stockByCode.get(l.stock_code)?.yesterday_is_limit_up && <YesterdayLimitTag dir="up" />}
                        {stockByCode.get(l.stock_code)?.yesterday_is_limit_down && <YesterdayLimitTag dir="down" />}
                      </div>
                      {(dragonTagsByCode.get(l.stock_code)?.length ?? 0) > 0 && (
                        <div className="flex flex-wrap gap-0.5 mt-0.5">
                          {dragonTagsByCode.get(l.stock_code)!.map((t) => <LeaderTag key={t} label={t} />)}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 text-xs">
                    <span className="text-text-muted">{l.sector_name}</span>
                    <span className="font-mono text-dragon">龙:{l.leader_score.toFixed(0)}</span>
                    <Progress value={l.risk_score} className="w-12" />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center text-text-muted text-sm py-6">暂无龙头数据</div>
          )}
        </Card>

        {/* ── Weak-to-Strong ── */}
        <Card title="弱转强候选" action={<Zap className="w-3.5 h-3.5 text-accent" />}>
          {state?.weak_to_strong_candidates.length ? (
            <div className="space-y-2">
              {state.weak_to_strong_candidates.slice(0, 5).map((c) => (
                <div key={c.stock_code} className="p-2 rounded bg-bg-elevated">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-sm text-text-primary">{c.stock_name}</span>
                      <span className="text-xs text-text-muted">{c.stock_code}</span>
                      {regStatus.get(c.stock_code) && <RegulatoryTag status={regStatus.get(c.stock_code)!} />}
                      <SevereTargetTag target={severeTargets.get(c.stock_code)?.target_rate} approach={severeTargets.get(c.stock_code)?.approach} room={stockByCode.get(c.stock_code)?.severe_up_room ?? null} />
                      {stockByCode.get(c.stock_code)?.yesterday_is_limit_up && <YesterdayLimitTag dir="up" />}
                      {stockByCode.get(c.stock_code)?.yesterday_is_limit_down && <YesterdayLimitTag dir="down" />}
                      {dragonTagsByCode.get(c.stock_code)?.map((t) => <LeaderTag key={t} label={t} />)}
                    </div>
                    <div className="flex items-center gap-1.5">
                      <RiskBadge level={c.risk_level as RiskLevel} />
                      <span className={cn('text-xs font-medium', ACTION_COLORS[c.suggested_action])}>
                        {ACTION_LABELS[c.suggested_action]}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="accent">{SIGNAL_TYPE_LABELS[c.signal_type] ?? c.signal_type}</Badge>
                    <div className="flex items-center gap-1 text-xs text-text-muted">
                      置信 <span className="font-mono text-accent ml-0.5">{c.confidence_score.toFixed(0)}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyHint
              icon={Zap}
              title="今日暂无弱转强候选"
              hint="强势池个股中未出现破位/走弱后涨停、炸板复板等修复形态，属当前弱势行情的正常表现。"
            />
          )}
        </Card>
      </div>

      {/* ════════════════════════════════════════════════════════════════════════
          总龙头·板块分布（功能/交互同板块赚钱效应，数据范围限定总龙头）
      ════════════════════════════════════════════════════════════════════════ */}
      <div className="space-y-4">
        <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wider">
          总龙头板块分布
        </h2>
        {dragonSectors.length === 0 ? (
          <EmptyHint
            icon={TrendingUp}
            title="当前无总龙头"
            hint="合并全集（强势池+涨跌停池）中暂无达到全市场龙头标签（10/20/60龙·高板龙·连板龙）的个股。"
          />
        ) : (() => {
          const profit = dragonSectors.filter((s) => s.avg_pct >= 0)
          const loss = dragonSectors.filter((s) => s.avg_pct < 0)
          const expandedGroup = expandedDragonSector ? dragonSectorGroupMap.get(expandedDragonSector) : null
          return (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <SectorEffectCard
                  title="总龙头·板块赚钱效应"
                  sectors={profit}
                  isLoss={false}
                  expandedName={expandedDragonSector}
                  onToggleRow={toggleDragonSector}
                  tagFor={(code) => sectorTagsByCode.get(code)}
                  emptyText="暂无上涨板块"
                />
                <SectorEffectCard
                  title="总龙头·板块亏钱效应"
                  sectors={loss}
                  isLoss={true}
                  expandedName={expandedDragonSector}
                  onToggleRow={toggleDragonSector}
                  tagFor={(code) => sectorTagsByCode.get(code)}
                  emptyText="暂无下跌板块"
                />
              </div>

              {/* 展开：仅展示该板块的龙头成员 */}
              {expandedGroup && (
                <SectorSection
                  group={expandedGroup}
                  collapsed={false}
                  onToggle={() => setExpandedDragonSector(null)}
                  onClickStock={(code) => navigate(`/stocks/${code}`)}
                />
              )}
            </>
          )
        })()}
      </div>

    </div>
  )
}
