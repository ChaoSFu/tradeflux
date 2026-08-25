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
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { RefreshCw, ChevronDown, ChevronRight, AlertTriangle, Flame } from 'lucide-react'
import { fetchLimitUpRadar, refreshLimitUpDetails } from '@/api/limitUpRadar'
import { Badge } from '@/components/ui/badge'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import type {
  LimitUpRadarSector, LimitUpRadarTodayStock, LimitUpRadarCoreStock, W2SCoreRole,
} from '@/types'

// 角色标签：只反映"被召回的原因"，不是强弱排名（Core Recall != Core Classification）
const ROLE_LABEL: Record<W2SCoreRole, { text: string; variant: 'dragon' | 'accent' | 'warn' | 'muted' }> = {
  SECTOR_LEADER:   { text: '板块龙头', variant: 'dragon' },
  SECTOR_CORE:     { text: '板块核心', variant: 'dragon' },
  CURRENT_CORE:    { text: '当前核心', variant: 'accent' },
  RECENT_CORE:     { text: '近期核心', variant: 'warn' },
  HISTORICAL_CORE: { text: '历史核心', variant: 'muted' },
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

/** 涨停次数三连：10/20/60日。0 显示为暗色，避免一排 0 抢视线 */
function LuCounts({ a, b, c }: { a: number | null; b: number | null; c: number | null }) {
  const cell = (v: number | null) => (
    <span className={cn('inline-block w-6 text-right tabular-nums', v ? 'text-warn' : 'text-text-muted/50')}>
      {v ?? '—'}
    </span>
  )
  return <span className="font-mono">{cell(a)}<span className="text-text-muted/30 mx-0.5">/</span>{cell(b)}<span className="text-text-muted/30 mx-0.5">/</span>{cell(c)}</span>
}

/** 区间涨幅三连。数据源是东财真实复合区间收益，跟活跃股池的近似算法不同 */
function ChgCounts({ a, b, c }: { a: number | null; b: number | null; c: number | null }) {
  const cell = (v: number | null) => (
    <span className={cn('inline-block w-14 text-right tabular-nums', pctClass(v))}>
      {v == null ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`}
    </span>
  )
  return <span className="font-mono text-xs">{cell(a)}{cell(b)}{cell(c)}</span>
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
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [refreshErr, setRefreshErr] = useState<string | null>(null)

  const params = {
    include_core: includeCore,
    group_mode: primaryOnly ? ('primary' as const) : ('all_watched_sectors' as const),
  }
  const { data, isLoading } = useQuery({
    queryKey: ['limit-up-radar', params],
    queryFn: () => fetchLimitUpRadar(params),
  })

  const refresh = useMutation({
    mutationFn: () => refreshLimitUpDetails(),
    onSuccess: (res) => {
      // 后端刷新失败不抛错，而是 ok=false + 上次成功时间：页面继续显示旧数据并
      // 明确标注，不伪装成最新
      setRefreshErr(res.ok ? null : res.error || '刷新失败')
      if (res.ok) qc.invalidateQueries({ queryKey: ['limit-up-radar'] })
    },
    onError: (e: Error) => setRefreshErr(e.message),
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
            <button
              onClick={() => refresh.mutate()}
              disabled={refresh.isPending}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded text-sm bg-accent-dim text-accent hover:bg-accent/20 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={cn('w-3.5 h-3.5', refresh.isPending && 'animate-spin')} />
              刷新涨停数据
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
              板块门槛：涨停≥{data.filter_min_limit_up} 且 最高≥{data.filter_min_board_height}板
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
            ? `没有板块同时满足「涨停≥${data.filter_min_limit_up} 且 最高≥${data.filter_min_board_height}板」（${data.hidden_sector_count} 个板块因不达标被隐藏）`
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

function SectorCard({ sector, open, onToggle }: {
  sector: LimitUpRadarSector; open: boolean; onToggle: () => void
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
              <CoreTable rows={sector.core_stocks} />
            </section>
          )}

          <section>
            <h3 className="text-xs font-semibold text-text-secondary mb-2">
              今日攻击 <span className="font-normal text-text-muted">（{sector.today_limit_up_count} 只涨停）</span>
            </h3>
            <TodayTable rows={sector.today_limit_up_stocks} />
          </section>
        </div>
      )}
    </div>
  )
}

function RoleTags({ roles, reasons }: { roles: W2SCoreRole[]; reasons: string[] }) {
  if (!roles.length) return null
  return (
    <span className="inline-flex items-center gap-1 flex-wrap" title={reasons.join(' · ')}>
      {roles.map((r) => {
        const cfg = ROLE_LABEL[r]
        return cfg ? <Badge key={r} variant={cfg.variant}>{cfg.text}</Badge> : null
      })}
    </span>
  )
}

function CoreTable({ rows }: { rows: LimitUpRadarCoreStock[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-muted border-b border-bg-border">
            <th className="text-left font-normal py-1.5 pr-3">股票</th>
            <th className="text-left font-normal py-1.5 pr-3">角色</th>
            <th className="text-right font-normal py-1.5 pr-3">今日</th>
            <th className="text-center font-normal py-1.5 pr-3" title="近10日/20日/60日曾涨停次数（东财口径，含炸板）">涨停 10/20/60</th>
            <th className="text-center font-normal py-1.5 pr-3" title="60日最高连板">高板</th>
            <th className="text-right font-normal py-1.5 pr-3" title="近10日/20日/60日区间涨幅（真实复合收益，与活跃股池的近似算法不同）">涨幅 10/20/60</th>
            <th className="text-right font-normal py-1.5 pr-3" title="龙头分。仅当该股今日在候选池、本轮真的算过时才显示">龙头</th>
            <th className="text-right font-normal py-1.5 pr-3" title="风险分。仅当该股今日在候选池、本轮真的算过时才显示">风险</th>
            <th className="text-left font-normal py-1.5 pr-3">召回理由</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.code} className="border-b border-bg-border/40 last:border-0">
              <td className="py-1.5 pr-3 whitespace-nowrap">
                <Link to={`/stocks/${r.code}`} className="text-text-primary hover:text-accent">
                  <span className="font-medium">{r.name}</span>
                  <span className="ml-1.5 font-mono text-text-muted">{r.code}</span>
                </Link>
                {r.is_broken_today && <Badge variant="down" className="ml-1.5">炸板</Badge>}
              </td>
              <td className="py-1.5 pr-3"><RoleTags roles={r.core_roles} reasons={r.core_reasons} /></td>
              <td className={cn('py-1.5 pr-3 text-right font-mono font-bold whitespace-nowrap', pctClass(r.pct_change))}>
                {fmtPct(r.pct_change)}
              </td>
              <td className="py-1.5 pr-3 text-center whitespace-nowrap">
                <LuCounts a={r.limit_up_days_10d} b={r.limit_up_days_20d} c={r.limit_up_days_60d} />
              </td>
              <td className="py-1.5 pr-3 text-center font-mono text-dragon tabular-nums">{r.board_count_60d || '—'}</td>
              <td className="py-1.5 pr-3 text-right whitespace-nowrap">
                <ChgCounts a={r.interval_chg_10d} b={r.interval_chg_20d} c={r.interval_chg_60d} />
              </td>
              <td className="py-1.5 pr-3 text-right"><ScoreCell v={r.leader_score} fresh={r.scores_as_of_today} tone="dragon" /></td>
              <td className="py-1.5 pr-3 text-right"><ScoreCell v={r.risk_score} fresh={r.scores_as_of_today} tone="danger" /></td>
              <td className="py-1.5 pr-3 text-text-muted">{r.core_reasons.join(' · ') || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TodayTable({ rows }: { rows: LimitUpRadarTodayStock[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-muted border-b border-bg-border">
            <th className="text-left font-normal py-1.5 pr-3">股票</th>
            <th className="text-left font-normal py-1.5 pr-3">板位</th>
            <th className="text-right font-normal py-1.5 pr-3" title="首次封板时间">首封</th>
            <th className="text-right font-normal py-1.5 pr-3" title="最终封板时间；与首封不同说明中途开过板">终封</th>
            <th className="text-right font-normal py-1.5 pr-3" title="封单额；— 表示东方财富未提供该字段，不是0">封单</th>
            <th className="text-right font-normal py-1.5 pr-3">炸板</th>
            <th className="text-center font-normal py-1.5 pr-3" title="近10日/20日/60日曾涨停次数（东财口径，含炸板）">涨停 10/20/60</th>
            <th className="text-right font-normal py-1.5 pr-3" title="近10日/20日/60日区间涨幅（真实复合收益，与活跃股池的近似算法不同）">涨幅 10/20/60</th>
            <th className="text-right font-normal py-1.5 pr-3" title="龙头分">龙头</th>
            <th className="text-right font-normal py-1.5 pr-3" title="风险分">风险</th>
            <th className="text-left font-normal py-1.5 pr-3">核心角色</th>
            <th className="text-left font-normal py-1.5">涨停原因（催化剂，非板块归属）</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const reopened = r.first_limit_time && r.last_limit_time && r.first_limit_time !== r.last_limit_time
            return (
              <tr key={r.code} className="border-b border-bg-border/40 last:border-0 align-top">
                <td className="py-1.5 pr-3 whitespace-nowrap">
                  <Link to={`/stocks/${r.code}`} className="text-text-primary hover:text-accent">
                    <span className="font-medium">{r.name}</span>
                    <span className="ml-1.5 font-mono text-text-muted">{r.code}</span>
                  </Link>
                </td>
                <td className="py-1.5 pr-3 whitespace-nowrap">
                  <BoardTag n={r.board_count} />
                  {r.limit_stat_days != null && r.limit_stat_count != null && r.limit_stat_days > 1 && (
                    <span className="ml-1.5 text-text-muted font-mono">
                      {r.limit_stat_days}日{r.limit_stat_count}板
                    </span>
                  )}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono whitespace-nowrap">{fmtTime(r.first_limit_time)}</td>
                <td className={cn('py-1.5 pr-3 text-right font-mono whitespace-nowrap',
                                  reopened ? 'text-warn' : 'text-text-secondary')}>
                  {fmtTime(r.last_limit_time)}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono whitespace-nowrap">{fmtSeal(r.seal_amount)}</td>
                <td className={cn('py-1.5 pr-3 text-right font-mono whitespace-nowrap',
                                  r.broken_times ? 'text-warn' : 'text-text-muted')}>
                  {r.broken_times == null ? '—' : r.broken_times === 0 ? '0' : `${r.broken_times}次`}
                </td>
                <td className="py-1.5 pr-3 text-center whitespace-nowrap">
                  <LuCounts a={r.limit_up_days_10d} b={r.limit_up_days_20d} c={r.limit_up_days_60d} />
                </td>
                <td className="py-1.5 pr-3 text-right whitespace-nowrap">
                  <ChgCounts a={r.interval_chg_10d} b={r.interval_chg_20d} c={r.interval_chg_60d} />
                </td>
                <td className="py-1.5 pr-3 text-right"><ScoreCell v={r.leader_score} fresh={r.scores_as_of_today} tone="dragon" /></td>
                <td className="py-1.5 pr-3 text-right"><ScoreCell v={r.risk_score} fresh={r.scores_as_of_today} tone="danger" /></td>
                <td className="py-1.5 pr-3"><RoleTags roles={r.core_roles} reasons={r.core_reasons} /></td>
                <td className="py-1.5 text-text-muted max-w-md" title={r.limit_content || undefined}>
                  {r.limit_reason || '—'}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
