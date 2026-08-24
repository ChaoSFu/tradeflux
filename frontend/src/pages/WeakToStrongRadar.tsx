/**
 * 弱转强雷达 — Phase 1 + Phase 2（Market Gate / Space Gate 降级 / 三层止损 + Stress R/R）
 * 核心原则："弱转强成立 ≠ 值得买入"：BUYABLE = 结构已确认 且 没有 Hard Blocker
 * （数据过期/大盘RED/板块不允许/龙头non_leader/监管风险过高/观察期已过）
 * 且 没有 Soft Cap（板块NEW_START/龙头未决/涨停空间不足）拦截，三层判断链，
 * 不是把指标线性加权后直接吐 BUY（不再用"五道硬性关卡"这个不准确的旧表述，
 * 见 w2s_state_machine.py 模块头注释 / docs/WEAK_TO_STRONG_RADAR.md 第1节）。
 * Stress R/R 是压力情景（模拟次日跌停开盘）下的风险回报比，不是完整期望收益模型；
 * Chips（日内获利盘估算）仍需分钟级数据，本仓库目前没有该数据源，Phase 3 视情况补充，
 * Checklist 里明确用灰色"Phase 2"标签占位，不伪造完整度。
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchW2SCandidates, fetchW2SCandidateDetail, triggerW2SRefresh, fetchW2SRefreshStatus,
  fetchW2SMarketGate, fetchW2SMainlines,
} from '@/api/weakToStrongRadar'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { StateBadge } from '@/components/weakToStrong/StateBadge'
import { ChecklistPanel } from '@/components/weakToStrong/ChecklistPanel'
import { fmt, pct, pctColor } from '@/utils/format'
import { cn } from '@/utils/cn'
import { RefreshCw, Crosshair, AlertTriangle, BookOpen, TrendingUp, Info } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useAuthStore } from '@/store/auth'
import type { W2SCandidate, W2SState, W2SMarketState } from '@/types'

const STATE_PRIORITY: Record<W2SState, number> = {
  BUYABLE: 0, CONFIRMING: 1, REPAIRING: 2, READY: 3, WATCH: 4, WAIT: 5, BLOCK: 6,
}

// GREEN/RED 是红绿灯语义（安全=绿/危险=红），不是价格涨跌方向，用safe/danger
// 而不是up/down——此前误用up/down（A股涨=红/跌=绿），导致文字写着"RED"却渲染
// 成绿色，"GREEN"却渲染成红色，字面意思完全反了（2026-08-24修复的真实bug）。
const MARKET_STATE_STYLE: Record<W2SMarketState, { dot: string; text: string; label: string }> = {
  GREEN:  { dot: 'bg-safe',   text: 'text-safe',   label: '偏多，正常参与' },
  YELLOW: { dot: 'bg-warn',   text: 'text-warn',   label: '中性，谨慎参与' },
  ORANGE: { dot: 'bg-warn',   text: 'text-warn',   label: '偏弱，降低预期' },
  RED:    { dot: 'bg-danger', text: 'text-danger', label: '弱势，暂停新增买入类信号' },
}

const LEADER_LABEL: Record<string, string> = {
  core: '核心龙头', backup: '备选龙头', undetermined: '龙头未决', non_leader: '非龙头',
}

// 监管风险等级越高越危险，用text-danger（红）不是text-down（此前误用down，
// A股跌=绿，导致EXTREME这种最高风险等级反而渲染成绿色，2026-08-24修复）。
const REG_RISK_COLOR: Record<string, string> = {
  LOW: 'text-text-secondary', MEDIUM: 'text-warn', HIGH: 'text-danger', EXTREME: 'text-danger font-bold',
}

// 实时涨跌幅 = (现价-昨收)/昨收，随价格全天漂移，跟"竞价Gap"（9:25后固定的
// 开盘Gap）是两个不同含义的指标，各自单独一列，不能互相替代（round4 review
// 曾指出"涨跌幅"列此前误渲染成竞价Gap，这里是修复后新增的真正涨跌幅列）。
function todayPctChange(cand: W2SCandidate): number | null {
  if (cand.price == null || cand.prev_close == null || cand.prev_close <= 0) return null
  return round2((cand.price - cand.prev_close) / cand.prev_close * 100)
}
function round2(n: number): number {
  return Math.round(n * 100) / 100
}

// 本地日期字符串（不用toISOString，UTC+8下午夜前后会跟本地日期差一天），
// 只用来跟 mainlines.data_as_of 比较是不是"今天"，判断板块数据要不要显眼提示过期。
// 表头复杂指标的说明图标：光标悬停显示——指标含义/数据来源/计算方式/结果怎么
// 解读，不用靠用户自己去猜或者翻文档（2026-08-24按用户要求新增）。
function HeaderHint({ label, hint, align = 'right' }: { label: string; hint: string; align?: 'left' | 'right' }) {
  return (
    <span className={cn('inline-flex items-center gap-1', align === 'right' && 'justify-end w-full')} title={hint}>
      {label}
      <Info className="w-3 h-3 text-text-muted/60 shrink-0" />
    </span>
  )
}

function localTodayStr(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function formatDuration(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}秒` : `${ms}毫秒`
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}秒前`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}分钟前`
}

function CandidateRow({
  cand, expanded, onToggle,
}: {
  cand: W2SCandidate
  expanded: boolean
  onToggle: () => void
}) {
  const { data: detail, isLoading } = useQuery({
    queryKey: ['w2s-candidate-detail', cand.stock_code],
    queryFn: () => fetchW2SCandidateDetail(cand.stock_code),
    enabled: expanded,
  })

  return (
    <>
      <tr
        onClick={onToggle}
        className="border-b border-bg-border/15 last:border-0 cursor-pointer hover:bg-bg-elevated transition-colors"
      >
        <td className="px-3 py-2">
          <StateBadge state={cand.current_state} />
          {cand.structural_state !== cand.current_state && (
            <div className="text-[10px] text-text-muted mt-0.5" title="底层结构进度，被闸门临时覆盖展示">
              结构:{cand.structural_state}
            </div>
          )}
        </td>
        <td className="px-2 py-2 whitespace-nowrap">
          <div className="font-mono text-accent">{cand.stock_code}</div>
          <div className="text-text-primary font-medium">{cand.stock_name}</div>
        </td>
        <td className="px-2 py-2 max-w-[180px]">
          {cand.sector_name ? (
            <>
              <div className="text-text-secondary truncate flex items-center gap-1">
                {cand.sector_name}
                {cand.is_mainline_sector && (
                  <span
                    className="shrink-0 px-1 py-0.5 rounded text-[10px] font-medium bg-accent-dim text-accent"
                    title="当前MAIN_UPTREND强度前列，结构确认后才可能放行到BUYABLE"
                  >
                    主线
                  </span>
                )}
              </div>
              <div className="text-text-muted text-[11px]">{cand.sector_category ?? '—'}</div>
            </>
          ) : <span className="text-text-muted/50">未分类</span>}
        </td>
        <td className="px-2 py-2 text-text-secondary">
          {cand.leader_type ? LEADER_LABEL[cand.leader_type] ?? cand.leader_type : '—'}
          {cand.leader_rank ? <span className="text-text-muted"> · 第{cand.leader_rank}名</span> : null}
        </td>
        <td className="px-2 py-2 font-mono text-right">{fmt(cand.price, 2)}</td>
        <td className="px-2 py-2 font-mono text-right">
          <span className={pctColor(todayPctChange(cand))}>{pct(todayPctChange(cand))}</span>
        </td>
        <td className="px-2 py-2 font-mono text-right">
          <span className={pctColor(cand.auction_gap)}>{pct(cand.auction_gap)}</span>
        </td>
        <td className="px-2 py-2 font-mono text-right text-text-secondary">{fmt(cand.ma5, 2)}</td>
        <td className="px-2 py-2 font-mono text-right text-text-secondary">
          {cand.limit_room != null ? `${cand.limit_room.toFixed(1)}%` : '—'}
        </td>
        <td className="px-2 py-2 font-mono text-right">
          {cand.stress_rr != null ? (
            <span className={cand.stress_rr >= 1.0 ? 'text-up' : 'text-text-secondary'}>{cand.stress_rr.toFixed(2)}</span>
          ) : '—'}
        </td>
        <td className="px-2 py-2 text-right">
          <span className={cn('font-mono', REG_RISK_COLOR[cand.regulatory_risk_level ?? ''] ?? 'text-text-secondary')}>
            {cand.regulatory_risk_level ?? '—'}
          </span>
        </td>
        <td className="px-3 py-2 max-w-[240px] text-text-secondary text-[11px] truncate">
          {cand.trigger_reasons || cand.block_reasons || '—'}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-bg-border/15">
          <td colSpan={12} className="px-4 py-3 bg-bg-elevated/40">
            {isLoading || !detail ? (
              <LoadingRows rows={2} />
            ) : (
              <ChecklistPanel checklist={detail.checklist} />
            )}
          </td>
        </tr>
      )}
    </>
  )
}

export default function WeakToStrongRadar() {
  const queryClient = useQueryClient()
  const isLoggedIn = useAuthStore((s) => s.isLoggedIn)
  const [expandedCode, setExpandedCode] = useState<string | null>(null)

  const { data: candidates, isLoading } = useQuery({
    queryKey: ['w2s-candidates'],
    queryFn: () => fetchW2SCandidates(true),
  })

  const { data: marketGate } = useQuery({
    queryKey: ['w2s-market-gate'],
    queryFn: fetchW2SMarketGate,
  })

  // 板块Sector统计只由daily_update刷新，手动/refresh从不触碰Sector表，主线结果
  // 全天不随W2S刷新变化，不需要跟着刷新按钮invalidate——独立query，页面加载/
  // 切回时按React Query默认策略取一次即可。
  const { data: mainlines } = useQuery({
    queryKey: ['w2s-mainlines'],
    queryFn: fetchW2SMainlines,
  })

  const { data: refreshStatus } = useQuery({
    queryKey: ['w2s-refresh-status'],
    queryFn: fetchW2SRefreshStatus,
    refetchInterval: (query) => (query.state.data?.running ? 1500 : false),
  })

  const refreshMutation = useMutation({
    mutationFn: triggerW2SRefresh,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['w2s-refresh-status'] }),
  })

  // 每秒跳一次，用来实时算"距上次刷新多久"——手动触发为主的刷新模式下，
  // 这个时间比自动轮询频率更值得让用户看见。不做前端冷却限制：单用户场景
  // 不需要系统帮忙控制点击频率，真正要防的是"同时有两个刷新任务在跑"，
  // 这由后端 /refresh 的运行中检查（同一时刻只有一个线程在更新数据）保证，
  // 不依赖前端按钮状态。
  const [nowTick, setNowTick] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])
  const lastTriggeredAt = refreshStatus?.last_result?.triggered_at
    ? new Date(refreshStatus.last_result.triggered_at).getTime()
    : null
  const secondsSinceRefresh = lastTriggeredAt != null ? Math.max(0, Math.floor((nowTick - lastTriggeredAt) / 1000)) : null

  // 刷新任务结束后（running: true → false）拉取最新候选列表
  const wasRunning = useMemo(() => refreshStatus?.running ?? false, [refreshStatus?.running])
  useEffect(() => {
    if (!wasRunning && refreshStatus?.last_result) {
      queryClient.invalidateQueries({ queryKey: ['w2s-candidates'] })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshStatus?.last_result?.triggered_at])

  const sorted = useMemo(() => {
    if (!candidates) return []
    return [...candidates].sort((a, b) => {
      const p = STATE_PRIORITY[a.current_state] - STATE_PRIORITY[b.current_state]
      if (p !== 0) return p
      return (b.leader_score ?? 0) - (a.leader_score ?? 0)
    })
  }, [candidates])

  const isRefreshing = refreshMutation.isPending || refreshStatus?.running

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Disclaimer */}
      <div className="flex items-start gap-2 p-3 rounded bg-warn-dim border border-warn/20 text-xs text-warn">
        <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span className="flex-1">
          「弱转强成立」不等于「值得买入」——BUYABLE 需要回踩结构真正确认，且没有 Hard
          Blocker（大盘/板块/龙头非核心/监管风险过高等，命中即 BLOCK）、没有 Soft Cap
          （板块早期/龙头未决/涨停空间不足，命中封顶到较低展示态）拦截才会给出；
          Stress R/R 只是压力情景下的风险回报参考，不参与放行判断，Chips 相关判断仍是
          Phase 2 占位（详见展开的 Checklist）。⚠️ 不构成任何投资建议或买卖指令。
        </span>
        <Link
          to="/weak-to-strong-radar/guide"
          className="flex items-center gap-1 shrink-0 text-warn/80 hover:text-warn underline decoration-warn/30 underline-offset-2 whitespace-nowrap"
        >
          <BookOpen className="w-3 h-3" /> 了解实现原理
        </Link>
      </div>

      {/* Gate bar */}
      <div className="flex items-center gap-2 px-3 py-2 rounded border border-bg-border bg-bg-card text-xs flex-wrap">
        <Crosshair className="w-3.5 h-3.5 text-accent shrink-0" />
        <span className="text-text-secondary font-medium">大盘闸门（Market Gate）</span>
        {marketGate ? (
          <>
            <span className="flex items-center gap-1.5">
              <span className={cn('w-1.5 h-1.5 rounded-full', MARKET_STATE_STYLE[marketGate.market_state]?.dot)} />
              <span className={cn('font-mono font-semibold', MARKET_STATE_STYLE[marketGate.market_state]?.text)}>
                {marketGate.market_state}
              </span>
            </span>
            <span className="text-text-muted">
              趋势分 {fmt(marketGate.trend_score, 1)} · 风险偏好分 {fmt(marketGate.risk_score, 1)}
            </span>
            <span className="text-text-muted">{MARKET_STATE_STYLE[marketGate.market_state]?.label}</span>
            {/* Market Gate实际由趋势(指数)+广度(涨跌家数)两段独立刷新节奏的数据拼成，
                此前只显示一个笼统的"数据截至"（=广度的日期），趋势那段掉线不会体现在这
                一个日期上（windvane涨跌统计连续7天静默失败、Market Gate用旧数据算了
                一周才被发现，这是这次事故暴露出来的真实盲区，2026-08-24修复）。两者
                相同时仍只显示一个日期，不制造没必要的视觉噪音；不同或任一非当日时才
                拆开显示、并用警示色标出哪一段过期。 */}
            {marketGate.trend_as_of === marketGate.breadth_as_of ? (
              marketGate.as_of_date && (
                <span className={cn(
                  'ml-auto',
                  marketGate.as_of_date === localTodayStr() ? 'text-text-muted/70' : 'text-down font-medium',
                )}>
                  {marketGate.as_of_date === localTodayStr() ? '' : '⚠ '}数据截至 {marketGate.as_of_date}
                </span>
              )
            ) : (
              <span className="ml-auto flex items-center gap-2">
                <span className={cn(marketGate.trend_as_of === localTodayStr() ? 'text-text-muted/70' : 'text-down font-medium')}>
                  趋势 {marketGate.trend_as_of ?? '—'}
                </span>
                <span className={cn(marketGate.breadth_as_of === localTodayStr() ? 'text-text-muted/70' : 'text-down font-medium')}>
                  广度 {marketGate.breadth_as_of ?? '—'}
                </span>
              </span>
            )}
          </>
        ) : (
          <span className="text-text-muted">加载中…</span>
        )}
      </div>

      {/* 今日主线摘要（2026-08-24新增）：板块视角，Top3上限不是配额，纯本地DB计算，
          跟 run_refresh() 共用同一份 get_current_mainlines()，不会跟候选表的
          is_mainline_sector标记算出不一样的结果。板块数据只由daily_update刷新，
          过期时必须显眼提示，不能包装成"今日"实时数据（2026-08-24 windvane
          涨跌统计连续7天静默失败、Market Gate用旧数据算了一周才被发现，这个
          教训直接决定了这里的过期提示不能省）。 */}
      <div className="flex items-start gap-2 px-3 py-2 rounded border border-bg-border bg-bg-card text-xs">
        <TrendingUp className="w-3.5 h-3.5 text-accent shrink-0 mt-0.5" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <span className="text-text-secondary font-medium">今日主线</span>
            {mainlines?.data_as_of && (
              mainlines.data_as_of === localTodayStr() ? (
                <span className="text-text-muted/70">数据截至 {mainlines.data_as_of}</span>
              ) : (
                <span className="text-down font-medium">
                  ⚠ 板块数据截至 {mainlines.data_as_of}，非当日实时，主线结论可能已过期
                </span>
              )
            )}
          </div>
          {!mainlines ? (
            <span className="text-text-muted">加载中…</span>
          ) : mainlines.mainlines.length === 0 ? (
            <span className="text-text-muted">当前无明确主线（全市场关注板块里没有板块达到 MAIN_UPTREND 强度，不为凑数硬选）</span>
          ) : (
            <div className="flex flex-wrap gap-x-5 gap-y-1.5">
              {mainlines.mainlines.map((m) => (
                <div key={m.sector_id} className="flex items-center gap-1.5">
                  <span className="font-mono text-text-muted">#{m.rank}</span>
                  <span className="text-text-primary font-medium">{m.sector_name}</span>
                  <span className="text-text-muted text-[11px]">{m.sector_category}</span>
                  <span className="font-mono text-accent">强度 {m.sector_strength_score.toFixed(0)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Header / refresh control */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-xs text-text-muted">
          候选 {candidates?.length ?? 0} 只
          {refreshStatus?.last_result && (
            <span className="ml-2">
              上次刷新：{refreshStatus.last_result.refreshed} 只 / 状态变化 {refreshStatus.last_result.state_changed} 只
              · 耗时 {formatDuration(refreshStatus.last_result.duration_ms)}
              {secondsSinceRefresh != null && (
                <span className="text-text-muted/70"> · {formatElapsed(secondsSinceRefresh)}</span>
              )}
            </span>
          )}
          {refreshStatus?.last_error && (
            <span className="ml-2 text-down">上次刷新失败：{refreshStatus.last_error}</span>
          )}
          {refreshMutation.isError && (
            <span className="ml-2 text-down">{(refreshMutation.error as Error).message}</span>
          )}
        </div>
        <button
          onClick={() => refreshMutation.mutate()}
          disabled={!!isRefreshing || !isLoggedIn}
          title={isLoggedIn ? undefined : '需要登录后才能手动触发刷新'}
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded text-xs font-medium transition-colors',
            isRefreshing || !isLoggedIn
              ? 'bg-bg-elevated text-text-muted cursor-not-allowed'
              : 'bg-accent-dim text-accent hover:bg-accent/20',
          )}
        >
          <RefreshCw className={cn('w-3.5 h-3.5', isRefreshing && 'animate-spin')} />
          {isRefreshing ? '刷新中…' : '刷新数据并重新评估'}
        </button>
      </div>

      {/* Table */}
      <div className="card overflow-hidden p-0">
        {isLoading ? (
          <div className="p-4"><LoadingRows rows={6} /></div>
        ) : sorted.length === 0 ? (
          <div className="p-8 text-center text-text-muted text-sm">
            暂无候选。候选池由每日更新流程盘前发现，也可点击右上角「刷新数据并重新评估」触发一次快速状态刷新。
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-bg-border/20 bg-bg-elevated sticky top-0 z-10">
                  <th className="text-left px-3 py-1.5 text-text-secondary/70 font-medium">
                    <HeaderHint align="left" label="状态" hint="展示态：WATCH观察/READY竞价达标/REPAIRING修复中/CONFIRMING确认中/BUYABLE结构确认/WAIT等待/BLOCK拦截。由底层结构事实叠加Hard Blocker(硬性拦截)和Soft Cap(软上限)两层规则推导，不是几个指标加权打分。" />
                  </th>
                  <th className="text-left px-2 py-1.5 text-text-secondary/70 font-medium">股票</th>
                  <th className="text-left px-2 py-1.5 text-text-secondary/70 font-medium">
                    <HeaderHint align="left" label="板块" hint="该股票当前的主板块归属，每日按板块当日强势股数量/连板高度/情绪分自动算出。下方小字是板块生命周期分类：NEW_START早期/EXPANDING扩张/MAIN_UPTREND主升/HEALTHY_DIVERGENCE健康分歧/HIGH_LEVEL_WARNING高位预警/DECLINING退潮/DEAD死亡。" />
                  </th>
                  <th className="text-left px-2 py-1.5 text-text-secondary/70 font-medium">
                    <HeaderHint align="left" label="龙头" hint="同板块内按Core Leader Score排名后的身份：核心龙头/备选龙头/龙头未决/非龙头。综合板块内排名、全市场强势池百分位、连板历史、换手率、板块分歧日抗跌能力等因素算出，非龙头会被硬性拦截，龙头未决会软上限封顶在CONFIRMING。" />
                  </th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">现价</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">
                    <HeaderHint label="涨跌幅" hint="(现价-昨收)/昨收，随现价全天实时变化。" />
                  </th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">
                    <HeaderHint label="竞价Gap" hint="竞价阶段(9:25)的(今开-昨收)/昨收，非实时涨跌幅，开盘后固定不变，是READY状态的判断依据之一。" />
                  </th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">
                    <HeaderHint label="MA5" hint="最近5个交易日收盘价的算术平均线，用于判断股价是否收复短期均线支撑（结构修复/确认的判断依据之一）。" />
                  </th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">
                    <HeaderHint label="涨停空间" hint="(涨停价-现价)/现价。涨停价按该股票真实涨跌停规则算出（主板10%/创业板科创板20%/北交所30%/ST 5%），反映距离涨停还剩多少上涨空间，低于阈值会软上限封顶在WAIT。" />
                  </th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">
                    <HeaderHint label="Stress R/R" hint="压力情景风险回报比 = 今日剩余涨停空间% / 模拟明日跌停开盘的亏损%，只回答『如果明天真跌停，今天剩的空间值不值得担这个风险』。仅供参考，不参与BUYABLE的硬性拦截判断。" />
                  </th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">
                    <HeaderHint label="监管风险" hint="该股票当前/近期是否处于监管重点关注名单，数据来自监管公开信息同步。LOW/MEDIUM/HIGH/EXTREME四档粗分类，达到配置的风险等级会硬性拦截买入类信号。" />
                  </th>
                  <th className="text-left px-3 py-1.5 text-text-secondary/70 font-medium">触发/拦截原因</th>
                </tr>
              </thead>
              <tbody>
                {sorted.slice(0, 15).map((c) => (
                  <CandidateRow
                    key={c.stock_code}
                    cand={c}
                    expanded={expandedCode === c.stock_code}
                    onToggle={() => setExpandedCode((prev) => (prev === c.stock_code ? null : c.stock_code))}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {sorted.length > 15 && (
        <div className="text-center text-xs text-text-muted">
          仅展示优先级最高的 15 只（共 {sorted.length} 只候选，BLOCK 状态自动排在最后）
        </div>
      )}
    </div>
  )
}
