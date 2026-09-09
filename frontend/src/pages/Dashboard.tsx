import { Fragment, useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { fetchMarketState, fetchMarketHistory, fetchProfitEffect } from '@/api/marketState'
import {
  fetchStrongPool, fetchLifecycleEffect, fetchLifecycleEvidence, fetchLeaderCycle,
} from '@/api/stocks'
import type { EvidenceEvent, EvidenceCell } from '@/api/stocks'
import { LIFECYCLE_ZH, STATE_ORDER } from '@/lib/lifecycle'
import LeaderCyclePanel, { LifecycleScopeNote } from '@/components/stockPool/LeaderCyclePanel'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { LifecycleEffectChart } from '@/components/charts/LifecycleEffectChart'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { RiskBadge } from '@/components/common/RiskBadge'
import { SectorSection, buildSectorGroups } from '@/components/common/SectorSection'
import { SortTh, compareWithNullsLast, type SortState } from '@/components/common/SortTh'
import {
  MARKET_PHASE_LABELS, EMOTION_CYCLE_LABELS, ACTION_LABELS,
  ACTION_COLORS, LEADER_TYPE_LABELS, SIGNAL_TYPE_LABELS,
  PHASE_COLORS, pct, PHASE_NAME_TO_NUM,
} from '@/utils/format'
import { cn } from '@/utils/cn'
import { TrendingUp, Zap, ChevronDown, ChevronUp, Activity } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type {
  RiskLevel, ProfitEffectGroup, SectorProfitEffect, Stock, MarketHistoryPoint,
} from '@/types'
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
 * 上面一格是**当日**：昨天处于某状态的票，今天的去极值均涨幅、红盘率。
 * 按涨幅从高到低排，同分看红盘率；算不出的（不足 3 只）沉底。
 * 下面一格是**历史**：过去 60 天处于该状态之后 T+1/T+3/T+5 普遍怎么走。
 *
 * 历史那部分刻意标成「线索」而不是结论——它没做同日同池对照，也没有置信区间。
 * `scripts/evaluate_lifecycle.py` 里有完整的版本（同日同池超额、可执行超额、
 * 按周期整段重抽的 95% 区间），而那里多数状态的区间是**跨 0 的**。
 * 在界面上把这几个数字摆成结论，就等于又造了一个看起来精确的黑箱。
 */
type HistKey = 't1' | 't1_win' | 't3' | 't3_win' | 't5' | 't5_win' | 't1_n' | 'state'

const HISTORY_COLS: { key: HistKey; label: string }[] = [
  { key: 'state', label: '状态' },
  // **不叫「胜率」。** 它是绝对收益为正的比例，跟事件表里「跑赢同日同池」
  // 是两个东西，摆在一起用同一个词迟早被读混
  { key: 't1', label: 'T+1' }, { key: 't1_win', label: '上涨占比' },
  { key: 't3', label: 'T+3' }, { key: 't3_win', label: '上涨占比' },
  { key: 't5', label: 'T+5' }, { key: 't5_win', label: '上涨占比' },
  { key: 't1_n', label: '样本' },
]

/**
 * 同分时的次序键。做短线看的是 T+1：**平均涨幅一样,就先看谁赢面大**。
 * 点胜率时反过来用涨幅打平——两个数本来就是一件事的两面。
 */
const HIST_TIEBREAK: Partial<Record<HistKey, HistKey>> = {
  t1: 't1_win', t1_win: 't1',
  t3: 't3_win', t3_win: 't3',
  t5: 't5_win', t5_win: 't5',
}

/**
 * 逐日赚钱效应曲线。跟 LifecycleEffect 共用 ['lifecycle-effect'] 这一次请求——
 * series 和 cohorts 本来就是同一个接口算出来的。
 */
function LifecycleSeries({ history }: { history: MarketHistoryPoint[] }) {
  const q = useQuery({
    queryKey: ['lifecycle-effect'], queryFn: fetchLifecycleEffect,
    staleTime: 10 * 60 * 1000,
  })
  if (q.isPending) return <LoadingSpinner />
  if (q.error) {
    return <div className="text-center text-warn text-xs py-10">数据获取失败</div>
  }
  return <LifecycleEffectChart series={q.data?.series ?? []} history={history}
                               todayEstimate={q.data?.today_estimate} />
}


function LifecycleEffect() {
  const { data } = useQuery({
    queryKey: ['lifecycle-effect'], queryFn: fetchLifecycleEffect,
    staleTime: 10 * 60 * 1000,
  })
  // 默认按 T+1 效应从高到低,平手再看 T+1 胜率——短线先问明天怎么走
  const [sort, setSort] = useState<SortState<HistKey>>({ key: 't1', dir: 'desc' })
  const onSort = (k: HistKey) =>
    setSort((p) => (p.key === k ? { key: k, dir: p.dir === 'desc' ? 'asc' : 'desc' }
                                : { key: k, dir: 'desc' }))

  const history = useMemo(() => {
    const rows = [...(data?.history ?? [])]
    const key = sort.key
    if (!key) return rows
    const tie = HIST_TIEBREAK[key]
    // 「状态」按生命周期的先后排，不按枚举名的字母序——CROSS_FAILED 排在
    // CROSS_SUCCESS 前面对看的人没有任何意义
    const val = (r: typeof rows[number], k: HistKey) =>
      k === 'state' ? STATE_ORDER.indexOf(r.state) : r[k]
    return rows.sort((a, b) => {
      const c = compareWithNullsLast(val(a, key), val(b, key), sort.dir)
      if (c !== 0 || !tie) return c
      return compareWithNullsLast(val(a, tie), val(b, tie), sort.dir)
    })
  }, [data?.history, sort])

  const cohorts = (data?.cohorts ?? []).filter((c) => c.count > 0)
  // **不在这里 return null。** 转移时点证据是独立的离线产物，跟今天有没有
  // 队列、有没有历史前瞻无关；一起吞掉会让「评估还没跑」变成整块空白

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
                c.trimmed_avg_pct_change === null ? 'text-text-muted'
                  : c.trimmed_avg_pct_change >= 0 ? 'text-up' : 'text-down')}>
                {pct(c.trimmed_avg_pct_change)}
              </span>
              <span className="text-xs text-text-muted">{c.count}只</span>
            </div>
            <div className="text-[11px] text-text-muted mt-1">
              {/* 名字必须说清是哪个统计量——上面那张图是**均值**，两个数会差
                  得很远，同屏摆着只差一个字就会读混 */}
              <span title="去掉一个最高、一个最低之后的均值">今日去极值均涨幅</span>
              {' · 红盘率 '}{rate(c.red_ratio)}
              {c.trimmed_avg_pct_change === null && '（不足 3 只，去掉两端就没剩下了）'}
            </div>
          </div>
        ))}
      </div>

      <LifecycleEvidence />

      {history.length > 0 && (
        // **默认折叠。** 无对照、无区间的那张表是背景材料，不该跟做了对照和
        // 区间的事件表抢同一屏；要看时点开
        <details className="card p-3">
          <summary className="flex items-baseline gap-2 flex-wrap cursor-pointer
                              select-none list-none">
            <span className="text-xs text-text-secondary font-medium">
              按状态截面（近 60 个交易日）
            </span>
            <span className="text-[11px] text-warn">
              无对照、无区间 —— 只是线索，不是结论
            </span>
          </summary>
          {/* 状态会**混路径**：「修复中」既可能来自刚断板（第一次转强），也可能
              来自修复失败（失败后再修复）——上面那张事件表里这两条方向相反。
              混回一行等于把拆开的信息又稀释掉，所以这张表在下面 */}
          <p className="text-[11px] text-text-muted mt-1">
            这里的「上涨占比」是绝对收益为正的比例，<span className="text-text-secondary">
            不是跑赢基准</span>；市场当天的涨跌全混在数字里。而且同一个状态会混路径
            ——「修复中」既可能来自刚断板，也可能来自修复失败，上面那张表里这两条
            方向相反。
          </p>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-xs" style={{ minWidth: 560 }}>
              <thead>
                <tr className="text-[10px]">
                  {HISTORY_COLS.map(({ key, label }) => (
                    <SortTh key={key} col={key} label={label} align="left"
                            sort={sort} onSort={onSort}
                            className="px-2 py-1 border-b border-bg-border" />
                  ))}
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
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
          {/* 后端的 notes 要渲染出来：口径的代价（比如保留了多少行未标
              is_settled 的快照）跟数字一起来，不能只留在 JSON 里 */}
          {(data?.notes ?? []).length > 0 && (
            <ul className="text-[11px] text-text-muted mt-2 space-y-0.5">
              {data!.notes.map((n, i) => (
                <li key={i} className="flex gap-1.5">
                  <span className="text-text-muted/50 shrink-0">·</span><span>{n}</span>
                </li>
              ))}
            </ul>
          )}
          <p className="text-[11px] text-text-muted mt-2">
            上涨占比在 50% 附近 = 跟随机没区别，但它本来也不是超额——
            做了对照和区间的版本是上面那张事件表。
          </p>
        </details>
      )}
    </div>
  )
}



