/**
 * 涨跌停分析 — 仪表盘
 * 功能：近期涨停/跌停趋势曲线 + 板块集中度（饼图 + 排名列表 + 跨板块分析）
 */
import { useMemo, useState } from 'react'
import { useQuery, useQueries } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  fetchLimitMoves, fetchLimitMovesTrend,
  fetchSectorLimitTrend, fetchSectorLimitTrendOptions,
  fetchStrongPool,
} from '@/api/stocks'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { getSectorColor, SectorTag, OneWordCountTag } from '@/components/common/SectorTags'
import { SectorSection } from '@/components/common/SectorSection'
import { ArrowUp, ArrowDown, TrendingUp, Search, X, ChevronDown } from 'lucide-react'
import { cn } from '@/utils/cn'
import { format } from 'date-fns'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, PieChart, Pie, Cell,
} from 'recharts'
import type { Stock, SectorLimitTrendOption, SectorLimitTrendPoint } from '@/types'

// ─── Palette ──────────────────────────────────────────────────────────────────
const C_UP   = '#FF4560'
const C_DOWN = '#26C281'
const C_OTHER = '#505570'
// 走势图最多同时叠加的板块数（再多图就看不清了）
const MAX_OVERLAY = 4

// ─── 高弹性板块判定（涨跌停阈值 >10%：科创板/创业板 ±20%、北交所 ±30%）──────────
// 与后端 eastmoney_fetcher.get_limit_pct 的前缀口径保持一致
const isBjMarket = (code: string) => /^(4|8|92)/.test(code)
const isHighElasticity = (code: string) => code.startsWith('688') || code.startsWith('300') || code.startsWith('301')
const marketTag = (code: string): string | null => {
  if (isBjMarket(code)) return '北交所'
  if (code.startsWith('688')) return '科创板'
  if (code.startsWith('300') || code.startsWith('301')) return '创业板'
  return null
}

// ─── Sector stat with stock tracking ─────────────────────────────────────────

interface SectorStat {
  name: string
  limit_up: number
  limit_down: number
  total: number
  up_one_word: number       // 板块内当日一字板涨停数
  down_one_word: number     // 板块内当日一字板跌停数
  up_max_board: number      // 板块内涨停股最高连板数（today_board_count）
  down_max_board: number    // 板块内跌停股最高连续跌停数（today_limit_down_count）
  up_stock_ids: number[]    // stock IDs in this sector among today's up list
  down_stock_ids: number[]  // stock IDs in this sector among today's down list
}

function buildSectorStats(upStocks: Stock[], downStocks: Stock[]): SectorStat[] {
  const map = new Map<string, {
    limit_up: number; limit_down: number; up_one_word: number; down_one_word: number
    up_max_board: number; down_max_board: number
    up_ids: number[]; down_ids: number[]
  }>()
  const get = (sec: string) => {
    let e = map.get(sec)
    if (!e) { e = { limit_up: 0, limit_down: 0, up_one_word: 0, down_one_word: 0, up_max_board: 0, down_max_board: 0, up_ids: [], down_ids: [] }; map.set(sec, e) }
    return e
  }

  for (const s of upStocks) {
    const board = s.today_board_count ?? 0
    for (const sec of s.sectors ?? []) {
      const e = get(sec)
      e.limit_up++; e.up_ids.push(s.id)
      if (s.today_is_one_word_limit_up) e.up_one_word++
      if (board > e.up_max_board) e.up_max_board = board
    }
  }
  for (const s of downStocks) {
    const board = s.today_limit_down_count ?? 0
    for (const sec of s.sectors ?? []) {
      const e = get(sec)
      e.limit_down++; e.down_ids.push(s.id)
      if (s.today_is_one_word_limit_down) e.down_one_word++
      if (board > e.down_max_board) e.down_max_board = board
    }
  }

  const result: SectorStat[] = []
  for (const [name, v] of map) {
    result.push({
      name, limit_up: v.limit_up, limit_down: v.limit_down,
      total: v.limit_up + v.limit_down,
      up_one_word: v.up_one_word, down_one_word: v.down_one_word,
      up_max_board: v.up_max_board, down_max_board: v.down_max_board,
      up_stock_ids: v.up_ids, down_stock_ids: v.down_ids,
    })
  }
  return result
}

// ─── Chart tooltip ────────────────────────────────────────────────────────────

