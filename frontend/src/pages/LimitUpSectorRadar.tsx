/**
 * 涨停板块雷达（2026-08-25新增）。
 *
 * 这个页面不是"把涨停数据展示得更丰富"，而是从涨停现象里识别**资金是否正在一个
 * 板块形成集团进攻**，并确保真正的板块核心不会因为今天没涨停而从视野里消失。
 *
 * 所以：先看板块，展开才看个股；每张板块卡把"核心锚今日表现"和"今日涨停梯队"
 * 放在一起——这两块合起来才能区分「新老核心共振」和「老核心负反馈+低位补涨」，
 * 单看今日涨停数量这两种完全相反的结构长得一模一样。
 *
 * 所有分组/排序/召回逻辑都在后端（见 limit_up_radar_service.py），这里只负责展示，
 * 不重新计算哪只股票属于哪个板块——避免跟弱转强雷达/主线/活跃股池产生多套归属语义。
 */
import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { RefreshCw, ChevronDown, ChevronUp, ChevronsUpDown, ChevronRight, AlertTriangle, Flame } from 'lucide-react'
import { fetchLimitUpRadar, refreshLimitUpDetails, fetchRefreshStatus } from '@/api/limitUpRadar'
import type { SectorSortKey } from '@/api/limitUpRadar'
import { Badge } from '@/components/ui/badge'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import type {
  LimitUpRadarSector, LimitUpRadarTodayStock, LimitUpRadarCoreStock,
  LimitUpRadarBrokenStock, W2SCoreRole,
} from '@/types'

// 角色标签：只反映"被召回的原因"，不是强弱排名（Core Recall != Core Classification）
/**
 * 核心角色标签（2026-08-26 简化）。
 *
 * 原来全是四个字（当前核心/近期核心/历史核心/板块龙头/板块核心），一只票挂三个
 * 标签就得换行，把整行撑成两行高，几十行叠起来表格全乱。
 *
 * 简化的依据是**列头已经写着「核心角色」**，每个标签里再重复一遍"核心"是纯冗余。
 * 这三个角色本质上只是时间窗口的差别（近10日 / 近20日 / 只有60日窗口够得着），
 * 所以留下能区分窗口的那两个字就够。全称走 title 悬停，一个字没丢。
 */
const ROLE_LABEL: Record<W2SCoreRole, { text: string; full: string; variant: 'dragon' | 'accent' | 'warn' | 'muted' }> = {
  SECTOR_LEADER:   { text: '龙头', full: '板块龙头', variant: 'dragon' },
  SECTOR_CORE:     { text: '板块', full: '板块核心', variant: 'dragon' },
  CURRENT_CORE:    { text: '当前', full: '当前核心 —— 近10日仍在涨停', variant: 'accent' },
  RECENT_CORE:     { text: '近期', full: '近期核心 —— 近20日活跃或打出过高连板', variant: 'warn' },
  HISTORICAL_CORE: { text: '历史', full: '历史核心 —— 只有60日窗口才够得着，情绪锚', variant: 'muted' },
}

/**
 * 板块排序主键的展示名与说明。键名与次级键规则由后端 SECTOR_SORT_KEYS 定义，
 * 这里只做展示——排序逻辑不在前端重写一遍，否则两边迟早对不上。
 */
const DEFAULT_SECTOR_SORT: SectorSortKey = 'board_height'
const SECTOR_SORT_ORDER: SectorSortKey[] =
  ['board_height', 'broken_streak_height', 'continuation_count', 'today_limit_up_count']
const SECTOR_SORT_LABEL: Record<SectorSortKey, { name: string; tip: string }> = {
  board_height:         { name: '最高连板', tip: '现在还连着的高度，有资金正在接力；同高度看涨停只数' },
  broken_streak_height: { name: '最高断板', tip: '打过高板但断了，当前连板可能只有1却随时可能再起；同高度看涨停只数' },
  continuation_count:   { name: '连板个数', tip: '板块里还在连板的只数；同只数看涨停总数' },
  today_limit_up_count: { name: '涨停个数', tip: '横向一致性，今天同时开火的票有多少；同只数看最高连板' },
}

const fmtTime = (t: string | null) => (t ? t.slice(0, 5) : '—')

/** 封单额：null 是"东财没给"，必须显示 — 而不是 0.00亿 */
const fmtSeal = (v: number | null) => (v == null ? '—' : `${(v / 1e8).toFixed(2)}亿`)

const fmtPct = (v: number | null) => (v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(2)}%`)

/**
 * 「老核心正/负反馈」这个结论要不要给。
 *
 * 核心锚的今日涨跌幅来自当日 StockDailySnapshot，而快照只覆盖候选池里的股票
 * （强势池∪涨跌停∪成交额前列）——宽召回捞出来的核心锚大多不在候选池内，所以
 * 覆盖率天然偏低。生产实测出现过"21只核心锚里只有1只有当日数据、均值+5.42%"
 * 的情况：那 1 只代表不了整个板块老核心的状态，直接挂上"老核心正反馈"会把一个
 * 样本误读成板块级判断，而这恰恰是本页面最重要的那个结论。
 *
 * 所以要求至少3只、且覆盖到三成以上才给结论；不够就只给数字和覆盖率，让用户
 * 自己看下面的核心锚明细。宁可不下结论，不下一个站不住的结论。
 */
const coreVerdictTrustworthy = (s: LimitUpRadarSector) =>
  s.core_pct_known_count >= 3 && s.core_pct_known_count / Math.max(s.core_count, 1) >= 0.3

/** 涨停次数 10/20/60 三个独立单元格（拆开才能各自排序）。0 用暗色，避免一排0抢视线 */
function LuCells({ a, b, c }: { a: number | null; b: number | null; c: number | null }) {
  const cell = (v: number | null, i: number) => (
    <td key={i} className={cn('py-1.5 pr-2 text-right font-mono tabular-nums',
                              v ? 'text-warn' : 'text-text-muted/50')}>
      {v ?? '—'}
    </td>
  )
  return <>{[a, b, c].map(cell)}</>
}

/** 区间涨幅 10/20/60 三个独立单元格。东财真实复合区间收益，跟活跃股池的近似算法不同 */
function ChgCells({ a, b, c }: { a: number | null; b: number | null; c: number | null }) {
  const cell = (v: number | null, i: number) => (
    <td key={i} className={cn('py-1.5 pr-2 text-right font-mono tabular-nums text-xs', pctClass(v))}>
      {v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`}
    </td>
  )
  return <>{[a, b, c].map(cell)}</>
}

