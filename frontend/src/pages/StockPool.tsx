import { useState, useMemo, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchStrongPool, fetchLimitMoves } from '@/api/stocks'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { SectorTag, OverflowBadge, LeaderTag, SectorLeaderTag, RegulatoryTag } from '@/components/common/SectorTags'
import { useSectorLeaders } from '@/hooks/useSectorLeaders'
import { useRegulatoryStatus, type RegStatus } from '@/hooks/useRegulatoryStatus'
import {
  useLeaderUniverseMaxes, getLeaderTags, dragonPrimary,
  type LeaderMaxes,
} from '@/hooks/useLeaderUniverseMaxes'
import { Search, Star, Flame, Crown, Info, ChevronUp, ChevronDown, ChevronsUpDown, TrendingDown } from 'lucide-react'
// Note: Star kept for is_leader badge; Flame for limit_up tab; Crown for dragon tab; Info for ColInfo tooltips
import { cn } from '@/utils/cn'
import type { Stock } from '@/types'

// ─── Sort ─────────────────────────────────────────────────────────────────────

type SortKey =
  | 'today_board_count'
  | 'limit_up_days_10d'
  | 'limit_up_days_20d'
  | 'limit_up_days_60d'
  | 'board_count_60d'
  | 'pct_change_10d'
  | 'pct_change_20d'
  | 'pct_change_60d'
  | 'leader_score'
  | 'risk_score'
  | 'today_pct_change'
  | 'phase_group'

type SortDir = 'asc' | 'desc'

// 股票全集口径
type Universe = 'all' | 'strong' | 'limit'
const UNIVERSES: { key: Universe; label: string }[] = [
  { key: 'all',    label: '全部' },
  { key: 'strong', label: '强势股' },
  { key: 'limit',  label: '涨跌停股' },
]

const DEFAULT_SORT: { key: SortKey; dir: SortDir } = { key: 'today_board_count', dir: 'desc' }

// 震荡(0) → 涨停(1) → 走弱(2) → 破位(3)  asc = 最健康在前
const PHASE_RANK: Record<string, number> = {
  oscillating: 0,
  limit_up:    1,
  weakening:   2,
  broken:      3,
}

function sortStocks(stocks: Stock[], key: SortKey, dir: SortDir): Stock[] {
  if (key === 'phase_group') {
    return [...stocks].sort((a, b) => {
      const ra = PHASE_RANK[getGroupKey(a)] ?? 0
      const rb = PHASE_RANK[getGroupKey(b)] ?? 0
      return dir === 'asc' ? ra - rb : rb - ra
    })
  }
  return [...stocks].sort((a, b) => {
    const av = sortValue(a, key)
    const bv = sortValue(b, key)
    return dir === 'desc' ? bv - av : av - bv
  })
}

// 取排序值：跌停股在「连续连板/60日高板」两列用跌停口径字段
function sortValue(s: Stock, key: SortKey): number {
  if (s.today_is_limit_down) {
    if (key === 'today_board_count') return s.today_limit_down_count ?? -Infinity
    if (key === 'board_count_60d')   return (s.board_down_count_60d ?? -Infinity) as number
  }
  return ((s as any)[key] ?? -Infinity) as number
}

// ─── Group definitions ────────────────────────────────────────────────────────

type GroupKey = 'all' | 'dragon' | 'oscillating' | 'limit_up' | 'weakening' | 'broken' | 'limit_down'

interface GroupDef {
  key: GroupKey
  label: string
  color: string
  bgColor: string
}

// Tab display order: 全部 first, then 总龙头, then phase groups
const GROUPS: GroupDef[] = [
  { key: 'all',         label: '全部',     color: '#A78BFA', bgColor: 'rgba(167,139,250,0.10)' },
  { key: 'dragon',      label: '总龙头',   color: '#FFD700', bgColor: 'rgba(255,215,0,0.10)'   },
  { key: 'oscillating', label: '震荡龙头', color: '#4F9CF9', bgColor: 'rgba(79,156,249,0.10)'  },
  { key: 'limit_up',    label: '涨停龙头', color: '#FF4560', bgColor: 'rgba(255,69,96,0.10)'   },
  { key: 'weakening',   label: '走弱龙头', color: '#34D399', bgColor: 'rgba(52,211,153,0.10)' },
  { key: 'broken',      label: '破位龙头', color: '#26C281', bgColor: 'rgba(38,194,129,0.08)'  },
  { key: 'limit_down',  label: '跌停股',   color: '#059669', bgColor: 'rgba(5,150,105,0.12)'   },
]