function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload ?? {}
  return (
    <div className="card p-2.5 text-xs space-y-1 shadow-xl border border-bg-border/60 min-w-[180px]">
      <div className="text-text-muted mb-1">{label}</div>
      {payload.map((p: any) => (
        <div key={p.name} className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: p.color }} />
          <span className="text-text-secondary">{p.name}</span>
          <span className="font-mono ml-auto font-semibold" style={{ color: p.color }}>{p.value}</span>
        </div>
      ))}
      {(d._topUp || d._topDown) && (
        <div className="pt-1 mt-1 border-t border-bg-border/40 space-y-0.5">
          {d._topUp && (
            <div className="flex items-center gap-1.5">
              <span className="text-up">涨停最多</span>
              <span className="text-text-primary ml-auto">{d._topUp} <span className="font-mono text-up font-bold">×{d._topUpN}</span></span>
            </div>
          )}
          {d._topDown && (
            <div className="flex items-center gap-1.5">
              <span className="text-down">跌停最多</span>
              <span className="text-text-primary ml-auto">{d._topDown} <span className="font-mono text-down font-bold">×{d._topDownN}</span></span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function PieCustomTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const p = payload[0]
  return (
    <div className="card px-2.5 py-1.5 text-xs shadow-xl border border-bg-border/60">
      <span className="font-medium" style={{ color: p.payload.fill }}>{p.name}</span>
      <span className="text-text-muted ml-2">{p.value} 只</span>
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function LimitMovesDashboard() {
  // 点击图例切换曲线显隐（默认全部显示）
  const [hiddenLines, setHiddenLines] = useState<Set<string>>(new Set())
  const toggleLine = (key?: string) => {
    if (!key) return
    setHiddenLines((prev) => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  // 选择历史交易日（''=最新）
  const [selDate, setSelDate] = useState('')
  const dateKey = selDate || 'latest'

  const { data: upData,   isLoading: upLoading }   = useQuery({
    queryKey: ['limit-moves', 'limit_up', dateKey],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up', date: selDate || undefined }),
  } as any)

  const { data: downData, isLoading: downLoading } = useQuery({
    queryKey: ['limit-moves', 'limit_down', dateKey],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down', date: selDate || undefined }),
  } as any)

  const { data: trendData, isLoading: trendLoading } = useQuery({
    queryKey: ['limit-moves-trend', 30],
    queryFn: () => fetchLimitMovesTrend(30),
  } as any)

  // 叠加板块：可多选（≤MAX_OVERLAY），在走势图上叠加各板块每日涨停/跌停数，对比板块间走势相关性
  const [selSectors, setSelSectors] = useState<string[]>([])
  const toggleSector = (name: string) =>
    setSelSectors(prev => prev.includes(name)
      ? prev.filter(n => n !== name)
      : prev.length >= MAX_OVERLAY ? prev : [...prev, name])
  const { data: sectorOptions } = useQuery({
    queryKey: ['sector-limit-trend-options', 30],
    queryFn: () => fetchSectorLimitTrendOptions(30),
  } as any)
  const sectorTrendQueries = useQueries({
    queries: selSectors.map(name => ({
      queryKey: ['sector-limit-trend', name, 30],
      queryFn: () => fetchSectorLimitTrend(name, 30),
      staleTime: 5 * 60_000,
    })),
  })
  const sectorTrendLoading = sectorTrendQueries.some(q => q.isLoading)

  // 可选日期（近30个交易日，来自 trend），最新在前
  const dateOptions: string[] = useMemo(
    () => ((trendData as any) ?? []).map((p: any) => p.date).reverse(),
    [trendData],
  )

  const limitUps:   Stock[] = (upData   as any)?.items ?? []
  const limitDowns: Stock[] = (downData as any)?.items ?? []

  // 近5/10日「每日涨停最多 / 跌停最多板块」出现次数统计（识别酝酿/退潮主线）
  const domFreq = useMemo(() => {
    const raw: any[] = (trendData as any) ?? []
    const count = (field: string, days: number) => {
      const m = new Map<string, number>()
      for (const p of raw.slice(-days)) if (p[field]) m.set(p[field], (m.get(p[field]) ?? 0) + 1)
      return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    }
    return {
      up5: count('top_up_sector', 5), up10: count('top_up_sector', 10),
      down5: count('top_down_sector', 5), down10: count('top_down_sector', 10),
    }
  }, [trendData])

  // ── Trend chart data ──────────────────────────────────────────────────────
  const chartDataBase = useMemo(() => {
    const raw: any[] = (trendData as any) ?? []
    const W = 30  // 滚动窗口：近30个交易日
    const round1 = (v: number) => Math.round(v * 10) / 10
    return raw.map((p, i) => {
      const win = raw.slice(Math.max(0, i - W + 1), i + 1)
      const avgUp   = win.reduce((s, q) => s + q.limit_up_count,   0) / win.length
      const avgDown = win.reduce((s, q) => s + q.limit_down_count, 0) / win.length
      return {
        date: format(new Date(p.date), 'MM/dd'),
        _rawDate: p.date,
        '涨停': p.limit_up_count,
        '跌停': p.limit_down_count,
        '涨停30日均值': round1(avgUp),
        '跌停30日均值': round1(avgDown),
        _topUp: p.top_up_sector, _topUpN: p.top_up_sector_count,
        _topDown: p.top_down_sector, _topDownN: p.top_down_sector_count,
      }
    })
  }, [trendData])

  // 叠加板块序列（数据量小，每次渲染直接合并；selSectors 长度可变，不适合 useMemo 依赖）
  const sectorSeries = selSectors.map((name, i) => ({
    name,
    color: getSectorColor(name),
    byDate: new Map(
      ((((sectorTrendQueries[i]?.data as any) ?? []) as SectorLimitTrendPoint[]).map(p => [p.date, p])),
    ),
  }))
  const chartData = chartDataBase.map(row => {
    const out: any = { ...row }
    for (const s of sectorSeries) {
      if (s.byDate.size === 0) continue
      const sp = s.byDate.get(row._rawDate)
      out[`${s.name}·涨停`] = sp?.limit_up_count ?? 0
      out[`${s.name}·跌停`] = sp?.limit_down_count ?? 0
    }
    return out
  })

  const avg = useMemo(() => {
    const raw: any[] = (trendData as any) ?? []
    const last10 = raw.slice(-10)
    if (!last10.length) return { up: 0, down: 0 }
    return {
      up:   Math.round(last10.reduce((s, p) => s + p.limit_up_count,   0) / last10.length),
      down: Math.round(last10.reduce((s, p) => s + p.limit_down_count, 0) / last10.length),
    }
  }, [trendData])

  // ── Sector hotspot ────────────────────────────────────────────────────────
  const sectorStats = useMemo(() => buildSectorStats(limitUps, limitDowns), [limitUps, limitDowns])

  const topUpSectors = useMemo(
    () =>
      [...sectorStats]
        .filter(s => s.limit_up > 0)
        .sort((a, b) =>
          // 涨停数从大到小；相同则最高连板数从大到小
          b.limit_up !== a.limit_up
            ? b.limit_up - a.limit_up
            : b.up_max_board - a.up_max_board,
        )
        .slice(0, 10),
    [sectorStats],
  )
  const topDownSectors = useMemo(
    () =>
      [...sectorStats]
        .filter(s => s.limit_down > 0)
        .sort((a, b) =>
          // 跌停数从大到小；相同则最高连续跌停数从大到小
          b.limit_down !== a.limit_down
            ? b.limit_down - a.limit_down
            : b.down_max_board - a.down_max_board,
        )
        .slice(0, 10),
    [sectorStats],
  )

  // ── 二板及以上（过滤首板）：看板块持续强度 ──────────────────────────────
  // 涨停连板数 today_board_count>=2；跌停连续跌停数 today_limit_down_count>=2
  const upStocks2   = useMemo(() => limitUps.filter(s => (s.today_board_count ?? 0) >= 2), [limitUps])
  const downStocks2 = useMemo(() => limitDowns.filter(s => (s.today_limit_down_count ?? 0) >= 2), [limitDowns])
  const sectorStats2 = useMemo(() => buildSectorStats(upStocks2, downStocks2), [upStocks2, downStocks2])
  const topUpSectors2 = useMemo(
    () => [...sectorStats2].filter(s => s.limit_up > 0)
      .sort((a, b) => b.limit_up !== a.limit_up ? b.limit_up - a.limit_up : b.up_max_board - a.up_max_board)
      .slice(0, 10),
    [sectorStats2],
  )
  const topDownSectors2 = useMemo(
    () => [...sectorStats2].filter(s => s.limit_down > 0)
      .sort((a, b) => b.limit_down !== a.limit_down ? b.limit_down - a.limit_down : b.down_max_board - a.down_max_board)
      .slice(0, 10),
    [sectorStats2],
  )

  // ── 连板梯队（≥2连板，涨跌停都要）：直接看个股，反映资金强推的方向 ──────────
  const upLadder = useMemo(
    () => [...upStocks2].sort((a, b) =>
      (b.today_board_count ?? 0) - (a.today_board_count ?? 0)
      || (b.today_pct_change ?? 0) - (a.today_pct_change ?? 0)),
    [upStocks2],
  )
  const downLadder = useMemo(
    () => [...downStocks2].sort((a, b) =>
      (b.today_limit_down_count ?? 0) - (a.today_limit_down_count ?? 0)
      || (a.today_pct_change ?? 0) - (b.today_pct_change ?? 0)),
    [downStocks2],
  )

  // ── 高弹性板块涨跌停（创业板/科创板 ±20%、北交所 ±30%，与主板±10%区分）────
  const highElasticUp = useMemo(
    () => limitUps.filter(s => isHighElasticity(s.code) || isBjMarket(s.code))
      .sort((a, b) => (b.today_pct_change ?? 0) - (a.today_pct_change ?? 0)),
    [limitUps],
  )
  const highElasticDown = useMemo(
    () => limitDowns.filter(s => isHighElasticity(s.code) || isBjMarket(s.code))
      .sort((a, b) => (a.today_pct_change ?? 0) - (b.today_pct_change ?? 0)),
    [limitDowns],
  )

  const dataLoading = upLoading || downLoading

  // 点击集中板块 → 展开该板块成员个股（复用 SectorSection，与 Dashboard 一致）
  // 同一板块的涨停+跌停股合并展示（SectorSection 会按涨停/跌停龙头分组）。
  // 主区(上)与 ≥2板区(下) 各自独立的展开状态，互不影响。
  // Exp 只存 name/side，成员股在下方按开关状态实时计算（而非点击时冻结），
  // 这样切换「+强势股」开关能立刻反映，不用重新点击板块。
  const navigate = useNavigate()
  type Side = 'up' | 'down'
  type Exp = { name: string; side: Side } | null
  // 页面级统一收起/展开：一个按钮控制本页全部股票列表卡片（涨跌停集中板块/
  // ≥2板/连板梯队/高弹性板块共8张），只影响列表卡片，不影响走势图等其它内容。
  const [listsCollapsed, setListsCollapsed] = useState(false)
  const [expMain, setExpMain] = useState<Exp>(null)
  const [exp2, setExp2] = useState<Exp>(null)
  // 连板梯队 / 高弹性板块涨跌停：点击板块标签展开，与「涨停集中板块」同一套数据
  // 展示逻辑（同一交易日全量涨跌停 + 可选叠加强势股），只是各自独立的展开状态，
  // 让面板出现在点击处附近，不用滚回页面顶部。
  const [expLadder, setExpLadder] = useState<Exp>(null)
  const [expElastic, setExpElastic] = useState<Exp>(null)
  // 「涨跌停 + 强势股」开关：各展开面板各自独立
  const [showStrongMain, setShowStrongMain] = useState(false)
  const [showStrong2, setShowStrong2] = useState(false)
  const [showStrongLadder, setShowStrongLadder] = useState(false)
  const [showStrongElastic, setShowStrongElastic] = useState(false)

  const mkToggle = (setter: (fn: (p: Exp) => Exp) => void) =>
    (name: string, side: Side) =>
      setter(p => (p && p.name === name && p.side === side) ? null : { name, side })
  const toggleMain = mkToggle(setExpMain)
  const toggle2 = mkToggle(setExp2)
  const toggleLadder = mkToggle(setExpLadder)
  const toggleElastic = mkToggle(setExpElastic)

  // 板块内强势股（懒加载：仅当任一「+强势股」开关开启时才拉取，与涨跌停股合并去重）
  const { data: strongPoolData } = useQuery({
    queryKey: ['strong-pool-for-limit-moves'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
    enabled: showStrongMain || showStrong2 || showStrongLadder || showStrongElastic,
    staleTime: 5 * 60_000,
  } as any)
  const strongPoolStocks: Stock[] = (strongPoolData as any)?.items ?? []

  const mergeStrong = (base: Stock[], sectorName: string, on: boolean): Stock[] => {
    if (!on) return base
    const ids = new Set(base.map(s => s.id))
    const extra = strongPoolStocks.filter(s => (s.sectors ?? []).includes(sectorName) && !ids.has(s.id))
    return [...base, ...extra]
  }

  const expMainStocks = useMemo(() => {
    if (!expMain) return []
    const base = [...limitUps, ...limitDowns].filter(s => (s.sectors ?? []).includes(expMain.name))
    return mergeStrong(base, expMain.name, showStrongMain)
  }, [expMain, limitUps, limitDowns, showStrongMain, strongPoolStocks])

  const exp2Stocks = useMemo(() => {
    if (!exp2) return []
    const base = [...upStocks2, ...downStocks2].filter(s => (s.sectors ?? []).includes(exp2.name))
    return mergeStrong(base, exp2.name, showStrong2)
  }, [exp2, upStocks2, downStocks2, showStrong2, strongPoolStocks])

  // 连板梯队 / 高弹性板块涨跌停的板块点击展开：与「涨停集中板块」同一数据口径
  // ——当日全量涨跌停股按板块名过滤（不局限于≥2板或高弹性市场子集）。
  const expLadderStocks = useMemo(() => {
    if (!expLadder) return []
    const base = [...limitUps, ...limitDowns].filter(s => (s.sectors ?? []).includes(expLadder.name))
    return mergeStrong(base, expLadder.name, showStrongLadder)
  }, [expLadder, limitUps, limitDowns, showStrongLadder, strongPoolStocks])

  const expElasticStocks = useMemo(() => {
    if (!expElastic) return []
    const base = [...limitUps, ...limitDowns].filter(s => (s.sectors ?? []).includes(expElastic.name))
    return mergeStrong(base, expElastic.name, showStrongElastic)
  }, [expElastic, limitUps, limitDowns, showStrongElastic, strongPoolStocks])

  const renderExp = (
    exp: Exp, stocks: Stock[], close: () => void,
    showStrong: boolean, setShowStrong: (v: boolean) => void,
  ) =>
    exp ? (
      <div className="space-y-2">
        <div className="flex items-center justify-start">
          <StrongToggle on={showStrong} onChange={setShowStrong} />
        </div>
        <SectorSection
          group={{ name: exp.name, stocks }}
          collapsed={false}
          onToggle={close}
          onClickStock={(code) => navigate(`/stocks/${code}`)}
        />
      </div>
    ) : null

  return (
    <div className="space-y-4 animate-fade-in">

      {/* ── Stat cards ─────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 flex-wrap">
        <StatCard
          icon={<ArrowUp className="w-4 h-4" />}
          label={selDate ? '当日涨停' : '今日涨停'}
          value={dataLoading ? null : limitUps.length}
          sub={dataLoading ? null : `近10日均 ${avg.up}`}
          color="up"
        />
        <StatCard
          icon={<ArrowDown className="w-4 h-4" />}
          label={selDate ? '当日跌停' : '今日跌停'}
          value={dataLoading ? null : limitDowns.length}
          sub={dataLoading ? null : `近10日均 ${avg.down}`}
          color="down"
        />
        {!dataLoading && limitDowns.length > 0 && (
          <div className="card px-4 py-3 flex items-center gap-3">
            <TrendingUp className="w-4 h-4 text-accent/60" />
            <div>
              <div className="text-xs text-text-muted">涨跌比</div>
              <div className="text-xl font-bold font-mono text-accent leading-none mt-0.5">
                {limitUps.length}:{limitDowns.length}
                <span className="text-sm font-normal text-text-muted ml-1.5">
                  ({(limitUps.length / limitDowns.length).toFixed(1)}x)
                </span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* ── Trend chart ────────────────────────────────────────────────── */}
      <div className="card p-4 space-y-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-3 flex-wrap">
            <span className="text-sm font-semibold text-text-primary">近期涨停 / 跌停走势</span>
            <SectorPicker
              options={((sectorOptions as any) ?? []) as SectorLimitTrendOption[]}
              values={selSectors}
              onToggle={toggleSector}
              onClear={() => setSelSectors([])}
            />
            {sectorTrendLoading && (
              <span className="text-xs text-text-muted animate-pulse">加载板块数据…</span>
            )}
          </div>
          <span className="text-xs text-text-muted">近30个交易日</span>
        </div>
        <div className="h-52">
          {trendLoading ? (
            <div className="h-full flex items-center justify-center"><LoadingRows /></div>
          ) : chartData.length === 0 ? (
            <div className="h-full flex items-center justify-center text-text-muted text-sm">暂无历史数据</div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chartData} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: '#737A96', fontSize: 11 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
                <YAxis tick={{ fill: '#737A96', fontSize: 11 }} axisLine={false} tickLine={false} width={32} />
                <Tooltip content={<CustomTooltip />} />
                <Legend
                  wrapperStyle={{ fontSize: 12, color: '#A2A9C4', paddingTop: 4, cursor: 'pointer' }}
                  iconSize={8}
                  onClick={(e: any) => toggleLine(e?.dataKey ?? e?.value)}
                  formatter={(value: string, entry: any) => (
                    <span style={{ opacity: hiddenLines.has(entry?.dataKey ?? value) ? 0.35 : 1 }}>{value}</span>
                  )}
                />
                <Line type="monotone" dataKey="涨停" stroke={C_UP}   strokeWidth={2} dot={false} activeDot={{ r: 4 }} hide={hiddenLines.has('涨停')} />
                <Line type="monotone" dataKey="跌停" stroke={C_DOWN} strokeWidth={2} dot={false} activeDot={{ r: 4 }} hide={hiddenLines.has('跌停')} />
                <Line type="monotone" dataKey="涨停30日均值" stroke={C_UP}   strokeWidth={1.5} strokeDasharray="5 4" strokeOpacity={0.5} dot={false} activeDot={false} hide={hiddenLines.has('涨停30日均值')} />
                <Line type="monotone" dataKey="跌停30日均值" stroke={C_DOWN} strokeWidth={1.5} strokeDasharray="5 4" strokeOpacity={0.5} dot={false} activeDot={false} hide={hiddenLines.has('跌停30日均值')} />
                {sectorSeries.flatMap(s => [
                  <Line key={`${s.name}-up`} type="monotone" dataKey={`${s.name}·涨停`} stroke={s.color} strokeWidth={2} dot={{ r: 2, fill: s.color, strokeWidth: 0 }} activeDot={{ r: 4 }} hide={hiddenLines.has(`${s.name}·涨停`)} />,
                  <Line key={`${s.name}-down`} type="monotone" dataKey={`${s.name}·跌停`} stroke={s.color} strokeWidth={1.5} strokeDasharray="6 3" strokeOpacity={0.75} dot={false} activeDot={{ r: 3 }} hide={hiddenLines.has(`${s.name}·跌停`)} />,
                ])}
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      {/* ── 近期主导板块统计（每日涨停/跌停最多板块的出现次数）── */}
      {((trendData as any)?.length ?? 0) > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <DomPanel title="涨停主导板块 · 酝酿" hint="近5/10日「当日涨停最多板块」出现次数，次数越多=该板块持续爆发涨停" d5={domFreq.up5} d10={domFreq.up10} accent="up" />
          <DomPanel title="跌停主导板块 · 退潮" hint="近5/10日「当日跌停最多板块」出现次数，次数越多=该板块持续爆发跌停" d5={domFreq.down5} d10={domFreq.down10} accent="down" />
        </div>
      )}

      {/* 日期选择：仅影响下方「集中板块 / 二板」等当日数据（不影响上方走势图与主导统计） */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs text-text-muted">查看日期</span>
        <select
          value={selDate}
          onChange={(e) => setSelDate(e.target.value)}
          className={cn('bg-bg-card border rounded-lg px-2.5 py-1.5 text-sm focus:outline-none cursor-pointer',
            selDate ? 'border-accent/50 text-accent' : 'border-bg-border text-text-primary')}
        >
          <option value="">最新（今日）</option>
          {dateOptions.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        {selDate && <span className="text-[10px] px-1.5 py-0.5 rounded bg-warn/15 text-warn">历史 · 仅影响下方集中板块/二板</span>}
        <button
          onClick={() => setListsCollapsed(v => !v)}
          className="ml-auto flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-bg-border bg-bg-elevated text-text-secondary hover:text-text-primary hover:border-accent/40 transition-colors"
          title="统一收起/展开本页全部股票列表卡片"
        >
          <ChevronDown className={cn('w-3.5 h-3.5 transition-transform', !listsCollapsed && 'rotate-180')} />
          {listsCollapsed ? '展开全部列表' : '收起全部列表'}
        </button>
      </div>

      {/* ── Sector hotspot ─────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 gap-4">
        <SectorHotspot
          title="涨停集中板块"
          sectors={topUpSectors}
          allSectorStats={sectorStats}
          field="limit_up"
          stocks={limitUps}
          color={C_UP}
          isLoading={dataLoading}
          collapsed={listsCollapsed}
          onSelectSector={(n) => toggleMain(n, 'up')}
          expandedName={expMain?.side === 'up' ? expMain.name : null}
        />
        <SectorHotspot
          title="跌停集中板块"
          sectors={topDownSectors}
          allSectorStats={sectorStats}
          field="limit_down"
          stocks={limitDowns}
          color={C_DOWN}
          isLoading={dataLoading}
          collapsed={listsCollapsed}
          onSelectSector={(n) => toggleMain(n, 'down')}
          expandedName={expMain?.side === 'down' ? expMain.name : null}
        />
      </div>
      {renderExp(expMain, expMainStocks, () => setExpMain(null), showStrongMain, setShowStrongMain)}

      {/* ── 二板及以上集中板块（过滤首板，看持续强度）─────────────────────── */}
      <div className="flex items-baseline gap-2 pt-1">
        <span className="text-sm font-semibold text-text-primary">二板及以上集中板块</span>
        <span className="text-xs text-text-muted">过滤首板，反映涨停 / 跌停板块的持续强度</span>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <SectorHotspot
          title="涨停集中板块 · ≥2板"
          sectors={topUpSectors2}
          allSectorStats={sectorStats2}
          field="limit_up"
          stocks={upStocks2}
          color={C_UP}
          isLoading={dataLoading}
          collapsed={listsCollapsed}
          onSelectSector={(n) => toggle2(n, 'up')}
          expandedName={exp2?.side === 'up' ? exp2.name : null}
        />
        <SectorHotspot
          title="跌停集中板块 · ≥2板"
          sectors={topDownSectors2}
          allSectorStats={sectorStats2}
          field="limit_down"
          stocks={downStocks2}
          color={C_DOWN}
          isLoading={dataLoading}
          collapsed={listsCollapsed}
          onSelectSector={(n) => toggle2(n, 'down')}
          expandedName={exp2?.side === 'down' ? exp2.name : null}
        />
      </div>
      {renderExp(exp2, exp2Stocks, () => setExp2(null), showStrong2, setShowStrong2)}

      {/* ── 连板梯队（≥2连板，涨跌停都要）：资金强推方向一目了然 ────────────── */}
      <div className="flex items-baseline gap-2 pt-1">
        <span className="text-sm font-semibold text-text-primary">连板梯队</span>
        <span className="text-xs text-text-muted">≥2连板个股列表，涨停跌停均含，板数越高=资金推得越坚决</span>
      </div>
      <div className="space-y-4">
        <LadderTable
          title="涨停连板梯队"
          stocks={upLadder}
          badgeKind="board"
          boardKey="today_board_count"
          color={C_UP}
          isLoading={dataLoading}
          collapsed={listsCollapsed}
          onClickStock={(code) => navigate(`/stocks/${code}`)}
          onClickSector={(n) => toggleLadder(n, 'up')}
          activeSector={expLadder?.side === 'up' ? expLadder.name : null}
          emptyText="暂无 ≥2连板涨停股"
        />
        <LadderTable
          title="跌停连板梯队"
          stocks={downLadder}
          badgeKind="board"
          boardKey="today_limit_down_count"
          color={C_DOWN}
          isLoading={dataLoading}
          collapsed={listsCollapsed}
          onClickStock={(code) => navigate(`/stocks/${code}`)}
          onClickSector={(n) => toggleLadder(n, 'down')}
          activeSector={expLadder?.side === 'down' ? expLadder.name : null}
          emptyText="暂无 ≥2连板跌停股"
        />
      </div>
      {renderExp(expLadder, expLadderStocks, () => setExpLadder(null), showStrongLadder, setShowStrongLadder)}

      {/* ── 高弹性板块涨跌停（创业板/科创板±20%、北交所±30%，与主板±10%区分）── */}
      <div className="flex items-baseline gap-2 pt-1">
        <span className="text-sm font-semibold text-text-primary">高弹性板块涨跌停</span>
        <span className="text-xs text-text-muted">涨跌幅阈值 &gt;10% 的市场：创业板 / 科创板（±20%）、北交所（±30%）</span>
      </div>
      <div className="space-y-4">
        <LadderTable
          title="高弹性涨停"
          stocks={highElasticUp}
          badgeKind="market"
          color={C_UP}
          isLoading={dataLoading}
          collapsed={listsCollapsed}
          onClickStock={(code) => navigate(`/stocks/${code}`)}
          onClickSector={(n) => toggleElastic(n, 'up')}
          activeSector={expElastic?.side === 'up' ? expElastic.name : null}
          emptyText="暂无高弹性板块涨停股"
        />
        <LadderTable
          title="高弹性跌停"
          stocks={highElasticDown}
          badgeKind="market"
          color={C_DOWN}
          isLoading={dataLoading}
          collapsed={listsCollapsed}
          onClickStock={(code) => navigate(`/stocks/${code}`)}
          onClickSector={(n) => toggleElastic(n, 'down')}
          activeSector={expElastic?.side === 'down' ? expElastic.name : null}
          emptyText="暂无高弹性板块跌停股"
        />
      </div>
      {renderExp(expElastic, expElasticStocks, () => setExpElastic(null), showStrongElastic, setShowStrongElastic)}

    </div>
  )
}

// ─── 「涨跌停 + 强势股」开关（板块展开面板专用）──────────────────────────────

function StrongToggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      onClick={() => onChange(!on)}
      title={on ? '当前展示：涨跌停 + 该板块强势股' : '当前展示：仅涨跌停股票，点击叠加该板块强势股'}
      className={cn(
        'flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg border transition-colors shrink-0',
        on ? 'bg-accent/15 text-accent border-accent/40' : 'bg-bg-elevated text-text-muted border-bg-border hover:text-text-secondary',
      )}
    >
      <span className={cn('w-7 h-4 rounded-full relative shrink-0 transition-colors', on ? 'bg-accent' : 'bg-bg-border')}>
        <span className={cn(
          'absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white transition-transform',
          on && 'translate-x-3',
        )} />
      </span>
      {on ? '涨跌停 + 强势股' : '仅涨跌停'}
    </button>
  )
}

// ─── Sub-components ───────────────────────────────────────────────────────────

/** 可搜索板块多选器：选中后在走势图上叠加各板块每日涨停/跌停曲线（≤MAX_OVERLAY 个） */
function SectorPicker({ options, values, onToggle, onClear }: {
  options: SectorLimitTrendOption[]
  values: string[]
  onToggle: (name: string) => void
  onClear: () => void
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const filtered = useMemo(() => {
    const s = q.trim().toLowerCase()
    return s ? options.filter(o => o.name.toLowerCase().includes(s)) : options
  }, [options, q])
  const full = values.length >= MAX_OVERLAY

  return (
    <div className="relative flex items-center gap-1.5 flex-wrap">
      {/* 已选板块 chips */}
      {values.map(name => (
        <span
          key={name}
          className="flex items-center gap-1.5 px-2 py-1 rounded-lg border text-xs"
          style={{ borderColor: `${getSectorColor(name)}80`, color: getSectorColor(name), backgroundColor: `${getSectorColor(name)}14` }}
        >
          <span className="w-1.5 h-1.5 rounded-full" style={{ backgroundColor: getSectorColor(name) }} />
          {name}
          <X className="w-3 h-3 cursor-pointer opacity-70 hover:opacity-100" onClick={() => onToggle(name)} />
        </span>
      ))}
      {values.length > 1 && (
        <button
          onClick={onClear}
          className="text-[10px] px-1.5 py-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-elevated"
          title="清除全部叠加板块"
        >
          清空
        </button>
      )}
      <button
        onClick={() => { setOpen(o => !o); setQ('') }}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-bg-border text-xs text-text-muted hover:text-text-secondary hover:border-bg-border/80 transition-colors"
      >
        <Search className="w-3 h-3" />
        {values.length ? '继续叠加' : '叠加板块走势'}
        <ChevronDown className={cn('w-3 h-3 transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <>
          {/* 点击外部关闭 */}
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute top-full left-0 z-30 mt-1.5 w-72 card border border-bg-border/70 shadow-2xl overflow-hidden">
            <div className="p-2 border-b border-bg-border/40">
              <div className="flex items-center gap-1.5 bg-bg-elevated rounded-lg px-2 py-1.5">
                <Search className="w-3.5 h-3.5 text-text-muted shrink-0" />
                <input
                  autoFocus
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder="搜索板块…"
                  className="bg-transparent text-xs text-text-primary placeholder:text-text-muted focus:outline-none w-full"
                />
              </div>
            </div>
            <div className="max-h-64 overflow-y-auto">
              {filtered.length === 0 ? (
                <div className="py-6 text-center text-xs text-text-muted">无匹配板块</div>
              ) : (
                filtered.slice(0, 60).map((o) => {
                  const checked = values.includes(o.name)
                  const disabled = !checked && full
                  return (
                    <div
                      key={o.name}
                      onClick={() => { if (!disabled) onToggle(o.name) }}
                      className={cn(
                        'px-3 py-1.5 flex items-center gap-2 text-xs',
                        disabled ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer hover:bg-bg-elevated',
                        checked && 'bg-bg-elevated',
                      )}
                    >
                      <span
                        className={cn(
                          'w-3 h-3 rounded border shrink-0 flex items-center justify-center',
                          checked ? 'border-accent bg-accent/20 text-accent' : 'border-bg-border',
                        )}
                      >
                        {checked && <span className="text-[9px] leading-none">✓</span>}
                      </span>
                      <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ backgroundColor: getSectorColor(o.name) }} />
                      <span className="text-text-primary truncate">{o.name}</span>
                      <span className="ml-auto font-mono shrink-0 space-x-1.5">
                        {o.limit_up_total > 0 && <span className="text-up">涨{o.limit_up_total}</span>}
                        {o.limit_down_total > 0 && <span className="text-down">跌{o.limit_down_total}</span>}
                      </span>
                    </div>
                  )
                })
              )}
            </div>
            <div className="px-3 py-1.5 border-t border-bg-border/40 text-[10px] text-text-muted">
              近30个交易日出现过涨跌停的板块 · 按次数排序 · 最多叠加{MAX_OVERLAY}个
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function DomPanel({ title, hint, d5, d10, accent }: {
  title: string; hint: string; d5: [string, number][]; d10: [string, number][]; accent: 'up' | 'down'
}) {
  const Row = ({ label, items }: { label: string; items: [string, number][] }) => (
    <div className="flex items-start gap-2 py-1.5">
      <span className="text-xs text-text-muted shrink-0 w-10 pt-0.5">{label}</span>
      {items.length ? (
        <div className="flex flex-wrap gap-1.5">
          {items.map(([name, c]) => (
            <span key={name} className="inline-flex items-center gap-1">
              <SectorTag name={name} />
              <span className={cn('text-[10px] font-mono font-bold', accent === 'up' ? 'text-up' : 'text-down')}>×{c}</span>
            </span>
          ))}
        </div>
      ) : <span className="text-xs text-text-muted">—</span>}
    </div>
  )
  return (
    <div className="card p-4">
      <div className={cn('text-sm font-semibold mb-1', accent === 'up' ? 'text-up' : 'text-down')}>{title}</div>
      <p className="text-xs text-text-muted mb-2 leading-relaxed">{hint}</p>
      <div className="divide-y divide-bg-border/40">
        <Row label="近5日" items={d5} />
        <Row label="近10日" items={d10} />
      </div>
    </div>
  )
}

function StatCard({ icon, label, value, sub, color }: {
  icon: React.ReactNode; label: string; value: number | null; sub: string | null; color: 'up' | 'down'
}) {
  return (
    <div className={cn(
      'card flex items-center gap-3 px-4 py-3 border',
      color === 'up' ? 'bg-up/6 border-up/20' : 'bg-down/6 border-down/20',
    )}>
      <span className={cn('p-1.5 rounded-lg', color === 'up' ? 'bg-up/15 text-up' : 'bg-down/15 text-down')}>
        {icon}
      </span>
      <div>
        <div className="text-xs text-text-muted">{label}</div>
        <div className={cn('text-2xl font-bold font-mono leading-none mt-0.5', color === 'up' ? 'text-up' : 'text-down')}>
          {value === null ? '…' : value}
        </div>
        {sub && <div className="text-xs text-text-muted/70 mt-0.5">{sub}</div>}
      </div>
    </div>
  )
}

// ─── Sector hotspot (donut + ranked list + cross-sector analysis) ─────────────

function SectorHotspot({ title, sectors, allSectorStats, field, stocks, color, isLoading, onSelectSector, expandedName, collapsed }: {
  title: string
  sectors: SectorStat[]
  allSectorStats: SectorStat[]
  field: 'limit_up' | 'limit_down'
  stocks: Stock[]             // full list of today's up/down stocks
  color: string
  isLoading: boolean
  onSelectSector?: (name: string) => void
  expandedName?: string | null
  collapsed?: boolean          // 页面级统一收起/展开（单一开关控制全部股票列表卡片）
}) {
  // ── Cross-sector (overlap) stocks ─────────────────────────────────────────
  // A stock is "cross-sector" if it appears in 2+ of the top-N shown sectors
  const topSectorNames = useMemo(() => new Set(sectors.map(s => s.name)), [sectors])

  const overlapStocks = useMemo(() => (
    stocks.filter(s => {
      const matched = (s.sectors ?? []).filter(sec => topSectorNames.has(sec))
      return matched.length >= 2
    })
  ), [stocks, topSectorNames])

  // ── Pie data: top-8 sectors + "其他" remainder ────────────────────────────
  const pieData = useMemo(() => {
    const top8 = sectors.slice(0, 8)
    const rest  = sectors.slice(8)
    const data = top8.map(s => ({
      name:  s.name,
      value: s[field],
      fill:  getSectorColor(s.name),
    }))
    if (rest.length > 0) {
      data.push({
        name:  '其他',
        value: rest.reduce((sum, s) => sum + s[field], 0),
        fill:  C_OTHER,
      })
    }
    return data
  }, [sectors, field])

  const totalCount = stocks.length
  return (
    <div className="card overflow-hidden p-0">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div
        className="px-4 py-2.5 border-b border-bg-border/40 flex items-center gap-2 flex-wrap"
        style={{ backgroundColor: `${color}0d` }}
      >
        <span className="font-semibold text-sm" style={{ color }}>{title}</span>
        {!isLoading && (
          <>
            <span className="text-xs font-mono px-1.5 py-0.5 rounded" style={{ color, backgroundColor: `${color}18` }}>
              {sectors.length} 个板块
            </span>
            {overlapStocks.length > 0 && (
              <span
                className="text-xs font-mono px-1.5 py-0.5 rounded"
                style={{ color: '#8A90A8', backgroundColor: 'rgba(138,144,168,0.12)', border: '1px solid rgba(138,144,168,0.25)' }}
                title={`${overlapStocks.map(s => s.name).join('、')} 同属多个集中板块`}
              >
                {overlapStocks.length}只跨板块
              </span>
            )}
          </>
        )}
      </div>

      {collapsed ? null : isLoading ? (
        <div className="p-4"><LoadingRows /></div>
      ) : sectors.length === 0 ? (
        <div className="py-6 text-center text-text-muted text-sm">暂无数据</div>
      ) : (
        <>
          {/* ── Body: donut chart (left) + ranked list (right) ─────────── */}
          <div className="flex gap-0 min-h-0">

            {/* Donut chart */}
            <div className="flex-none flex flex-col items-center justify-center py-3 px-2 border-r border-bg-border/20"
              style={{ width: 130 }}>
              <div className="relative" style={{ width: 110, height: 110 }}>
                <PieChart width={110} height={110}>
                  <Pie
                    data={pieData}
                    cx="50%" cy="50%"
                    innerRadius={32} outerRadius={50}
                    paddingAngle={1.5}
                    dataKey="value"
                    strokeWidth={0}
                  >
                    {pieData.map((entry, i) => (
                      <Cell key={i} fill={entry.fill} fillOpacity={0.9} />
                    ))}
                  </Pie>
                  <Tooltip content={<PieCustomTooltip />} />
                </PieChart>
                {/* Center label */}
                <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
                  <span className="text-lg font-bold font-mono leading-none" style={{ color }}>
                    {totalCount}
                  </span>
                  <span className="text-[11px] text-text-muted/70 leading-tight">只</span>
                </div>
              </div>
              {/* Legend dots — top 5 */}
              <div className="mt-1 space-y-0.5 w-full px-1">
                {pieData.slice(0, 5).map((entry) => (
                  <div key={entry.name} className="flex items-center gap-1 min-w-0">
                    <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: entry.fill }} />
                    <span className="text-[11px] text-text-muted truncate leading-tight">{entry.name}</span>
                  </div>
                ))}
                {pieData.length > 5 && (
                  <div className="flex items-center gap-1">
                    <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: C_OTHER }} />
                    <span className="text-[11px] text-text-muted leading-tight">其他 {pieData.length - 5} 个</span>
                  </div>
                )}
              </div>
            </div>

            {/* Ranked list */}
            <div className="flex-1 min-w-0 divide-y divide-bg-border/20">
              {sectors.map((s, idx) => {
                const val      = s[field]
                const maxVal   = sectors[0]?.[field] ?? 1
                const pct      = (val / maxVal) * 100
                const maxBoard = field === 'limit_up' ? s.up_max_board : s.down_max_board
                return (
                  <div
                    key={s.name}
                    onClick={() => onSelectSector?.(s.name)}
                    className={cn(
                      'px-3 py-2 flex items-center gap-2.5 transition-colors',
                      onSelectSector && 'cursor-pointer hover:bg-bg-elevated',
                      expandedName === s.name && 'bg-bg-elevated ring-1 ring-inset ring-accent/40',
                    )}
                  >
                    <span className={cn(
                      'w-4 text-center text-xs font-mono shrink-0',
                      idx === 0 ? 'text-dragon font-bold' : 'text-text-muted/80',
                    )}>
                      {idx + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between mb-0.5">
                        <span className="text-sm text-text-primary truncate pr-1">{s.name}</span>
                        <span className="flex items-center gap-1.5 shrink-0 font-mono text-xs">
                          {field === 'limit_up'
                            ? <OneWordCountTag count={s.up_one_word} dir="up" />
                            : <OneWordCountTag count={s.down_one_word} dir="down" />}
                          {maxBoard > 0 && (
                            <span className="text-text-muted/80">最高{maxBoard}板</span>
                          )}
                          <span className="font-bold" style={{ color }}>{val}只</span>
                        </span>
                      </div>
                      <div className="h-1 rounded-full bg-bg-border/40 overflow-hidden">
                        <div
                          className="h-full rounded-full"
                          style={{ width: `${pct}%`, backgroundColor: `${color}90` }}
                        />
                      </div>
                    </div>
                    {/* Cross-side badge */}
                    {field === 'limit_up'   && s.limit_down > 0 && (
                      <span className="text-xs font-mono shrink-0 text-down/70">跌{s.limit_down}</span>
                    )}
                    {field === 'limit_down' && s.limit_up > 0 && (
                      <span className="text-xs font-mono shrink-0 text-up/70">涨{s.limit_up}</span>
                    )}
                  </div>
                )
              })}
            </div>
          </div>

          {/* ── Cross-sector stocks section ─────────────────────────────── */}
          {overlapStocks.length > 0 && (
            <div className="border-t border-bg-border/30 px-4 py-2.5">
              <div className="text-[12px] text-text-muted/85 uppercase tracking-wider mb-1.5">
                跨板块股票 · {overlapStocks.length} 只
              </div>
              <div className="flex flex-wrap gap-1.5">
                {overlapStocks.map(s => {
                  const secs = (s.sectors ?? []).filter(sec => topSectorNames.has(sec))
                  return (
                    <div
                      key={s.id}
                      className="flex items-center gap-1 text-xs bg-bg-elevated rounded px-2 py-0.5 border border-bg-border/40"
                      title={`板块：${secs.join('、')}`}
                    >
                      <span className="text-text-primary font-medium">{s.name}</span>
                      <span className="text-text-muted/85 font-mono text-[12px]">{s.code}</span>
                      <span
                        className="text-[11px] px-1 py-px rounded font-mono"
                        style={{ color, backgroundColor: `${color}18` }}
                      >
                        {secs.length}板块
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ─── Ladder table（连板梯队 / 高弹性板块涨跌停 · 通用个股表格，参考活跃股池样式）──

function PctCell({ v }: { v: number | null | undefined }) {
  if (v == null) return <span className="text-text-muted">—</span>
  return (
    <span className={cn('font-medium', v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-muted')}>
      {v > 0 ? '+' : ''}{v.toFixed(1)}%
    </span>
  )
}

function LadderTable({ title, stocks, badgeKind, boardKey, color, isLoading, onClickStock, onClickSector, activeSector, emptyText, collapsed }: {
  title: string
  stocks: Stock[]
  badgeKind: 'board' | 'market'
  boardKey?: 'today_board_count' | 'today_limit_down_count'
  color: string
  isLoading: boolean
  onClickStock: (code: string) => void
  onClickSector?: (name: string) => void
  activeSector?: string | null
  emptyText: string
  collapsed?: boolean          // 页面级统一收起/展开（单一开关控制全部股票列表卡片）
}) {
  const badgeFor = (s: Stock): string => {
    if (badgeKind === 'board' && boardKey) return `${(s[boardKey] ?? 0) as number}板`
    if (badgeKind === 'market') return marketTag(s.code) ?? '—'
    return '—'
  }
  return (
    <div className="card overflow-hidden p-0">
      <div
        className="px-4 py-2.5 border-b border-bg-border/40 flex items-center gap-2"
        style={{ backgroundColor: `${color}0d` }}
      >
        <span className="font-semibold text-sm" style={{ color }}>{title}</span>
        {!isLoading && (
          <span className="text-xs font-mono px-1.5 py-0.5 rounded" style={{ color, backgroundColor: `${color}18` }}>
            {stocks.length} 只
          </span>
        )}
      </div>
      {collapsed ? null : isLoading ? (
        <div className="p-4"><LoadingRows /></div>
      ) : stocks.length === 0 ? (
        <div className="py-6 text-center text-text-muted text-sm">{emptyText}</div>
      ) : (
        <div className="overflow-x-auto max-h-[360px] overflow-y-auto">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-bg-card">
              <tr className="border-b border-bg-border/40">
                <th className="text-left px-3 py-2 text-xs text-text-secondary/70 font-medium whitespace-nowrap">代码 / 名称</th>
                <th className="text-left px-3 py-2 text-xs text-text-secondary/70 font-medium">板块</th>
                <th className="px-3 py-2 text-xs text-text-secondary/70 font-medium text-right whitespace-nowrap">{badgeKind === 'board' ? '连板' : '市场'}</th>
                <th className="px-3 py-2 text-xs text-text-secondary/70 font-medium text-right whitespace-nowrap">10日涨幅</th>
                <th className="px-3 py-2 text-xs text-text-secondary/70 font-medium text-right whitespace-nowrap">20日涨幅</th>
                <th className="px-3 py-2 text-xs text-text-secondary/70 font-medium text-right whitespace-nowrap">60日涨幅</th>
                <th className="px-3 py-2 text-xs text-text-secondary/70 font-medium text-right whitespace-nowrap">龙头分</th>
                <th className="px-3 py-2 text-xs text-text-secondary/70 font-medium text-right whitespace-nowrap">风险分</th>
                <th className="px-3 py-2 text-xs text-text-secondary/70 font-medium text-right whitespace-nowrap">今日涨幅</th>
              </tr>
            </thead>
            <tbody>
              {stocks.map(s => (
                <tr
                  key={s.id}
                  onClick={() => onClickStock(s.code)}
                  className="border-b border-bg-border/25 hover:bg-bg-elevated cursor-pointer transition-colors last:border-0"
                >
                  <td className="px-3 py-2 whitespace-nowrap">
                    <div className="font-mono text-accent text-xs">{s.code}</div>
                    <div className="text-text-primary font-medium">{s.name}</div>
                  </td>
                  <td className="px-3 py-2 max-w-[240px]">
                    <div className="flex flex-wrap gap-1">
                      {(s.sectors ?? []).slice(0, 3).map(sec => (
                        <span
                          key={sec}
                          onClick={onClickSector ? (e) => { e.stopPropagation(); onClickSector(sec) } : undefined}
                          className={cn(
                            onClickSector && 'cursor-pointer transition-transform hover:scale-105',
                            activeSector === sec && 'ring-1 ring-accent rounded',
                          )}
                        >
                          <SectorTag name={sec} />
                        </span>
                      ))}
                      {(s.sectors ?? []).length === 0 && <span className="text-xs text-text-muted">—</span>}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    <span className="text-[11px] font-mono font-bold px-1.5 py-0.5 rounded" style={{ color, backgroundColor: `${color}18` }}>
                      {badgeFor(s)}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs"><PctCell v={s.pct_change_10d} /></td>
                  <td className="px-3 py-2 text-right font-mono text-xs"><PctCell v={s.pct_change_20d} /></td>
                  <td className="px-3 py-2 text-right font-mono text-xs"><PctCell v={s.pct_change_60d} /></td>
                  <td className="px-3 py-2 text-right font-mono text-xs text-text-secondary">{s.leader_score?.toFixed(0) ?? '—'}</td>
                  <td className="px-3 py-2 text-right font-mono text-xs">
                    <span className={cn(s.risk_score >= 50 ? 'text-down' : 'text-text-secondary')}>{s.risk_score?.toFixed(0) ?? '—'}</span>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-xs">
                    {s.today_pct_change != null ? (
                      <span className="font-bold" style={{ color: s.today_pct_change >= 0 ? C_UP : C_DOWN }}>
                        {s.today_pct_change >= 0 ? '+' : ''}{s.today_pct_change.toFixed(2)}%
                      </span>
                    ) : <span className="text-text-muted">—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