type EvKey = 'event' | 'x1' | 'x3' | 'n'

type Verdict = 'pos' | 'neg' | 'unconfirmed' | 'insufficient'

/**
 * **判定完全由 95% 区间决定，不看中位数的正负。**
 *
 * 这不是打分,是把区间翻译成一句话:区间整段在 0 以上/以下才算方向确认,跨 0
 * 就是没确认——中位数 +3.1% 看起来再好也一样。反过来说,页面也不能因为中位
 * 数是正的就把它涂成红色:那等于用一个未确认的数字去锚定人的判断。
 */
function verdictOf(c?: EvidenceCell | null): Verdict {
  if (!c || !c.ci) return 'insufficient'
  if (c.ci[0] > 0) return 'pos'
  if (c.ci[1] < 0) return 'neg'
  return 'unconfirmed'
}

const VERDICT_ZH: Record<Verdict, string> = {
  pos: '正向确认', neg: '负向确认', unconfirmed: '未确认', insufficient: '样本不足',
}
// 只有确认了方向才给颜色。未确认一律中性灰——**颜色是结论,不是装饰**
const VERDICT_TONE: Record<Verdict, string> = {
  pos: 'text-up', neg: 'text-down',
  unconfirmed: 'text-text-secondary', insufficient: 'text-text-muted/60',
}