function getGroupKey(stock: Stock): GroupKey {
  if (stock.today_is_limit_up) return 'limit_up'
  if (stock.today_is_limit_down) return 'limit_down'
  if (stock.phase === 'broken') return 'broken'
  if (stock.phase === 'weakening') return 'weakening'
  return 'oscillating'
}

// ─── Leader-tag helpers ───────────────────────────────────────────────────────

// getLeaderTags / LeaderMaxes 由 useLeaderUniverseMaxes 提供（龙1/龙2 全集口径）

// ─── Phase group tag ─────────────────────────────────────────────────────────

const PHASE_META: Record<Exclude<GroupKey, 'dragon'>, { label: string; color: string; bg: string }> = {
  all:         { label: '全部',     color: '#A78BFA', bg: 'rgba(167,139,250,0.10)' },
  limit_up:    { label: '涨停龙头', color: '#FF4560', bg: 'rgba(255,69,96,0.12)'   },
  oscillating: { label: '震荡龙头', color: '#5EA6FF', bg: 'rgba(94,166,255,0.12)'  },
  weakening:   { label: '走弱龙头', color: '#34D399', bg: 'rgba(52,211,153,0.12)'  },
  broken:      { label: '破位龙头', color: '#26C281', bg: 'rgba(38,194,129,0.10)'  },
  limit_down:  { label: '跌停股',   color: '#059669', bg: 'rgba(5,150,105,0.12)'   },
}

function PhaseGroupTag({ phase }: { phase: Exclude<GroupKey, 'dragon'> }) {
  const { label, color, bg } = PHASE_META[phase]
  return (
    <span
      className="inline-block text-xs px-1.5 py-0.5 rounded font-medium whitespace-nowrap"
      style={{ color, backgroundColor: bg, border: `1px solid ${color}30` }}
    >
      {label}
    </span>
  )
}