/** 龙头分/风险分：只在本轮真的算过时才显示，否则 — （见 scores_as_of_today） */
function ScoreCell({ v, fresh, tone }: { v: number | null; fresh: boolean; tone: 'dragon' | 'danger' }) {
  if (!fresh || v == null) {
    return <span className="text-text-muted/50" title="该股今日不在候选池，没有当日快照，分数是上次入池时的旧值，不展示">—</span>
  }
  return <span className={cn('font-mono tabular-nums', tone === 'dragon' ? 'text-dragon' : 'text-danger')}>{Math.round(v)}</span>
}

const pctClass = (v: number | null) =>
  v == null ? 'text-text-muted' : v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-secondary'

const fmtRefreshed = (iso: string | null) => {
  if (!iso) return '从未刷新'
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

// ─── 列头排序（沿用活跃股池 StockPool 的交互：首次点一律 desc，同列再点 desc↔asc，
//     排序状态在所有板块卡之间共享，点一次全部板块同步换序）────────────────────
type SortDir = 'desc' | 'asc'
interface SortState<K> { key: K | null; dir: SortDir }

function SortTh<K extends string>({ col, label, sort, onSort, align = 'right', title }: {
  col: K
  label: string
  sort: SortState<K>
  onSort: (k: K) => void
  align?: 'left' | 'right' | 'center'
  title?: string
}) {
  const active = sort.key === col
  return (
    <th
      onClick={() => onSort(col)}
      title={title}
      className={cn(
        'py-1.5 pr-3 font-normal cursor-pointer select-none group whitespace-nowrap',
        align === 'left' ? 'text-left' : align === 'center' ? 'text-center' : 'text-right',
        active ? 'text-accent' : 'text-text-muted hover:text-text-secondary',
      )}
    >
      <span className="inline-flex items-center gap-0.5">
        {label}
        {active
          ? (sort.dir === 'desc' ? <ChevronDown className="w-3 h-3 shrink-0" /> : <ChevronUp className="w-3 h-3 shrink-0" />)
          : <ChevronsUpDown className="w-3 h-3 shrink-0 opacity-0 group-hover:opacity-40 transition-opacity" />}
      </span>
    </th>
  )
}

/**
 * 按列排序。**缺失值永远排最后**，跟排序方向无关——升序时让一堆 — 冒到最前面
 * 毫无意义，而且会把"东财没给这个字段"看成"这只股票这项最低"。
 * 未选列时返回原顺序（后端已经按业务规则排好：连板→首封→终封→封单）。
 */
function applySort<T, K extends string>(rows: T[], sort: SortState<K>, pick: (r: T, k: K) => unknown): T[] {
  if (!sort.key) return rows
  const k = sort.key
  const sign = sort.dir === 'desc' ? -1 : 1
  return [...rows].sort((a, b) => {
    const va = pick(a, k), vb = pick(b, k)
    const na = va == null || va === '', nb = vb == null || vb === ''
    if (na && nb) return 0
    if (na) return 1
    if (nb) return -1
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * sign
    return String(va).localeCompare(String(vb)) * sign
  })
}

function useSort<K extends string>() {
  const [sort, setSort] = useState<SortState<K>>({ key: null, dir: 'desc' })
  const onSort = (k: K) =>
    setSort((p) => (p.key === k ? { key: k, dir: p.dir === 'desc' ? 'asc' : 'desc' } : { key: k, dir: 'desc' }))
  return { sort, onSort }
}

function BoardTag({ n }: { n: number | null }) {
  if (!n) return <span className="text-text-muted">—</span>
  return (
    <Badge variant={n >= 3 ? 'dragon' : n >= 2 ? 'up' : 'muted'}>
      {n === 1 ? '首板' : `${n}板`}
    </Badge>
  )
}

export default function LimitUpSectorRadar() {
  const qc = useQueryClient()
  const [includeCore, setIncludeCore] = useState(true)
  const [primaryOnly, setPrimaryOnly] = useState(false)
  // 板块排序主键，下拉选。每个键回答的是不同的问题，所以做成切换而不是加权合并
  // ——加权总分说不清"为什么这个板块排在前面"。
  // 次级键各不相同（见 SECTOR_SORT_LABEL 的 tip），由后端 SECTOR_SORT_KEYS 定义，
  // 前端只负责传键名，不在这里再写一遍排序规则。
  const [sectorSort, setSectorSort] = useState<SectorSortKey>(DEFAULT_SECTOR_SORT)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [refreshErr, setRefreshErr] = useState<string | null>(null)
  const coreSort = useSort<CoreSortKey>()
  const todaySort = useSort<TodaySortKey>()
  const brokenSort = useSort<BrokenSortKey>()

  const params = {
    include_core: includeCore,
    group_mode: primaryOnly ? ('primary' as const) : ('all_watched_sectors' as const),
    sector_sort: sectorSort,
  }
  const { data, isLoading } = useQuery({
    queryKey: ['limit-up-radar', params],
    queryFn: () => fetchLimitUpRadar(params),
  })

  // 刷新是后台任务（约40秒），POST 只负责启动，之后轮询状态直到跑完再刷新数据。
  // 这不违反"不做自动轮询"——那条约束针对的是"页面自己定时打外部接口"，这里是
  // 用户主动点击后为了拿到这一次的结果而轮询本地状态，拿到就停。
  const [busy, setBusy] = useState(false)
  const [step, setStep] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const stopPolling = () => {
    if (pollRef.current) { window.clearInterval(pollRef.current); pollRef.current = null }
  }
  useEffect(() => stopPolling, [])

  const startPolling = () => {
    stopPolling()
    pollRef.current = window.setInterval(async () => {
      try {
        const st = await fetchRefreshStatus()
        setStep(st.step)
        if (!st.running) {
          stopPolling(); setBusy(false); setStep(null)
          setRefreshErr(st.ok ? null : st.error || '刷新失败')
          if (st.ok) qc.invalidateQueries({ queryKey: ['limit-up-radar'] })
        }
      } catch {
        // 单次状态查询失败不终止轮询，下一拍再试；真正的失败会由后端状态给出
      }
    }, 1500)
  }

  const refresh = useMutation({
    mutationFn: () => refreshLimitUpDetails(),
    onSuccess: (res) => {
      if (!res.ok) { setRefreshErr(res.error || '刷新失败'); return }
      setBusy(true); setStep(res.step ?? '启动中'); startPolling()
    },
    onError: (e: Error) => { setBusy(false); setRefreshErr(e.message) },
  })

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const s = data?.summary

  return (
    <div className="space-y-4">
      {/* ── 顶部：事实摘要 + 数据新鲜度 ───────────────────────────────────── */}
      <div className="card p-4">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div>
            <div className="flex items-center gap-2">
              <Flame className="w-5 h-5 text-danger" />
              <h1 className="text-lg font-bold text-text-primary">涨停板块雷达</h1>
              <span className="text-sm text-text-muted font-mono">{data?.trade_date || '—'}</span>
            </div>
            <p className="text-xs text-text-muted mt-1">
              资金今天在哪些板块形成集团进攻 · 板块核心是谁 · 老核心与新涨停是否共振
            </p>
          </div>

          <div className="flex items-center gap-3 flex-wrap">
            <label className="flex items-center gap-1.5 text-xs text-text-secondary cursor-pointer">
              <input type="checkbox" checked={includeCore} className="accent-accent"
                     onChange={(e) => setIncludeCore(e.target.checked)} />
              补全板块核心
            </label>
            <label className="flex items-center gap-1.5 text-xs text-text-secondary cursor-pointer"
                   title="默认一只股票可出现在多个关注板块（优先不漏核心）；勾选后只按主板块归组去重">
              <input type="checkbox" checked={primaryOnly} className="accent-accent"
                     onChange={(e) => setPrimaryOnly(e.target.checked)} />
              仅主板块
            </label>
            {/* 排序：下拉直选（2026-08-27 用户改的，原来是点击四态循环）。
                沿用仓库里已有的 select 写法（见 LimitMovesDashboard 的日期选择），
                不另造一套下拉。非默认值时边框和文字变强调色，一眼能看出"现在不是
                默认排序"——否则切过之后过一会儿就忘了自己切过。 */}
            <label className="flex items-center gap-1.5 text-xs text-text-secondary whitespace-nowrap">
              排序
              <select
                value={sectorSort}
                onChange={(e) => setSectorSort(e.target.value as SectorSortKey)}
                title={SECTOR_SORT_LABEL[sectorSort].tip}
                className={cn('bg-bg-card border rounded-lg px-2 py-1 text-xs focus:outline-none cursor-pointer',
                  sectorSort === DEFAULT_SECTOR_SORT
                    ? 'border-bg-border text-text-primary'
                    : 'border-accent/50 text-accent')}
              >
                {SECTOR_SORT_ORDER.map((k) => (
                  <option key={k} value={k} title={SECTOR_SORT_LABEL[k].tip}>
                    {SECTOR_SORT_LABEL[k].name}
                  </option>
                ))}
              </select>
            </label>
            <button
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending || busy}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm bg-accent-dim text-accent hover:bg-accent/20 transition-colors disabled:opacity-60"
              title="拉取涨停池/炸板池/涨停原因/核心召回，并为本页股票重算龙头分与风险分。约30-40秒。"
            >
              <RefreshCw className={cn('w-3.5 h-3.5', (refresh.isPending || busy) && 'animate-spin')} />
              {busy ? (step || '刷新中…') : '刷新涨停数据'}
            </button>
          </div>
        </div>

        {/* 事实摘要 */}
        <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-7 gap-3 mt-4">
          <Stat label="涨停" value={s?.limit_up_count ?? '—'} tone="up" />
          <Stat label="连板" value={s?.continuation_count ?? '—'} tone="dragon" />
          <Stat label="首板" value={s?.first_board_count ?? '—'} />
          <Stat label="最高板" value={s?.board_height ? `${s.board_height}板` : '—'} tone="dragon" />
          <Stat label="炸板" value={s?.broken_count ?? '—'} tone="down" />
          <Stat label="封板率" value={s?.seal_rate != null ? `${s.seal_rate}%` : '—'} />
          <Stat label="活跃板块" value={s?.active_sector_count ?? '—'} tone="accent" />
        </div>

        {/* 数据新鲜度：盘中手动刷新的页面必须让用户知道这份数据是什么时候抓的 */}
        <div className="flex items-center gap-3 mt-3 pt-3 border-t border-bg-border text-xs flex-wrap">
          <span className="text-text-muted">
            数据更新：<span className="font-mono text-text-secondary">{fmtRefreshed(data?.refreshed_at ?? null)}</span>
          </span>
          <span className="text-text-muted">来源：{data?.source === 'eastmoney' ? '东方财富' : '—'}</span>
          {/* 涨停明细和历史窗口是两份不同新鲜度的数据，必须分开显示：前者手动刷新
              就能更新，后者要等「每日数据更新」跑完 */}
          <span className="text-text-muted">
            历史窗口算至：
            <span className="font-mono text-text-secondary">{data?.history_as_of || '—'}</span>
            {!!data?.history_lag_days && data.history_lag_days >= 2 && (
              <span className="text-warn"> （落后{data.history_lag_days}个交易日）</span>
            )}
          </span>
          {/* 门槛和被隐藏的板块数必须显示出来——用户得能看出是不是把想看的板块
              也滤掉了，而不是以为"今天就这几个板块" */}
          {!!data && (
            <span className="text-text-muted">
              <span title={'两条任一满足即展示：\n' +
                           `· 涨停≥${data.filter_min_limit_up} 且 最高≥${data.filter_min_board_height}板 —— 已经走出高度的主线\n` +
                           `· 涨停≥${data.filter_min_limit_up_alone} —— 今天同时开火的票够多，横向一致性强但还没分出龙头`}>
                板块门槛：涨停≥{data.filter_min_limit_up} 且 最高≥{data.filter_min_board_height}板
                {data.filter_min_limit_up_alone > 0 && `，或涨停≥${data.filter_min_limit_up_alone}`}
              </span>
              {data.hidden_sector_count > 0 && (
                <span className="text-text-muted/70">（已隐藏 {data.hidden_sector_count} 个不达标板块）</span>
              )}
            </span>
          )}
          <span className="text-text-muted/70">不自动刷新，需手动点击</span>
          {refreshErr && (
            <span className="flex items-center gap-1 text-danger">
              <AlertTriangle className="w-3.5 h-3.5" />
              刷新失败（{refreshErr}），下方仍为上次成功的数据
            </span>
          )}
        </div>

        {/* 历史窗口过期是会让"近N日涨停次数"整体答非所问的问题，不能只做一行淡色
            提示——给足视觉重量，并直接告诉用户要做什么 */}
        {data?.warnings?.map((w) => (
          <div key={w}
               className="mt-2 px-3 py-2 rounded bg-warn-dim border border-warn/30 text-xs text-warn flex items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-px" />
            <span>{w}</span>
          </div>
        ))}
      </div>

      {/* ── 板块卡 ────────────────────────────────────────────────────────── */}
      {isLoading ? (
        <div className="card p-4"><LoadingRows rows={6} /></div>
      ) : !data?.sectors.length ? (
        <div className="card p-8 text-center text-text-muted text-sm">
          {data?.hidden_sector_count
            ? `没有板块满足「涨停≥${data.filter_min_limit_up} 且 最高≥${data.filter_min_board_height}板」`
              + (data.filter_min_limit_up_alone > 0 ? `，也没有板块涨停≥${data.filter_min_limit_up_alone}` : '')
              + `（${data.hidden_sector_count} 个板块因不达标被隐藏）`
            : (data?.trade_date ? `${data.trade_date} 没有涨停板块数据` : '暂无数据')}
          <div className="mt-2 text-xs">
            {data?.hidden_sector_count
              ? '说明今天没有形成有高度的板块进攻——这本身就是一个市场判断，不是数据缺失'
              : '点击右上角「刷新涨停数据」从东方财富拉取当日涨停明细'}
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          {data.sectors.map((sec) => (
            <SectorCard
              key={sec.sector_id}
              sector={sec}
              open={expanded.has(sec.sector_id)}
              onToggle={() => toggle(sec.sector_id)}
              coreSort={coreSort}
              todaySort={todaySort}
              brokenSort={brokenSort}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function Stat({ label, value, tone }: {
  label: string; value: React.ReactNode; tone?: 'up' | 'down' | 'dragon' | 'accent'
}) {
  const toneCls = tone === 'up' ? 'text-up' : tone === 'down' ? 'text-down'
    : tone === 'dragon' ? 'text-dragon' : tone === 'accent' ? 'text-accent' : 'text-text-primary'
  return (
    <div>
      <div className="text-xs text-text-muted">{label}</div>
      <div className={cn('text-lg font-bold font-mono leading-tight', toneCls)}>{value}</div>
    </div>
  )
}

type CoreSortKey = 'pct_change' | 'lu10' | 'lu20' | 'lu60' | 'board' | 'ic10' | 'ic20' | 'ic60' | 'leader' | 'risk'
type BrokenSortKey = 'board' | 'board60' | 'gap' | 'broken' | 'pct_change' | 'first'
  | 'turnover' | 'amount' | 'amp' | 'lu10' | 'lu20' | 'lu60' | 'ic10' | 'ic20' | 'ic60'
  | 'leader' | 'risk'
type TodaySortKey = 'pct_change' | 'board' | 'board60' | 'first' | 'last' | 'seal' | 'broken' | 'lu10' | 'lu20' | 'lu60'
  | 'ic10' | 'ic20' | 'ic60' | 'leader' | 'risk'

type SortCtl<K extends string> = { sort: SortState<K>; onSort: (k: K) => void }

function SectorCard({ sector, open, onToggle, coreSort, todaySort, brokenSort }: {
  sector: LimitUpRadarSector; open: boolean; onToggle: () => void
  coreSort: SortCtl<CoreSortKey>; todaySort: SortCtl<TodaySortKey>
  brokenSort: SortCtl<BrokenSortKey>
}) {
  const ladder = sector.board_ladder
    .map((e) => `${e.board === 1 ? '首板' : `${e.board}板`}×${e.count}`)
    .join(' ｜ ')

  return (
    <div className="card overflow-hidden p-0">
      {/* 板块头：一行看完这个板块今天的攻击强度 */}
      <button onClick={onToggle} className="w-full text-left px-4 py-3 hover:bg-bg-elevated/50 transition-colors">
        <div className="flex items-start gap-3">
          {open ? <ChevronDown className="w-4 h-4 mt-1 shrink-0 text-text-muted" />
                : <ChevronRight className="w-4 h-4 mt-1 shrink-0 text-text-muted" />}
          <div className="flex-1 min-w-0">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-base font-bold text-text-primary">{sector.sector_name}</span>
              <Badge variant="up">涨停 {sector.today_limit_up_count}</Badge>
              {sector.continuation_count > 0 && <Badge variant="dragon">连板 {sector.continuation_count}</Badge>}
              {sector.board_height > 0 && <Badge variant="dragon">最高 {sector.board_height}板</Badge>}
              {/* 断板最高：打过高板但streak断了的票。跟"最高连板"是两种不同的强——
                  神奇制药当前首板、在"最高3板"的板块里毫不起眼，但它历史上是 11日7板，
                  市场辨识度跟一个真首板完全不是一回事，只看连板会把它埋掉。 */}
              {sector.broken_streak_height != null && (
                <span title="板块内断板股的最高累计板数（东财口径 N天M板，N>M 才算断板；3日3板是连着的不算）。这类票当前连板可能很低，但打过高板、市场辨识度还在">
                  <Badge variant="warn">断板最高 {sector.broken_streak_height}板</Badge>
                </span>
              )}
            </div>

            <div className="flex items-center gap-x-4 gap-y-1 mt-1.5 text-xs text-text-secondary flex-wrap font-mono">
              <span>首封最早 {fmtTime(sector.earliest_limit_time)}</span>
              <span>封单合计 {fmtSeal(sector.total_seal_amount)}</span>
              <span>封板率 {sector.seal_rate != null ? `${sector.seal_rate}%` : '—'}</span>
              <span>炸板 {sector.broken_count}</span>
              {ladder && <span className="text-text-muted">梯队：{ladder}</span>}
            </div>

            {/* 老核心今日表现——跟今日涨停梯队并排看，才能区分共振和补涨 */}
            {sector.core_count > 0 && (
              <div className="mt-1.5 text-xs flex items-center gap-2 flex-wrap">
                <span className="text-text-muted">核心锚 {sector.core_count} 只 · 今日均值</span>
                {sector.core_pct_known_count === 0 ? (
                  // 核心锚涨跌幅来自当日快照，daily_update 跑完前不存在。这里必须
                  // 说清楚是"还没有数据"，显示成 0.00% 或 — 会被误读成"核心走平"
                  <span className="text-text-muted" title="核心锚今日涨跌幅取自当日快照，需等当天『每日数据更新』跑完才有">
                    待当日数据更新
                  </span>
                ) : (
                  <>
                    <span className={cn('font-mono font-bold', pctClass(sector.core_avg_pct_change))}>
                      {fmtPct(sector.core_avg_pct_change)}
                    </span>
                    {sector.core_avg_pct_change != null && (
                      coreVerdictTrustworthy(sector) ? (
                        <span className={sector.core_avg_pct_change > 0 ? 'text-up' : 'text-down'}>
                          {sector.core_avg_pct_change > 0 ? '老核心正反馈' : '老核心负反馈'}
                        </span>
                      ) : (
                        // 覆盖率太低时只给数字、不给"正/负反馈"结论：21只核心锚里只有1只
                        // 有当日数据时，那1只的涨跌幅代表不了整个板块老核心的状态，
                        // 挂上结论会把一个样本误读成板块判断
                        <span className="text-warn" title="核心锚今日涨跌幅来自当日快照，多数核心锚不在候选池内因而没有快照；样本太少时不给正/负反馈结论">
                          样本不足，不作判断
                        </span>
                      )
                    )}
                    {sector.core_pct_known_count < sector.core_count && (
                      <span className="text-text-muted/70">
                        （{sector.core_pct_known_count}/{sector.core_count} 只有当日数据）
                      </span>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        </div>
      </button>

      {open && (
        <div className="border-t border-bg-border px-4 py-3 space-y-4">
          {sector.core_stocks.length > 0 && (
            <section>
              <h3 className="text-xs font-semibold text-text-secondary mb-2">
                核心锚 <span className="font-normal text-text-muted">（今日未涨停，按历史市场辨识度召回，不代表当前强弱）</span>
                {sector.core_shown_count < sector.core_count && (
                  <span className="font-normal text-text-muted/70">
                    {' '}· 显示前 {sector.core_shown_count} / 共 {sector.core_count} 只
                  </span>
                )}
              </h3>
              <CoreTable rows={sector.core_stocks} ctl={coreSort} />
            </section>
          )}

          <section>
            <h3 className="text-xs font-semibold text-text-secondary mb-2">
              今日涨停 <span className="font-normal text-text-muted">（{sector.today_limit_up_count} 只）</span>
            </h3>
            <TodayTable rows={sector.today_limit_up_stocks} ctl={todaySort} />
          </section>

          {sector.broken_stocks.length > 0 && (
            <section>
              <h3 className="text-xs font-semibold text-text-secondary mb-2">
                今日炸板{' '}
                <span className="font-normal text-text-muted">
                  （{sector.broken_count} 只 · 盘中触及涨停但收盘没封住，看封板有多不坚决）
                </span>
              </h3>
              <BrokenTable rows={sector.broken_stocks} ctl={brokenSort} />
            </section>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * 角色标签组。**不换行**（2026-08-26）：一只票最多挂 3 个标签，原来 flex-wrap
 * 会把行撑成两行高，同一张表里高矮不齐，扫读时眼睛要不停重新对齐。
 * 简化标签后 3 个也放得下；万一还是超宽，宁可横向溢出被列宽裁掉，也不撑高行。
 */
function RoleTags({ roles, reasons }: { roles: W2SCoreRole[]; reasons: string[] }) {
  if (!roles.length) return null
  const full = roles.map((r) => ROLE_LABEL[r]?.full).filter(Boolean).join(' · ')
  return (
    <span className="inline-flex items-center gap-1 flex-nowrap whitespace-nowrap overflow-hidden"
          title={[full, reasons.join(' · ')].filter(Boolean).join('\n')}>
      {roles.map((r) => {
        const cfg = ROLE_LABEL[r]
        return cfg ? <Badge key={r} variant={cfg.variant}>{cfg.text}</Badge> : null
      })}
    </span>
  )
}

const CORE_PICK = (r: LimitUpRadarCoreStock, k: CoreSortKey) => ({
  pct_change: r.pct_change, lu10: r.limit_up_days_10d, lu20: r.limit_up_days_20d,
  lu60: r.limit_up_days_60d, board: r.board_count_60d,
  ic10: r.interval_chg_10d, ic20: r.interval_chg_20d, ic60: r.interval_chg_60d,
  leader: r.leader_score, risk: r.risk_score,
}[k])

/**
 * 核心锚与今日攻击共用的列网格（2026-08-26）。
 *
 * 这两张表是上下紧挨着放的，对照扫读是它们唯一的用法，所以同名列必须左右对齐。
 * 各自独立的 <table className="w-full"> 做不到这点——列宽按各自内容自动算，
 * 「10日涨停」这种两边都有的列永远错开一截。改成 table-fixed + 同一份 colgroup。
 *
 * 列序按"两边都有的在前，只有今日涨停才有的在后"排（用户 2026-08-26 定）：
 *   1-13  股票/核心角色/今日/涨停次数×3/60日高板/区间涨幅×3/龙头分/风险分/说明 ← 三表共有
 *   14-16 板位/首封/炸板          ← 今日涨停 + 今日炸板都有
 *   17,18 终封/封单               ← 只有今日涨停有（炸板没封住，本来就没有）
 *   19-22 距涨停/振幅/换手/成交额  ← 只有今日炸板有
 * 共有的三列排最前，是为了让两张表的空白都尽量靠后：今日涨停的空白全在 19-22 的
 * 尾部（看不见），今日炸板只剩 17/18 两列。全部列都给固定宽，不留自适应列——
 * 留一个的话它会把整行剩余空间全吸走，那正是"换手到成交额之间空一大片"的成因。
 * 说明列（召回理由/涨停原因）**紧挨风险分**，不隔着一片空白——核心锚原来把它甩到
 * 第18列，中间空 5 列，读一行要横跨半个屏幕。现在核心锚的空白全在最右边，
 * 视觉上等于不存在。
 *
 * 核心角色排在股票之后：它跟股票一样是"这一行是谁"的身份信息，不是指标。
 * 全部列都给固定宽度，容器更宽时浏览器按比例均摊多余空间——两表规则相同，
 * 所以窗口怎么变都对齐。
 *
 * 宽度按 **12px 字号（text-xs）** 量的：1个汉字12px、等宽数字约7px。第一版是按
 * 16px 估的，结果每列都宽出三成——股票列给了 13rem(208px) 而内容只有约 100px，
 * 白扔一半，一路把后面的列挤出视野，用户要横向拖动才看得到板位/首封/封单
 * （2026-08-27 反馈）。合计从 106rem 收到 81rem，少了约 400px。
 */
function RadarCols() {
  return (
    <colgroup>
      <col className="w-[7.4rem]" />{/* 股票：名称4字48px + 代码6位42px + 间距 ≈ 100px */}
      <col className="w-[6rem]" />{/* 核心角色：3个两字徽章 ≈ 116px */}
      <col className="w-[3.8rem]" />{/* 今日 */}
      <col className="w-[3.8rem]" />{/* 10日涨停：宽度由表头4字+排序箭头决定，不是数据 */}
      <col className="w-[3.8rem]" />{/* 20日涨停 */}
      <col className="w-[3.8rem]" />{/* 60日涨停 */}
      <col className="w-[3.8rem]" />{/* 60日高板 */}
      <col className="w-[4rem]" />{/* 10日涨幅：+120.3% 共7字符 */}
      <col className="w-[4rem]" />{/* 20日涨幅 */}
      <col className="w-[4rem]" />{/* 60日涨幅 */}
      <col className="w-[3.2rem]" />{/* 龙头分 */}
      <col className="w-[3.2rem]" />{/* 风险分 */}
      <col className="w-[3.8rem]" />{/* 说明：截断到6字 ≈ 72px */}
      <col className="w-[4.4rem]" />{/* 板位 —— 14-16 今日涨停+今日炸板都有 */}
      <col className="w-[3.2rem]" />{/* 首封：10:00 */}
      <col className="w-[3rem]" />{/* 炸板：紧跟首封（2026-08-27用户提出）。原来夹在
                                       终封/封单之后，今日炸板那张表就在首封和炸板
                                       之间空了两列；挪过来之后空白只剩 17/18，而且
                                       "首封→炸板几次→终封"读起来正好是一条时间线 */}
      <col className="w-[3.2rem]" />{/* 终封 —— 17/18 只有今日涨停有 */}
      <col className="w-[3.4rem]" />{/* 封单：0.61亿 */}
      <col className="w-[3.6rem]" />{/* 距涨停 —— 19-22 只有今日炸板有 */}
      <col className="w-[3rem]" />{/* 振幅 */}
      <col className="w-[3rem]" />{/* 换手 */}
      <col className="w-[3.8rem]" />{/* 成交额：给固定宽。原来是唯一的自适应列，
                                        table-fixed 下会把整行剩余空间全吸走，
                                        今日炸板的换手和成交额之间因此空一大片 */}
    </colgroup>
  )
}

/** 占位空列：这张表没有这个字段。留空而不是塞 —，"不适用"和"没数据"不是一回事。 */
const Pad = ({ n }: { n: number }) => (
  <>{Array.from({ length: n }, (_, i) => <td key={i} className="py-1.5 pr-3" />)}</>
)
const PadTh = ({ n }: { n: number }) => (
  <>{Array.from({ length: n }, (_, i) => <th key={i} className="py-1.5 pr-3" />)}</>
)

/**
 * 说明列（核心锚=召回理由，今日攻击=涨停原因）：长度完全不可控，"业绩增长+矿山
 * 服务合同+哥伦比亚铜金矿+EIA获批+产量指引"这种能把整行撑成三行高，几十行叠起来
 * 表格就散了。截到 6 个字符 + 省略号，全文走 title 悬停——信息一个字没少，
 * 只是不再抢版面。
 */
function NoteCell({ text, full }: { text: string | null; full?: string | null }) {
  if (!text) return <td className="py-1.5 pr-2 text-text-muted">—</td>
  return (
    <td className="py-1.5 pr-2 text-text-muted whitespace-nowrap overflow-hidden cursor-help"
        title={full || text}>
      {text.length > 6 ? `${text.slice(0, 6)}…` : text}
    </td>
  )
}

function CoreTable({ rows, ctl }: { rows: LimitUpRadarCoreStock[]; ctl: SortCtl<CoreSortKey> }) {
  const sorted = applySort(rows, ctl.sort, CORE_PICK)
  return (
    <div className="overflow-x-auto">
      <table className="w-full table-fixed text-xs">
        <RadarCols />
        <thead>
          <tr className="text-text-muted border-b border-bg-border">
            <th className="text-left font-normal py-1.5 pr-3">股票</th>
            <th className="text-left font-normal py-1.5 pr-3">核心角色</th>
            <SortTh col="pct_change" label="今日" {...ctl} />
            <SortTh col="lu10" label="10日涨停" {...ctl} title="东财口径：当日曾触及涨停，含炸板" />
            <SortTh col="lu20" label="20日涨停" {...ctl} title="东财口径：当日曾触及涨停，含炸板" />
            <SortTh col="lu60" label="60日涨停" {...ctl} title="东财口径：当日曾触及涨停，含炸板" />
            <SortTh col="board" label="60日高板" {...ctl} />
            <SortTh col="ic10" label="10日涨幅" {...ctl} title="真实复合区间收益，与活跃股池的近似算法不同" />
            <SortTh col="ic20" label="20日涨幅" {...ctl} title="真实复合区间收益，与活跃股池的近似算法不同" />
            <SortTh col="ic60" label="60日涨幅" {...ctl} title="真实复合区间收益，与活跃股池的近似算法不同" />
            <SortTh col="leader" label="龙头分" {...ctl} title="刷新按钮会为本页股票现算；—表示本轮没算过，不拿旧值冒充" />
            <SortTh col="risk" label="风险分" {...ctl} title="刷新按钮会为本页股票现算；—表示本轮没算过，不拿旧值冒充" />
            <th className="text-left font-normal py-1.5 pr-3">召回理由</th>
            <PadTh n={9} />{/* 14-22：板位/首封/终封/封单/炸板/距涨停/振幅/换手/成交额
                                核心锚今日未涨停，这些一个都不适用 */}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.code} className="border-b border-bg-border/40 last:border-0 align-top">
              <td className="py-1.5 pr-3 whitespace-nowrap">
                <Link to={`/stocks/${r.code}`} className="text-text-primary hover:text-accent">
                  <span className="font-medium">{r.name}</span>
                  <span className="ml-1.5 font-mono text-text-muted">{r.code}</span>
                </Link>
                {r.is_broken_today && <Badge variant="down" className="ml-1.5">炸板</Badge>}
              </td>
              <td className="py-1.5 pr-3"><RoleTags roles={r.core_roles} reasons={r.core_reasons} /></td>
              <td className={cn('py-1.5 pr-2 text-right font-mono font-bold whitespace-nowrap', pctClass(r.pct_change))}>
                {fmtPct(r.pct_change)}
              </td>
              <LuCells a={r.limit_up_days_10d} b={r.limit_up_days_20d} c={r.limit_up_days_60d} />
              <td className="py-1.5 pr-2 text-right font-mono text-dragon tabular-nums">{r.board_count_60d || '—'}</td>
              <ChgCells a={r.interval_chg_10d} b={r.interval_chg_20d} c={r.interval_chg_60d} />
              <td className="py-1.5 pr-3 text-right"><ScoreCell v={r.leader_score} fresh={r.scores_as_of_today} tone="dragon" /></td>
              <td className="py-1.5 pr-3 text-right"><ScoreCell v={r.risk_score} fresh={r.scores_as_of_today} tone="danger" /></td>
              <NoteCell text={r.core_reasons.join(' · ') || null} />
              <Pad n={9} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const TODAY_PICK = (r: LimitUpRadarTodayStock, k: TodaySortKey) => ({
  pct_change: r.pct_change, board: r.board_count, board60: r.board_count_60d,
  first: r.first_limit_time, last: r.last_limit_time,
  seal: r.seal_amount, broken: r.broken_times,
  lu10: r.limit_up_days_10d, lu20: r.limit_up_days_20d, lu60: r.limit_up_days_60d,
  ic10: r.interval_chg_10d, ic20: r.interval_chg_20d, ic60: r.interval_chg_60d,
  leader: r.leader_score, risk: r.risk_score,
}[k])

function TodayTable({ rows, ctl }: { rows: LimitUpRadarTodayStock[]; ctl: SortCtl<TodaySortKey> }) {
  const sorted = applySort(rows, ctl.sort, TODAY_PICK)
  return (
    <div className="overflow-x-auto">
      <table className="w-full table-fixed text-xs">
        <RadarCols />
        <thead>
          <tr className="text-text-muted border-b border-bg-border">
            <th className="text-left font-normal py-1.5 pr-3">股票</th>
            <th className="text-left font-normal py-1.5 pr-3">核心角色</th>
            <SortTh col="pct_change" label="今日" {...ctl}
                    title="当日涨跌幅。涨停股不都是+10%——创业板/科创板20%、北交所30%，低价股涨停价四舍五入后还会略超" />
            <SortTh col="lu10" label="10日涨停" {...ctl} title="东财口径：当日曾触及涨停，含炸板" />
            <SortTh col="lu20" label="20日涨停" {...ctl} title="东财口径：当日曾触及涨停，含炸板" />
            <SortTh col="lu60" label="60日涨停" {...ctl} title="东财口径：当日曾触及涨停，含炸板" />
            <SortTh col="board60" label="60日高板" {...ctl} />
            <SortTh col="ic10" label="10日涨幅" {...ctl} title="真实复合区间收益" />
            <SortTh col="ic20" label="20日涨幅" {...ctl} title="真实复合区间收益" />
            <SortTh col="ic60" label="60日涨幅" {...ctl} title="真实复合区间收益" />
            <SortTh col="leader" label="龙头分" {...ctl} />
            <SortTh col="risk" label="风险分" {...ctl} />
            <th className="text-left font-normal py-1.5 pr-3" title="涨停原因（催化剂，非板块归属）">涨停原因</th>
            <SortTh col="board" label="板位" {...ctl} align="left" />
            <SortTh col="first" label="首封" {...ctl} title="首次封板时间" />
            <SortTh col="broken" label="炸板" {...ctl} title="当日开板次数。首封→炸板几次→终封，正好是一条时间线" />
            <SortTh col="last" label="终封" {...ctl} title="最终封板时间；与首封不同说明中途开过板" />
            <SortTh col="seal" label="封单" {...ctl} title="封单额；— 表示东方财富未提供该字段，不是0" />
            <PadTh n={4} />{/* 19-22：距涨停/振幅/换手/成交额，只有今日炸板有 */}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const reopened = r.first_limit_time && r.last_limit_time && r.first_limit_time !== r.last_limit_time
            return (
              <tr key={r.code} className="border-b border-bg-border/40 last:border-0 align-top">
                <td className="py-1.5 pr-3 whitespace-nowrap">
                  <Link to={`/stocks/${r.code}`} className="text-text-primary hover:text-accent">
                    <span className="font-medium">{r.name}</span>
                    <span className="ml-1.5 font-mono text-text-muted">{r.code}</span>
                  </Link>
                </td>
                <td className="py-1.5 pr-3"><RoleTags roles={r.core_roles} reasons={r.core_reasons} /></td>
                <td className={cn('py-1.5 pr-2 text-right font-mono font-bold whitespace-nowrap', pctClass(r.pct_change))}>
                  {fmtPct(r.pct_change)}
                </td>
                <LuCells a={r.limit_up_days_10d} b={r.limit_up_days_20d} c={r.limit_up_days_60d} />
                <td className="py-1.5 pr-2 text-right font-mono text-dragon tabular-nums">{r.board_count_60d || '—'}</td>
                <ChgCells a={r.interval_chg_10d} b={r.interval_chg_20d} c={r.interval_chg_60d} />
                <td className="py-1.5 pr-3 text-right"><ScoreCell v={r.leader_score} fresh={r.scores_as_of_today} tone="dragon" /></td>
                <td className="py-1.5 pr-3 text-right"><ScoreCell v={r.risk_score} fresh={r.scores_as_of_today} tone="danger" /></td>
                <NoteCell
                  text={r.limit_reason}
                  full={[r.limit_reason, r.limit_content].filter(Boolean).join('\n\n') || null}
                />
                <td className="py-1.5 pr-3 whitespace-nowrap">
                  <BoardTag n={r.board_count} />
                  {r.limit_stat_days != null && r.limit_stat_count != null && r.limit_stat_days > 1 && (
                    <span className="ml-1.5 text-text-muted font-mono">
                      {r.limit_stat_days}日{r.limit_stat_count}板
                    </span>
                  )}
                </td>
                <td className="py-1.5 pr-2 text-right font-mono whitespace-nowrap">{fmtTime(r.first_limit_time)}</td>
                <td className={cn('py-1.5 pr-2 text-right font-mono whitespace-nowrap',
                                  r.broken_times ? 'text-warn' : 'text-text-muted')}>
                  {r.broken_times == null ? '—' : r.broken_times === 0 ? '0' : `${r.broken_times}次`}
                </td>
                <td className={cn('py-1.5 pr-2 text-right font-mono whitespace-nowrap',
                                  reopened ? 'text-warn' : 'text-text-secondary')}>
                  {fmtTime(r.last_limit_time)}
                </td>
                <td className="py-1.5 pr-2 text-right font-mono whitespace-nowrap">{fmtSeal(r.seal_amount)}</td>
                <Pad n={4} />
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}


const BROKEN_PICK = (r: LimitUpRadarBrokenStock, k: BrokenSortKey) => ({
  board: r.board_count, board60: r.board_count_60d, gap: r.gap_to_limit_pct,
  broken: r.broken_times, pct_change: r.pct_change, first: r.first_limit_time,
  turnover: r.turnover_rate, amount: r.amount, amp: r.amplitude,
  lu10: r.limit_up_days_10d, lu20: r.limit_up_days_20d, lu60: r.limit_up_days_60d,
  ic10: r.interval_chg_10d, ic20: r.interval_chg_20d, ic60: r.interval_chg_60d,
  leader: r.leader_score, risk: r.risk_score,
}[k])

/**
 * 今日炸板（2026-08-26新增）。
 *
 * 这张表回答的问题只有一个：**今天这个板块里有多少票封板不坚决、烂到什么程度。**
 * 所以列的选取跟涨停表不同，不是把涨停表照搬一遍换个数据源：
 *   · 「距涨停」是核心列——炸板收 -5% 和炸板收 +9% 完全是两回事，光看涨跌幅
 *     还分不出来是"打了一下就走"还是"封了半天塌了"
 *   · 「板位」在前——6天5板的高位股炸板是板块见顶信号，首板冲高回落只是情绪一般
 *   · 「炸板次数」——反复开合说明多空分歧极大
 *   · 没有「终封」列：炸板池本来就没有最终封板时间，它就是没封住
 *
 * 默认排序由后端给（连板降序 → 回落幅度升序 → 炸板次数降序），点表头可改。
 *
 * 关于跟涨停表重复：一只 14:30 炸板或回封的票可能同时出现在两边——涨停池和
 * 炸板池是两个独立接口、并发拉取，没有可比的时间戳。这种重复可以容忍，不做
 * 强制去重（用户 2026-08-26 确认）：随便挑一边丢掉才是真的丢信息。
 */
function BrokenTable({ rows, ctl }: { rows: LimitUpRadarBrokenStock[]; ctl: SortCtl<BrokenSortKey> }) {
  const sorted = applySort(rows, ctl.sort, BROKEN_PICK)
  return (
    <div className="overflow-x-auto">
      <table className="w-full table-fixed text-xs">
        <RadarCols />
        <thead>
          <tr className="text-text-muted border-b border-bg-border">
            <th className="text-left font-normal py-1.5 pr-3">股票</th>
            <th className="text-left font-normal py-1.5 pr-3">核心角色</th>
            <SortTh col="pct_change" label="今日" {...ctl} />
            <SortTh col="lu10" label="10日涨停" {...ctl} title="东财口径：当日曾触及涨停，含炸板" />
            <SortTh col="lu20" label="20日涨停" {...ctl} title="东财口径：当日曾触及涨停，含炸板" />
            <SortTh col="lu60" label="60日涨停" {...ctl} title="东财口径：当日曾触及涨停，含炸板" />
            <SortTh col="board60" label="60日高板" {...ctl} />
            <SortTh col="ic10" label="10日涨幅" {...ctl} title="真实复合区间收益" />
            <SortTh col="ic20" label="20日涨幅" {...ctl} title="真实复合区间收益" />
            <SortTh col="ic60" label="60日涨幅" {...ctl} title="真实复合区间收益" />
            <SortTh col="leader" label="龙头分" {...ctl} />
            <SortTh col="risk" label="风险分" {...ctl} />
            <th className="text-left font-normal py-1.5 pr-3">召回理由</th>
            <SortTh col="board" label="板位" {...ctl} align="left" />
            <SortTh col="first" label="首封" {...ctl} title="首次触及涨停的时间。炸板池没有最终封板时间——它就是没封住" />
            <SortTh col="broken" label="炸板" {...ctl} title="当日开板次数；反复开合说明多空分歧极大" />
            <PadTh n={2} />{/* 终封/封单：炸板没封住，本来就没有这两个 */}
            <SortTh col="gap" label="距涨停" {...ctl}
                    title="收盘价相对当日涨停价的差距。-0.5% 是打了一下就走，-8% 是封了又塌——封板不坚决的程度全在这一列" />
            <SortTh col="amp" label="振幅" {...ctl} />
            <SortTh col="turnover" label="换手" {...ctl} />
            <SortTh col="amount" label="成交额" {...ctl} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={r.code} className="border-b border-bg-border/40 last:border-0 align-top">
              <td className="py-1.5 pr-3 whitespace-nowrap">
                <Link to={`/stocks/${r.code}`} className="text-text-primary hover:text-accent">
                  <span className="font-medium">{r.name}</span>
                  <span className="ml-1.5 font-mono text-text-muted">{r.code}</span>
                </Link>
              </td>
              <td className="py-1.5 pr-3"><RoleTags roles={r.core_roles} reasons={r.core_reasons} /></td>
              <td className={cn('py-1.5 pr-2 text-right font-mono font-bold whitespace-nowrap', pctClass(r.pct_change))}>
                {fmtPct(r.pct_change)}
              </td>
              <LuCells a={r.limit_up_days_10d} b={r.limit_up_days_20d} c={r.limit_up_days_60d} />
              <td className="py-1.5 pr-2 text-right font-mono text-dragon tabular-nums">{r.board_count_60d || '—'}</td>
              <ChgCells a={r.interval_chg_10d} b={r.interval_chg_20d} c={r.interval_chg_60d} />
              <td className="py-1.5 pr-2 text-right"><ScoreCell v={r.leader_score} fresh={r.scores_as_of_today} tone="dragon" /></td>
              <td className="py-1.5 pr-2 text-right"><ScoreCell v={r.risk_score} fresh={r.scores_as_of_today} tone="danger" /></td>
              <NoteCell text={r.core_reasons.join(' · ') || null} />
              <td className="py-1.5 pr-3 whitespace-nowrap">
                <BoardTag n={r.board_count} />
                {r.limit_stat_days != null && r.limit_stat_count != null && r.limit_stat_days > 1 && (
                  <span className="ml-1.5 text-text-muted font-mono">
                    {r.limit_stat_days}日{r.limit_stat_count}板
                  </span>
                )}
              </td>
              <td className="py-1.5 pr-2 text-right font-mono whitespace-nowrap">{fmtTime(r.first_limit_time)}</td>
              <td className={cn('py-1.5 pr-2 text-right font-mono whitespace-nowrap',
                                (r.broken_times ?? 0) >= 3 ? 'text-down font-bold'
                                  : r.broken_times ? 'text-warn' : 'text-text-muted')}>
                {r.broken_times == null ? '—' : `${r.broken_times}次`}
              </td>
              <Pad n={2} />
              <td className={cn('py-1.5 pr-2 text-right font-mono whitespace-nowrap',
                                r.gap_to_limit_pct == null ? 'text-text-muted'
                                  : r.gap_to_limit_pct <= -5 ? 'text-down font-bold'
                                  : r.gap_to_limit_pct <= -2 ? 'text-warn' : 'text-text-secondary')}
                  title={r.price != null && r.limit_price != null
                    ? `收盘 ${r.price} / 涨停价 ${r.limit_price}` : undefined}>
                {fmtPct(r.gap_to_limit_pct)}
              </td>
              <td className="py-1.5 pr-2 text-right font-mono text-text-secondary whitespace-nowrap">
                {r.amplitude == null ? '—' : `${r.amplitude.toFixed(1)}%`}
              </td>
              <td className="py-1.5 pr-2 text-right font-mono text-text-secondary whitespace-nowrap">
                {r.turnover_rate == null ? '—' : `${r.turnover_rate.toFixed(1)}%`}
              </td>
              <td className="py-1.5 pr-2 text-right font-mono text-text-secondary whitespace-nowrap">
                {fmtSeal(r.amount)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