const evLabel = (e: EvidenceEvent) =>
  e.from && e.to
    ? `${LIFECYCLE_ZH[e.from] ?? e.from} → ${LIFECYCLE_ZH[e.to] ?? e.to}`
    : e.event

const signed = (v: number | null | undefined, d = 1) =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`

/**
 * 一个视界的三行：超额 / 跑赢基准·n / 区间·判定。
 *
 * `plain` 用于全池基线那一行——它按定义就该是 0，给它标「未确认」是噪声。
 */
const TD = 'px-2 py-1.5 whitespace-nowrap font-mono tabular-nums'

/**
 * 一个视界摊成五个格子：超额 / 跑赢 / n / 95% 区间 / 判定。
 *
 * 原来是一个格子里叠三行，四列把宽度撑满、中间大片留白，而每格自己却在换行。
 * 摊开之后每格只有一个值，扫读时视线是横着走的，不用在格子内部再解析一次。
 *
 * `plain` 用于全池基线那一行——它按定义就该是 0，给它标「未确认」是噪声。
 */
function horizonCells(c: EvidenceCell | null | undefined, plain = false) {
  const v = verdictOf(c)
  return (
    <>
      <td className={cn(TD, plain ? 'text-text-muted' : VERDICT_TONE[v])}>
        {signed(c?.median)}
      </td>
      <td className={cn(TD, 'text-text-muted')}>
        {c?.pos_rate != null ? `${(c.pos_rate * 100).toFixed(0)}%` : '—'}
      </td>
      <td className={cn(TD, 'text-text-muted')}>{c ? c.n : '—'}</td>
      <td className={cn(TD, 'text-text-muted/70 text-[11px]')}>
        {c?.ci ? `[${signed(c.ci[0])}, ${signed(c.ci[1])}]` : '—'}
      </td>
      <td className={cn(TD, 'text-[11px]', plain ? 'text-text-muted/40' : VERDICT_TONE[v])}>
        {plain ? '—' : VERDICT_ZH[v]}
      </td>
    </>
  )
}

/** 折叠区里的紧凑表格：比主表一行矮一截，字号小一号 */
const FTH = 'px-2 py-0.5 border-b border-bg-border'
const FTD = 'px-2 py-0.5 whitespace-nowrap font-mono tabular-nums text-[11px]'

/**
 * 折叠区里那两张表共用的排序。**空值永远沉底**——「这一档没有样本」不是「最小」。
 *
 * 单独抽出来是因为它们跟主表用的是同一套交互（点列头、同列再点切升降），
 * 而两张折叠表各写一遍必然慢慢分叉。
 */
function useFoldSort<K extends string>(initial: K) {
  const [sort, setSort] = useState<SortState<K>>({ key: initial, dir: 'desc' })
  const onSort = (k: K) =>
    setSort((p) => (p.key === k ? { key: k, dir: p.dir === 'desc' ? 'asc' : 'desc' }
                                : { key: k, dir: 'desc' }))
  const sortBy = <T,>(rows: T[], value: (r: T, k: K) => number | string | null) => {
    const key = sort.key
    if (!key) return rows
    return [...rows].sort((a, b) =>
      compareWithNullsLast(value(a, key), value(b, key), sort.dir))
  }
  return { sort, onSort, sortBy }
}

const HORIZONS = [1, 3, 5, 10] as const
type BalKey = 'event' | 'n' | '1' | '3' | '5' | '10'
type ExecKey = 'event' | '1' | '3'

/** 持有期比较那张表的排序取值。**没有那一格就是 null，沉底** */
const balValue = (e: EvidenceEvent, k: BalKey): number | string | null => {
  const b = e.excess_balanced
  if (k === 'event') return evLabel(e)
  if (k === 'n') return HORIZONS.map((h) => b?.[String(h)]?.n).find((x) => x != null) ?? null
  return b?.[k]?.median ?? null
}

/** 次日开盘可执行那张表的排序取值 */
const execValue = (e: EvidenceEvent, k: ExecKey): number | string | null =>
  k === 'event' ? evLabel(e) : (e.exec_excess?.[k]?.median ?? null)

function Fold({ title, note, children }: {
  title: string; note?: string; children: React.ReactNode
}) {
  return (
    <details className="mt-2 border-t border-bg-border/60 pt-2">
      <summary className="text-[11px] text-text-secondary cursor-pointer select-none
                          hover:text-text-primary">
        {title}
        {note && <span className="text-text-muted ml-1.5">{note}</span>}
      </summary>
      <div className="mt-1.5">{children}</div>
    </details>
  )
}

/**
 * 生命周期**事件**的历史表现。
 *
 * 跟下面那张按状态截面统计的表不是同一件事:
 *   按状态   = 处于某状态的期间怎么走（**无对照**,市场行情混在里面）
 *   按事件   = 转入某状态那天之后怎么走（逐事件对同日同池,行情已消掉,带区间）
 * 而且状态会**混路径**:「修复中」既可能来自刚断板,也可能来自修复失败,这两条
 * 历史表现方向相反,混回一行等于把刚拆开的信息又稀释掉。所以事件表在上面。
 *
 * 数据来自 scripts/evaluate_lifecycle.py --json 的离线产物。**这里只摆分布和
 * 区间,不打分**——判定那一列是把 95% 区间翻译成中文,不是评分。
 */
function LifecycleEvidence() {
  const { data } = useQuery({
    queryKey: ['lifecycle-evidence'], queryFn: fetchLifecycleEvidence,
    staleTime: 30 * 60 * 1000,
  })
  const [sort, setSort] = useState<SortState<EvKey>>({ key: 'x1', dir: 'desc' })
  // 两张折叠表各自的排序状态，跟主表互不影响
  const bal = useFoldSort<BalKey>('1')
  const exe = useFoldSort<ExecKey>('1')
  const onSort = (k: EvKey) =>
    setSort((p) => (p.key === k ? { key: k, dir: p.dir === 'desc' ? 'asc' : 'desc' }
                                : { key: k, dir: 'desc' }))

  const rows = useMemo(() => {
    const es = [...(data?.events ?? [])]
    const key = sort.key
    if (!key) return es
    const of = (e: EvidenceEvent) => ({
      event: evLabel(e), n: e.n_events,
      x1: e.excess?.['1']?.median ?? null,
      x3: e.excess?.['3']?.median ?? null,
    })[key]
    return es.sort((a, b) => compareWithNullsLast(of(a), of(b), sort.dir))
  }, [data?.events, sort])

  // ── 顶部总览：**机械地从区间推出来,没有人工挑选** ────────────────────────
  // 「哪个事件最值得看」这种话不能由页面来说——那就是打分了。这里只回答两个
  // 数得出来的问题:有没有任何事件方向确认为正?哪些确认为负?
  const confirmed = useMemo(() => {
    const pos: string[] = [], neg: string[] = []
    let best: { label: string; v: number } | null = null
    for (const e of data?.events ?? []) {
      const hs = (['1', '3'] as const).filter((h) => verdictOf(e.excess?.[h]) === 'pos')
      const ns = (['1', '3'] as const).filter((h) => verdictOf(e.excess?.[h]) === 'neg')
      if (hs.length) pos.push(`${evLabel(e)}（T+${hs.join('/T+')}）`)
      if (ns.length) neg.push(`${evLabel(e)}（T+${ns.join('/T+')}）`)
      // 没有正向确认时,说清「最高的那个是谁、多少」——**从数据里取,不写死**。
      // 写死的例子过两天就跟表里对不上,而且那是个会自己腐烂的结论
      const m = e.excess?.['1']?.median
      if (m != null && (best === null || m > best.v)) best = { label: evLabel(e), v: m }
    }
    return { pos, neg, best }
  }, [data?.events])

  if (!data) return null

  // **「还没跑过」不能显示成空表。** 空表看起来像「跑过了,但一条证据都没有」
  if (!data.available) {
    return (
      <div className="card p-3">
        <p className="text-xs text-text-secondary font-medium">生命周期事件历史表现</p>
        <p className="text-[11px] text-text-muted mt-1.5">
          {data.reason || '离线评估产物不可用'}
        </p>
        <code className="block mt-1.5 text-[11px] text-text-secondary
                         bg-bg-elevated rounded px-2 py-1 overflow-x-auto">
          cd backend && python scripts/evaluate_lifecycle.py --json
        </code>
      </div>
    )
  }

  const th = 'px-2 py-1 border-b border-bg-border'
  return (
    // **默认折叠。** 这是研究性证据，不是每天开盘要扫的东西；摊开占大半屏，
    // 把下面「按状态截面」和板块效应挤到折叠线以下。要看时点开，跟里面那三个
    // 折叠区是同一个交互
    <details className="card p-3">
      <summary className="flex items-baseline gap-2 flex-wrap cursor-pointer
                          select-none list-none">
        <span className="text-xs text-text-secondary font-medium">
          生命周期事件历史表现
        </span>
        <span className="text-[11px] text-text-muted">
          同日同池超额 · {data.formula_version}
          {data.as_of && ` · 数据截至 ${data.as_of}`}
          {data.generated_at &&
            ` · 评估于 ${data.generated_at.slice(0, 16).replace('T', ' ')}`}
        </span>
        {/* **口径过期要在折叠状态下也看得见**——它不是细节，是"这些数字还算不算数" */}
        {data.stale_formula && (
          <span className="text-[11px] text-warn">
            产物口径 ≠ 当前 {data.current_formula_version}，旧证据不对应现在的规则
          </span>
        )}
      </summary>
      <p className="text-[11px] text-warn/90 mt-1">
        样本来自「今天仍在强势池里的幸存者」，当时进不了池的票根本没有行——
        绝对水平偏高，只能做组间比较。
      </p>

      {/* ── 总览：直接由区间数出来，没有人工挑选，也没有评分 ─────────────── */}
      <div className="mt-2 rounded bg-bg-elevated/60 px-2.5 py-2 space-y-1">
        <div className="text-[11px]">
          <span className="text-text-muted">正向确认：</span>
          {confirmed.pos.length
            ? <span className="text-up">{confirmed.pos.join('、')}</span>
            : <span className="text-text-secondary">
                暂无 —— 没有任何事件的 95% 区间整段落在 0 以上。
                {confirmed.best && ` T+1 中位超额最高的是「${confirmed.best.label}」`
                  + `（${signed(confirmed.best.v)}），但它的区间也跨 0。`}
              </span>}
        </div>
        <div className="text-[11px]">
          <span className="text-text-muted">负向确认：</span>
          {confirmed.neg.length
            ? <span className="text-down">{confirmed.neg.join('、')}</span>
            : <span className="text-text-secondary">暂无</span>}
        </div>
      </div>

      <div className="mt-2 overflow-x-auto">
        <table className="text-xs" style={{ minWidth: 880 }}>
          {/* 双行表头：上面一行分 T+1 / T+3 两组，下面一行是组内的五个字段。
              排序只挂在两个「超额」上——其余四列是同一件事的不同侧面，
              各自排一遍没有意义 */}
          <thead>
            <tr className="text-[10px]">
              <th rowSpan={2} className={cn(th, 'text-left text-text-secondary/55')}>
                <SortTh col={'event' as EvKey} label="事件" align="left"
                        sort={sort} onSort={onSort} className="!px-0 !py-0 !border-0" />
              </th>
              <th colSpan={5}
                  className="px-2 pt-1 pb-0.5 text-left text-[10px] font-medium
                             text-text-secondary border-b border-bg-border/40">T+1</th>
              <th colSpan={5}
                  className="px-2 pt-1 pb-0.5 text-left text-[10px] font-medium
                             text-text-secondary border-b border-bg-border/40
                             border-l border-l-bg-border">T+3</th>
              <th rowSpan={2} className={cn(th, 'text-left text-text-secondary/55')}>
                <SortTh col={'n' as EvKey} label="事件数" align="left"
                        sort={sort} onSort={onSort} className="!px-0 !py-0 !border-0" />
              </th>
            </tr>
            <tr className="text-[10px]">
              <SortTh col={'x1' as EvKey} label="超额" align="left"
                      sort={sort} onSort={onSort} className={th} />
              {['跑赢', 'n', '95% 区间', '判定'].map((h) => (
                <th key={`1${h}`} className={cn(th, 'text-left font-medium',
                  'text-text-secondary/55')}>{h}</th>
              ))}
              <SortTh col={'x3' as EvKey} label="超额" align="left"
                      sort={sort} onSort={onSort} className={cn(th, 'border-l border-l-bg-border')} />
              {['跑赢', 'n', '95% 区间', '判定'].map((h) => (
                <th key={`3${h}`} className={cn(th, 'text-left font-medium',
                  'text-text-secondary/55')}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((e) => (
              <tr key={e.event} className="border-b border-bg-border/40 last:border-0">
                <td className="px-2 py-1.5 text-text-primary whitespace-nowrap">
                  {evLabel(e)}
                </td>
                {horizonCells(e.excess?.['1'])}
                {horizonCells(e.excess?.['3'])}
                <td className={cn(TD, 'text-text-muted')}>{e.n_events}</td>
              </tr>
            ))}
            {/* 基线留着：它是「超额确实以 0 为中心」的自查，不是一个待比较的对象 */}
            {data.baseline && (
              <tr className="border-t border-bg-border text-text-muted/70">
                <td className="px-2 py-1.5 whitespace-nowrap"
                    title="全部股票日。超额口径下它按定义就该接近 0——这一行是自查">
                  全池基线
                </td>
                {horizonCells(data.baseline.excess?.['1'], true)}
                {horizonCells(data.baseline.excess?.['3'], true)}
                <td className={cn(TD, 'text-text-muted')}>{data.baseline.n_events}</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* ── T+5/T+10 不进主表 ─────────────────────────────────────────────
          主表里 T+1 含近期事件、T+10 只含更早的,并排放会被读成「持有越久越
          差」。只有同一批（走得完 T+10 的）样本才谈得上时间衰减 */}
      <Fold title="持有期比较（T+1 → T+10）"
            note="只用能走完 T+10 的同一批事件，否则不是时间衰减">
        <div className="overflow-x-auto">
          <table className="w-full text-xs" style={{ minWidth: 480 }}>
            <thead>
              <tr className="text-[10px]">
                <SortTh col={'event' as BalKey} label="事件" align="left"
                        sort={bal.sort} onSort={bal.onSort} className={FTH} />
                {HORIZONS.map((h) => (
                  <SortTh key={h} col={String(h) as BalKey} label={`T+${h}`} align="left"
                          sort={bal.sort} onSort={bal.onSort} className={FTH} />
                ))}
                <SortTh col={'n' as BalKey} label="n" align="left"
                        sort={bal.sort} onSort={bal.onSort} className={FTH} />
              </tr>
            </thead>
            <tbody>
              {bal.sortBy((data.events ?? []).filter((e) => e.excess_balanced),
                          balValue).map((e) => {
                const b = e.excess_balanced!
                const n = HORIZONS.map((h) => b[String(h)]?.n).find((x) => x != null)
                return (
                  <tr key={e.event} className="border-b border-bg-border/40 last:border-0">
                    <td className="px-2 py-0.5 text-text-primary whitespace-nowrap text-[11px]">
                      {evLabel(e)}
                    </td>
                    {HORIZONS.map((h) => (
                      <td key={h} className={cn(FTD, 'text-text-secondary')}>
                        {signed(b[String(h)]?.median)}
                      </td>
                    ))}
                    <td className={cn(FTD, 'text-text-muted')}>{n ?? '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {!(data.events ?? []).some((e) => e.excess_balanced) && (
            <p className="text-[11px] text-text-muted">
              还没有事件能走完 T+10 的完整窗口。
            </p>
          )}
          <p className="text-[11px] text-text-muted mt-1.5">
            这里刻意不给区间和判定：换一批样本就是另一组数，跟主表的 T+1/T+3
            不可直接对读。
          </p>
        </div>
      </Fold>

      {/* ── 可执行口径:方向最贴近实操,但 n 只有个位数 ──────────────────── */}
      <Fold title="次日开盘可执行样本"
            note="样本积累中，有效 n 多为个位数，不用于方向判断">
        <div className="overflow-x-auto">
          <table className="w-full text-xs" style={{ minWidth: 420 }}>
            <thead>
              <tr className="text-[10px]">
                <SortTh col={'event' as ExecKey} label="事件" align="left"
                        sort={exe.sort} onSort={exe.onSort} className={FTH} />
                <SortTh col={'1' as ExecKey} label="T+1 可执行超额" align="left"
                        sort={exe.sort} onSort={exe.onSort} className={FTH} />
                <SortTh col={'3' as ExecKey} label="T+3" align="left"
                        sort={exe.sort} onSort={exe.onSort} className={FTH} />
              </tr>
            </thead>
            <tbody>
              {exe.sortBy(data.events ?? [], execValue).map((e) => (
                <tr key={e.event} className="border-b border-bg-border/40 last:border-0">
                  <td className="px-2 py-0.5 text-text-primary whitespace-nowrap text-[11px]">
                    {evLabel(e)}
                  </td>
                  {(['1', '3'] as const).map((h) => (
                    <td key={h} className={cn(FTD, 'text-text-secondary')}>
                      {signed(e.exec_excess?.[h]?.median)}
                      <span className="text-text-muted/60 ml-0.5">
                        {e.exec_excess?.[h] ? `(n${e.exec_excess[h]!.n})` : ''}
                      </span>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <p className="text-[11px] text-text-muted mt-1.5">
            T+1 = 昨收识别信号、次日开盘买、当天收盘卖，是最贴近实操的一格。
            <span className="text-warn"> 但现在有效 n 只有 1~12，方向不能当真。</span>
            为什么必须看超额：基线自己在开盘口径下就不是 0。
          </p>
        </div>
      </Fold>

      <Fold title="口径与数据质量">
        <ul className="text-[10px] text-text-muted/90 space-y-0.5 leading-relaxed">
          {(data.caveats ?? []).map((c, i) => (
            <li key={i} className="flex gap-1.5">
              <span className="text-text-muted/50 shrink-0">·</span><span>{c}</span>
            </li>
          ))}
          <li className="flex gap-1.5">
            <span className="text-text-muted/50 shrink-0">·</span>
            <span>
              MFE/MAE：真实 OHLC 自 2026-08-27 起才有，缺就不算（不拿收盘顶替），
              当前有效样本不足，暂不展示。
            </span>
          </li>
          <li className="flex gap-1.5">
            <span className="text-text-muted/50 shrink-0">·</span>
            <span>
              每一格的 n 是它自己的有效样本：事件数不等于每格都有那么多——窗口
              没走完、同日不足 3 只同类没有对照，各扣各的。
              {data.skipped_incomplete
                ? ` 窗口未走完而排除 ${data.skipped_incomplete} 次测量。` : ''}
            </span>
          </li>
        </ul>
      </Fold>
    </details>
  )
}


/**
 * 生命周期整块。**默认折叠**——它下面是一张十九列的表，摊开占大半屏；
 * 这一页先回答「今天赚不赚钱」，具体是哪几只票要看时再点开。
 *
 * 折叠状态下**必须看起来像一张卡、且带口径说明**：之前只剩一行光秃秃的标题，
 * 既不像可点的东西，也看不出底下还有六十只票的表——发现不了的功能等于没有。
 * 所以这里跟「生命周期事件历史表现」用同一个形状：details.card + summary 里
 * 放标题和口径。
 *
 * 口径文案走 LeaderCyclePanel 导出的那一份，**不在这里抄第二遍**；scope_note
 * 复用 ['leader-cycle'] 这个 key，跟面板共享同一次请求，不多打接口。
 */
function LifecycleSection() {
  const { data } = useQuery({
    queryKey: ['leader-cycle'], queryFn: () => fetchLeaderCycle(),
    staleTime: 5 * 60 * 1000,
  })
  return (
    <details className="card p-3 space-y-2">
      <summary className="cursor-pointer select-none list-none space-y-2">
        <div className="flex items-baseline gap-2 flex-wrap">
          <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wider">
            生命周期
          </h2>
          <span className="text-[11px] text-text-muted">
            Price Lifecycle v1.1 · 只描述价格结构，不代表交易许可
          </span>
          {/* stopPropagation：这是跳转，点它不该顺带把卡片折叠状态也切了 */}
          <Link to="/stocks" onClick={(e) => e.stopPropagation()}
                className="ml-auto text-[11px] text-accent">
            活跃股池 →
          </Link>
        </div>
        <LifecycleScopeNote scopeNote={data?.scope_note} />
      </summary>
      <LeaderCyclePanel hideScopeNote />
    </details>
  )
}


export default function Dashboard() {
  const navigate = useNavigate()
  const [expandedSector, setExpandedSector] = useState<string | null>(null)

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

  if (loadingState) return <LoadingSpinner />

  return (
    <div className="space-y-5 animate-fade-in">

      {/* 市场状态条已抽到全局 Layout（MarketStateBar），各页顶部统一展示 */}

      {/* ════════════════════════════════════════════════════════════════════════
          赚钱效应模块 —— **排在生命周期之前**（2026-09-09 按用户要求）：
          先看今天整体赚不赚钱、各状态组走成什么样，再往下看具体是哪几只票。
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

              {/* ── 逐日赚钱效应：**按生命周期状态分组** ──
                  换掉旧的四条（昨日涨停龙头/震荡/走弱/破位）。那四组按
                  Stock.phase 分，而 phase 只是"收盘价在哪条均线下面"的单日
                  快照——一只刚断板正在修复的票和一只连跌十天的老龙都可能被叫
                  「震荡龙头」，分出来的组回答不了任何问题。 */}
              <div className="card p-3">
                <div className="flex items-baseline justify-between flex-wrap gap-2 mb-1">
                  <span className="text-xs font-semibold text-text-primary">
                    逐日赚钱效应 · 按生命周期
                  </span>
                  <span className="text-[10px] text-text-muted">
                    每条线 = 昨天处于该状态的票，今天的<span className="text-text-secondary">平均</span>涨幅
                    <span className="text-text-muted/70">
                      ；当天该组没有成员时按 0 画（悬停显示「无成员」）。
                      未收盘时末点是<span className="text-warn">盘中估算</span>，不写库
                    </span>
                  </span>
                </div>
                <LifecycleSeries history={history ?? []} />
              </div>

              {/* ── 分组赚钱效应：**按生命周期分组** ──
                  2026-09-07 换掉旧的四张卡（昨日涨停龙头/震荡/走弱/破位）。
                  那四组按 Stock.phase 分，而 phase 只是"收盘价在哪条均线下面"
                  的单日快照——一只刚断板正在修复的票和一只连跌十天的老龙都可能
                  被叫「震荡龙头」，分出来的组回答不了任何问题。 */}
              <LifecycleEffect />

              {/* 生命周期整块排在板块效应之前（2026-09-09 按用户要求）：
                  先看池子里各状态是哪几只票，再看板块层面的赚钱/亏钱分布 */}
              <LifecycleSection />

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