// Dragon-group sort（龙1/龙2 两档）：
// 1. 主标签优先级：龙1 组整体在 龙2 组之前；组内 10>20>60>高板>连板（DRAGON_TAG_ORDER）
// 2. 主标签相同 → 标签数多的靠前
// 3. 板块龙头 → 4. 龙头分
function sortDragon(stocks: Stock[], globalMaxes: LeaderMaxes, sectorLeaders: Map<number, string>): Stock[] {
  return [...stocks].sort((a, b) => {
    const tagsA = getLeaderTags(a, globalMaxes)
    const tagsB = getLeaderTags(b, globalMaxes)
    const pa = dragonPrimary(tagsA), pb = dragonPrimary(tagsB)
    if (pa !== pb) return pa - pb
    if (tagsA.length !== tagsB.length) return tagsB.length - tagsA.length
    const slDiff = (sectorLeaders.has(b.id) ? 1 : 0) - (sectorLeaders.has(a.id) ? 1 : 0)
    if (slDiff !== 0) return slDiff
    return b.leader_score - a.leader_score
  })
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function StockPool() {
  const navigate = useNavigate()
  const [search, setSearch] = useState('')
  const [activeTab, setActiveTab] = useState<GroupKey>('all')
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>(DEFAULT_SORT)
  // 用户是否手动点过列头：未点时 全部/总龙头 tab 用龙头优先序(sortDragon)，点过即按列排序
  const [userSorted, setUserSorted] = useState(false)

  // 股票全集口径：全部（强势+涨跌停）/ 仅强势股 / 仅涨跌停股
  const [universe, setUniverse] = useState<Universe>('all')

  const selectTab = (key: GroupKey) => { setActiveTab(key); setUserSorted(false) }

  const { data: strongData, isLoading: loadingStrong } = useQuery({
    queryKey: ['strong-pool-all', search],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500, search }),
    keepPreviousData: true,
  } as any)
  const { data: upData, isLoading: loadingUp } = useQuery({
    queryKey: ['limit-up-all', search],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up', search }),
    keepPreviousData: true,
  } as any)
  const { data: downData, isLoading: loadingDown } = useQuery({
    queryKey: ['limit-down-all', search],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down', search }),
    keepPreviousData: true,
  } as any)
  const isLoading = loadingStrong || loadingUp || loadingDown

  // 按口径合并去重（以股票代码为逻辑主键，id 即 code-based）
  const allStocks: Stock[] = useMemo(() => {
    const strong: Stock[] = (strongData as any)?.items ?? []
    const up: Stock[] = (upData as any)?.items ?? []
    const down: Stock[] = (downData as any)?.items ?? []
    const src =
      universe === 'strong' ? strong
      : universe === 'limit' ? [...up, ...down]
      : [...strong, ...up, ...down]
    const seen = new Set<string | number>()
    const merged: Stock[] = []
    for (const s of src) {
      if (seen.has(s.id)) continue
      seen.add(s.id)
      merged.push(s)
    }
    return merged
  }, [strongData, upData, downData, universe])

  const total = allStocks.length
  const unitLabel = universe === 'strong' ? '强势股' : universe === 'limit' ? '涨跌停股' : '活跃股'

  // ── Global leader maxes：对比【强势池+涨停+跌停】合并全集（与涨跌停池页一致）──
  const globalLeaderMaxes = useLeaderUniverseMaxes()

  // ── 板块龙头（完全支配，由板块分析页数据决定）────────────────────────────
  const sectorLeaders = useSectorLeaders()  // Map<stockId, primarySector>
  const regStatus = useRegulatoryStatus()   // code → 监管状态（警示徽章）

  const grouped = useMemo(() => {
    const map = new Map<GroupKey, Stock[]>(GROUPS.map((g) => [g.key, []]))
    for (const stock of allStocks) {
      // 全部 tab：所有股票
      map.get('all')!.push(stock)
      // Phase groups (mutually exclusive)
      map.get(getGroupKey(stock))!.push(stock)
      // 总龙头 (non-exclusive)：仅全市场龙头标签持有者（10/20/60龙·60高板龙·连板龙 的龙1/龙2）
      // 不再纳入"每个板块的龙头"，避免被上百个板块各自的龙头撑大；板块龙头标签仍在行内展示。
      const hasLeaderTag = getLeaderTags(stock, globalLeaderMaxes).length > 0
      if (hasLeaderTag) {
        map.get('dragon')!.push(stock)
      }
    }
    return map
  }, [allStocks, globalLeaderMaxes, sectorLeaders])

  const activeDef = GROUPS.find((g) => g.key === activeTab)!
  // 当前是否由 sortDragon 控制顺序（未点列头 + 全部/总龙头 tab）
  const usingDragon = !userSorted && (activeTab === 'all' || activeTab === 'dragon')

  const activeStocks = useMemo(() => {
    const stocks = grouped.get(activeTab) ?? []
    if (usingDragon) return sortDragon(stocks, globalLeaderMaxes, sectorLeaders)
    return sortStocks(stocks, sort.key, sort.dir)
  }, [grouped, activeTab, sort, usingDragon, globalLeaderMaxes, sectorLeaders])

  // 传给列头的 sort 状态：sortDragon 模式下清空 key，使所有列头均不高亮
  const headerSort = usingDragon ? { key: '' as SortKey, dir: sort.dir } : sort

  // ── Leader maxes: always global — tags compare against the full pool ────────
  const leaderMaxes = globalLeaderMaxes

  const handleSort = (key: SortKey) => {
    setSort((prev) =>
      // 首次点列头（之前在龙头序）一律从 desc 开始；同列再点才 desc↔asc 切换
      userSorted && prev.key === key
        ? { key, dir: prev.dir === 'desc' ? 'asc' : 'desc' }
        : { key, dir: 'desc' },
    )
    setUserSorted(true)
  }

  return (
    <div className="flex flex-col gap-3 animate-fade-in h-full">
      {/* Top bar: search + total */}
      <div className="flex items-center gap-3 flex-shrink-0">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索股票..."
            className="bg-bg-card border border-bg-border rounded pl-8 pr-3 py-1.5 text-sm text-text-primary focus:outline-none focus:border-accent/50 w-44"
          />
        </div>
        {/* 全集口径切换：全部 / 强势股 / 涨跌停股 */}
        <div className="flex items-center gap-0.5 bg-bg-card border border-bg-border rounded-lg p-0.5">
          {UNIVERSES.map((u) => (
            <button
              key={u.key}
              onClick={() => setUniverse(u.key)}
              className={cn(
                'px-3 py-1 rounded-md text-xs font-medium transition-colors',
                universe === u.key
                  ? 'bg-accent/15 text-accent'
                  : 'text-text-muted hover:text-text-secondary',
              )}
            >
              {u.label}
            </button>
          ))}
        </div>
        <div className="ml-auto text-xs text-text-muted">共 {total} 只{unitLabel}</div>
      </div>

      {/* Tab bar */}
      <div className="flex items-center gap-2 flex-shrink-0">
        {GROUPS.map((grp) => {
          const count = grouped.get(grp.key)?.length ?? 0
          const isActive = grp.key === activeTab
          return (
            <button
              key={grp.key}
              onClick={() => selectTab(grp.key)}
              className={cn(
                'flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg text-sm font-medium transition-all whitespace-nowrap',
                isActive
                  ? 'shadow-sm'
                  : 'text-text-muted hover:text-text-secondary hover:bg-bg-elevated',
              )}
              style={
                isActive
                  ? { backgroundColor: grp.bgColor, color: grp.color, border: `1px solid ${grp.color}40` }
                  : { border: '1px solid transparent' }
              }
            >
              {grp.key === 'all'      && isActive && (
                <Star className="w-3.5 h-3.5 shrink-0" style={{ color: grp.color }} />
              )}
              {grp.key === 'dragon'   && isActive && (
                <Crown className="w-3.5 h-3.5 shrink-0" style={{ color: grp.color }} />
              )}
              {grp.key === 'limit_up' && isActive && (
                <Flame className="w-3.5 h-3.5 shrink-0" style={{ color: grp.color }} />
              )}
              {grp.key === 'limit_down' && isActive && (
                <TrendingDown className="w-3.5 h-3.5 shrink-0" style={{ color: grp.color }} />
              )}
              {grp.label}
              <span
                className="text-xs font-mono px-1 py-px rounded"
                style={
                  isActive
                    ? { backgroundColor: `${grp.color}25`, color: grp.color }
                    : { backgroundColor: 'rgba(255,255,255,0.06)', color: 'inherit' }
                }
              >
                {count}
              </span>
            </button>
          )
        })}
      </div>

      {/* Table panel */}
      <div
        className="card overflow-hidden p-0 flex-1 flex flex-col min-h-0"
        style={{ borderColor: `${activeDef.color}25` }}
      >
        {isLoading ? (
          <div className="p-4"><LoadingRows /></div>
        ) : (
          <div className="overflow-auto flex-1">
            <table className="w-full text-sm">
              <thead className="sticky top-0 z-10 bg-bg-card">
                <tr className="border-b border-bg-border/40">
                  <th className="text-left px-3 py-2 text-xs text-text-secondary/70 font-medium whitespace-nowrap">代码 / 名称</th>
                  <th className="text-left px-3 py-2 text-xs text-text-secondary/70 font-medium">板块</th>
                  <SortTh col="phase_group" label="分组" sort={headerSort} onSort={handleSort} align="left" />
                  <SortTh col="today_board_count" label={activeTab === 'limit_down' ? '连续跌停' : '连续连板'}  sort={headerSort} onSort={handleSort} />
                  <SortTh col="limit_up_days_10d" label="10日涨停"  sort={headerSort} onSort={handleSort} />
                  <SortTh col="limit_up_days_20d" label="20日涨停"  sort={headerSort} onSort={handleSort} />
                  <SortTh col="limit_up_days_60d" label="60日涨停"  sort={headerSort} onSort={handleSort} />
                  <SortTh col="board_count_60d"   label={activeTab === 'limit_down' ? '60日高跌' : '60日高板'}  sort={headerSort} onSort={handleSort} />
                  <SortTh col="pct_change_10d"    label="10日涨幅"  sort={headerSort} onSort={handleSort} />
                  <SortTh col="pct_change_20d"    label="20日涨幅"  sort={headerSort} onSort={handleSort} />
                  <SortTh col="pct_change_60d"    label="60日涨幅"  sort={headerSort} onSort={handleSort} />
                  <th className="px-3 py-2 whitespace-nowrap">
                    <span className="flex items-center justify-end gap-1">
                      <SortHeader label="龙头分" sortKey="leader_score" sort={headerSort} onSort={handleSort} align="right" />
                      <ColInfo {...LEADER_INFO} />
                    </span>
                  </th>
                  <th className="px-3 py-2 whitespace-nowrap">
                    <span className="flex items-center justify-end gap-1">
                      <SortHeader label="风险分" sortKey="risk_score" sort={headerSort} onSort={handleSort} align="right" />
                      <ColInfo {...RISK_INFO} />
                    </span>
                  </th>
                  <SortTh col="today_pct_change"  label="今日涨幅"  sort={headerSort} onSort={handleSort} />
                </tr>
              </thead>
              <tbody>
                {activeStocks.length === 0 ? (
                  <tr>
                    <td colSpan={14} className="py-16 text-center text-text-muted text-sm">暂无数据</td>
                  </tr>
                ) : (
                  activeStocks.map((stock) => (
                    <StockRow
                      key={stock.id}
                      stock={stock}
                      groupColor={activeDef.color}
                      leaderMaxes={leaderMaxes}
                      sectorLeaders={sectorLeaders}
                      regStatus={regStatus.get(stock.code)}
                      onClick={() => navigate(`/stocks/${stock.code}`)}
                    />
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── SortTh (matches LimitMovesPool style) ───────────────────────────────────

function SortTh({ col, label, sort, onSort, align = 'right' }: {
  col: SortKey; label: string
  sort: { key: SortKey; dir: SortDir }
  onSort: (k: SortKey) => void
  align?: 'left' | 'right'
}) {
  const active = sort.key === col
  const justifyClass = align === 'left' ? 'justify-start' : 'justify-end'
  const textClass    = align === 'left' ? 'text-left'     : 'text-right'
  return (
    <th
      onClick={() => onSort(col)}
      className={cn(
        'px-3 py-2 text-xs font-medium cursor-pointer select-none group whitespace-nowrap',
        textClass,
        active ? 'text-accent' : 'text-text-secondary/55 hover:text-text-secondary',
      )}
    >
      <span className={cn('inline-flex items-center gap-0.5', justifyClass)}>
        {label}
        {active
          ? (sort.dir === 'desc' ? <ChevronDown className="w-3 h-3 shrink-0" /> : <ChevronUp className="w-3 h-3 shrink-0" />)
          : <ChevronsUpDown className="w-3 h-3 shrink-0 opacity-0 group-hover:opacity-40 transition-opacity" />
        }
      </span>
    </th>
  )
}

// ─── Sortable column header ───────────────────────────────────────────────────

function SortHeader({
  label,
  sortKey,
  sort,
  onSort,
  align = 'left',
}: {
  label: string
  sortKey: SortKey
  sort: { key: SortKey; dir: SortDir }
  onSort: (k: SortKey) => void
  align?: 'left' | 'right' | 'center'
}) {
  const active = sort.key === sortKey
  return (
    <button
      onClick={() => onSort(sortKey)}
      className={cn(
        'flex items-center gap-0.5 text-xs font-medium transition-colors select-none',
        align === 'right' && 'ml-auto',
        align === 'center' && 'mx-auto',
        active ? 'text-accent' : 'text-text-secondary/55 hover:text-text-secondary',
      )}
    >
      {label}
      {active ? (
        sort.dir === 'desc'
          ? <ChevronDown className="w-3 h-3 shrink-0" />
          : <ChevronUp className="w-3 h-3 shrink-0" />
      ) : (
        <ChevronsUpDown className="w-3 h-3 shrink-0 opacity-30" />
      )}
    </button>
  )
}

// ─── Stock row ────────────────────────────────────────────────────────────────

const MAX_VISIBLE_SECTORS = 6

function StockRow({
  stock,
  groupColor,
  leaderMaxes,
  sectorLeaders,
  regStatus,
  onClick,
}: {
  stock: Stock
  groupColor: string
  leaderMaxes: LeaderMaxes
  sectorLeaders: Map<number, string>   // stockId → primarySector
  regStatus?: RegStatus
  onClick: () => void
}) {
  const sectors = stock.sectors ?? []
  const visible = sectors.slice(0, MAX_VISIBLE_SECTORS)
  const overflow = sectors.length - MAX_VISIBLE_SECTORS

  const leaderTags = getLeaderTags(stock, leaderMaxes)
  const phaseGroup = getGroupKey(stock) as Exclude<GroupKey, 'dragon'>

  const sectorName = sectorLeaders.get(stock.id)
  const leadSectors: string[] = sectorName ? [sectorName] : []

  return (
    <tr
      className="border-b border-bg-border/25 hover:bg-bg-elevated cursor-pointer transition-colors last:border-0"
      onClick={onClick}
    >
      {/* Code + Name */}
      <td className="px-3 py-2.5">
        <div className="font-mono text-accent text-xs">{stock.code}</div>
        <div className="text-text-primary font-medium flex items-center gap-1 whitespace-nowrap">
          {stock.name}
          {stock.is_leader && (
            <Star className="w-3 h-3 text-yellow-400 fill-yellow-400 shrink-0" />
          )}
          {regStatus && <RegulatoryTag status={regStatus} />}
        </div>
        {(leaderTags.length > 0 || leadSectors.length > 0) && (
          <div className="flex flex-wrap gap-0.5 mt-0.5">
            {leaderTags.map(t => <LeaderTag key={t} label={t} />)}
            {leadSectors.map(n => <SectorLeaderTag key={n} name={n} />)}
          </div>
        )}
      </td>

      {/* Sector tags */}
      <td className="px-3 py-2.5 max-w-[260px]">
        <div className="flex flex-wrap gap-1">
          {visible.map((name) => <SectorTag key={name} name={name} />)}
          {overflow > 0 && <OverflowBadge count={overflow} hidden={sectors.slice(MAX_VISIBLE_SECTORS)} />}
          {sectors.length === 0 && <span className="text-xs text-text-muted">—</span>}
        </div>
      </td>

      {/* 分组 */}
      <td className="px-3 py-2.5 whitespace-nowrap">
        <PhaseGroupTag phase={phaseGroup} />
      </td>

      {/* 连续连板（跌停股 → 连续跌停数） */}
      <td className="px-3 py-2.5 text-right font-mono text-xs">
        {(() => {
          const isDown = stock.today_is_limit_down
          const cnt = isDown ? (stock.today_limit_down_count ?? 0) : (stock.today_board_count ?? 0)
          if (!cnt) return <span className="text-text-muted/70">—</span>
          return (
            <span className={cn(
              'font-bold px-1 py-px rounded',
              isDown
                ? cnt >= 3 ? 'bg-down/20 text-down' : 'text-down/70'
                : cnt >= 3 ? 'text-dragon' : 'text-up',
            )}>
              {cnt}板
            </span>
          )
        })()}
      </td>

      {/* 10日涨停 */}
      <td className="px-3 py-2.5 text-right font-mono text-xs">
        <span className={cn(
          stock.limit_up_days_10d >= 3 ? 'text-dragon font-bold' :
          stock.limit_up_days_10d >= 2 ? 'text-up' : 'text-text-secondary',
        )}>
          {stock.limit_up_days_10d || '—'}
        </span>
      </td>

      {/* 20日涨停 */}
      <td className="px-3 py-2.5 text-right font-mono text-xs">
        <span className={cn(
          stock.limit_up_days_20d >= 5 ? 'text-dragon font-bold' :
          stock.limit_up_days_20d >= 3 ? 'text-up' : 'text-text-secondary',
        )}>
          {stock.limit_up_days_20d || '—'}
        </span>
      </td>

      {/* 60日涨停 */}
      <td className="px-3 py-2.5 text-right font-mono text-xs">
        <span className={cn(
          stock.limit_up_days_60d >= 5 ? 'text-dragon font-bold' :
          stock.limit_up_days_60d >= 3 ? 'text-up' : 'text-text-secondary',
        )}>
          {stock.limit_up_days_60d || '—'}
        </span>
      </td>

      {/* 60日高板（跌停股 → 60日高跌） */}
      <td className="px-3 py-2.5 text-right font-mono text-xs">
        {(() => {
          const isDown = stock.today_is_limit_down
          const val = isDown ? (stock.board_down_count_60d ?? 0) : stock.board_count_60d
          if (!val) return <span className="text-text-secondary">—</span>
          return (
            <span className={cn(
              val >= 5
                ? (isDown ? 'text-down font-bold' : 'text-dragon font-bold')
                : 'text-text-secondary',
            )}>
              {val}
            </span>
          )
        })()}
      </td>

      {/* 10日涨幅 */}
      <td className="px-3 py-2.5 text-right font-mono text-xs">
        {stock.pct_change_10d != null ? (
          <span className={cn('font-medium', stock.pct_change_10d > 0 ? 'text-up' : stock.pct_change_10d < 0 ? 'text-down' : 'text-text-muted')}>
            {stock.pct_change_10d > 0 ? '+' : ''}{stock.pct_change_10d.toFixed(1)}%
          </span>
        ) : <span className="text-text-muted">—</span>}
      </td>

      {/* 20日涨幅 */}
      <td className="px-3 py-2.5 text-right font-mono text-xs">
        {stock.pct_change_20d != null ? (
          <span className={cn('font-medium', stock.pct_change_20d > 0 ? 'text-up' : stock.pct_change_20d < 0 ? 'text-down' : 'text-text-muted')}>
            {stock.pct_change_20d > 0 ? '+' : ''}{stock.pct_change_20d.toFixed(1)}%
          </span>
        ) : <span className="text-text-muted">—</span>}
      </td>

      {/* 60日涨幅 */}
      <td className="px-3 py-2.5 text-right font-mono text-xs">
        {stock.pct_change_60d != null ? (
          <span className={cn('font-medium', stock.pct_change_60d > 0 ? 'text-up' : stock.pct_change_60d < 0 ? 'text-down' : 'text-text-muted')}>
            {stock.pct_change_60d > 0 ? '+' : ''}{stock.pct_change_60d.toFixed(1)}%
          </span>
        ) : <span className="text-text-muted">—</span>}
      </td>

      {/* 龙头分 */}
      <td className="px-3 py-2.5 text-right font-mono text-xs text-text-secondary">
        {stock.leader_score.toFixed(0)}
      </td>

      {/* 风险分 */}
      <td className="px-3 py-2.5 text-right font-mono text-xs">
        <span className={cn(stock.risk_score >= 50 ? 'text-down' : 'text-text-secondary')}>
          {stock.risk_score.toFixed(0)}
        </span>
      </td>

      {/* 今日涨幅 */}
      <td className="px-3 py-2.5 text-right font-mono text-xs">
        {stock.today_pct_change != null ? (
          <span className={cn('font-bold', stock.today_pct_change > 0 ? 'text-up' : 'text-down')}>
            {stock.today_pct_change > 0 ? '+' : ''}{stock.today_pct_change.toFixed(2)}%
          </span>
        ) : <span className="text-text-muted">—</span>}
      </td>
    </tr>
  )
}

// ─── Column info tooltip ──────────────────────────────────────────────────────

interface FormulaLine {
  factor: string
  formula: string
  note?: string
}

interface ColInfoProps {
  title: string
  subtitle?: string
  range?: string
  lines: FormulaLine[]
}

function ColInfo({ title, subtitle, range, lines }: ColInfoProps) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const ref = useRef<HTMLSpanElement>(null)

  const handleEnter = () => {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect()
      const vpW = window.innerWidth
      const W = 380
      // prefer right-aligned to icon; if that overflows left, pin to 8px from right edge
      let left = r.left
      if (left + W > vpW - 8) left = Math.max(8, vpW - W - 8)
      setPos({ top: r.bottom + 6, left })
    }
  }

  return (
    <span
      ref={ref}
      className="inline-flex items-center gap-0.5 cursor-default"
      onMouseEnter={handleEnter}
      onMouseLeave={() => setPos(null)}
    >
      <Info className="w-3 h-3 text-text-muted/85 hover:text-accent transition-colors" />
      {pos && (
        <div
          className="fixed z-50 rounded-lg shadow-xl border border-bg-border bg-bg-card overflow-hidden"
          style={{ top: pos.top, left: pos.left, width: 380 }}
          onMouseEnter={handleEnter}
          onMouseLeave={() => setPos(null)}
        >
          {/* Header */}
          <div className="px-3 py-2 border-b border-bg-border/60 bg-bg-elevated/50">
            <div className="text-xs font-semibold text-text-primary">{title}</div>
            {subtitle && <div className="text-xs text-text-muted mt-0.5">{subtitle}</div>}
            {range && (
              <span className="inline-block mt-1 text-xs font-mono text-accent bg-accent/10 px-1.5 py-0.5 rounded">
                {range}
              </span>
            )}
          </div>
          {/* Formula rows */}
          <div className="p-2 space-y-1">
            {lines.map((l, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <span className="shrink-0 text-text-muted w-[72px] text-right leading-relaxed">{l.factor}</span>
                <span className="font-mono text-accent/90 leading-relaxed flex-1">{l.formula}</span>
                {l.note && <span className="text-text-muted/70 leading-relaxed text-right">{l.note}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </span>
  )
}

// ─── Score meta definitions ───────────────────────────────────────────────────

const LEADER_INFO: ColInfoProps = {
  title: '龙头分',
  subtitle: '综合衡量当前板块领涨地位，越高越强',
  range: '0 – 100',
  lines: [
    { factor: '当前连板',   formula: 'min(30, board_now × 11)',       note: '≤30' },
    { factor: '近10日涨停', formula: '(lup_10 ÷ 7) × 30',            note: '≤30' },
    { factor: '情绪归一化', formula: '(emotion−20) ÷ 60 × 20',       note: '≤20' },
    { factor: '历史板高',   formula: '(board60−1) ÷ 7 × 12',         note: '≤12' },
    { factor: '60日密度',   formula: '(lup60−3) ÷ 18 × 8',           note: '≤8'  },
    { factor: '今日涨停',   formula: '+5',                             note: '奖励' },
    { factor: '今日换手',   formula: 'min(5, turnover × 0.5)',        note: '≤5'  },
    { factor: '板块龙头',   formula: '+12',                            note: '若 is_leader' },
    { factor: '炸板惩罚',   formula: '−12',                            note: '若炸板' },
  ],
}

const RISK_INFO: ColInfoProps = {
  title: '风险分',
  subtitle: '越高越危险；炸板时间与连跌是主要信号',
  range: '0 – 100',
  lines: [
    { factor: '近3日炸板',   formula: 'broken_3d × 28',               note: '高危' },
    { factor: '3-10日炸板',  formula: 'broken_7d × 12',               note: '中危' },
    { factor: '高板位风险',  formula: 'max(0, board60−4) × 8',        note: '≤32' },
    { factor: '今日跌停',    formula: '+15',                           note: '若跌停' },
    { factor: '连续下跌',    formula: 'min(30, declines × 8)',         note: '≤30' },
  ],
}

// SectorTag, OverflowBadge, getSectorColor — imported from @/components/common/SectorTags
