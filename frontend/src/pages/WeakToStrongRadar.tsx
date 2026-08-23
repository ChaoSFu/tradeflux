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
  fetchW2SMarketGate,
} from '@/api/weakToStrongRadar'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { StateBadge } from '@/components/weakToStrong/StateBadge'
import { ChecklistPanel } from '@/components/weakToStrong/ChecklistPanel'
import { fmt, pct, pctColor } from '@/utils/format'
import { cn } from '@/utils/cn'
import { RefreshCw, Crosshair, AlertTriangle, BookOpen } from 'lucide-react'
import { Link } from 'react-router-dom'
import { useAuthStore } from '@/store/auth'
import type { W2SCandidate, W2SState, W2SMarketState } from '@/types'

const STATE_PRIORITY: Record<W2SState, number> = {
  BUYABLE: 0, CONFIRMING: 1, REPAIRING: 2, READY: 3, WATCH: 4, WAIT: 5, BLOCK: 6,
}

const MARKET_STATE_STYLE: Record<W2SMarketState, { dot: string; text: string; label: string }> = {
  GREEN:  { dot: 'bg-up',    text: 'text-up',    label: '偏多，正常参与' },
  YELLOW: { dot: 'bg-warn',  text: 'text-warn',  label: '中性，谨慎参与' },
  ORANGE: { dot: 'bg-warn',  text: 'text-warn',  label: '偏弱，降低预期' },
  RED:    { dot: 'bg-down',  text: 'text-down',  label: '弱势，暂停新增买入类信号' },
}

const LEADER_LABEL: Record<string, string> = {
  core: '核心龙头', backup: '备选龙头', undetermined: '龙头未决', non_leader: '非龙头',
}

const REG_RISK_COLOR: Record<string, string> = {
  LOW: 'text-text-secondary', MEDIUM: 'text-warn', HIGH: 'text-down', EXTREME: 'text-down font-bold',
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
          <td colSpan={11} className="px-4 py-3 bg-bg-elevated/40">
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
            {marketGate.as_of_date && (
              <span className="text-text-muted/70 ml-auto">数据截至 {marketGate.as_of_date}</span>
            )}
          </>
        ) : (
          <span className="text-text-muted">加载中…</span>
        )}
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
                  <th className="text-left px-3 py-1.5 text-text-secondary/70 font-medium">状态</th>
                  <th className="text-left px-2 py-1.5 text-text-secondary/70 font-medium">股票</th>
                  <th className="text-left px-2 py-1.5 text-text-secondary/70 font-medium">板块</th>
                  <th className="text-left px-2 py-1.5 text-text-secondary/70 font-medium">龙头</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">现价</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium" title="竞价阶段(9:25)的(今开-昨收)/昨收，非实时涨跌幅，开盘后固定不变">竞价Gap</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">MA5</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">涨停空间</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">Stress R/R</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">监管风险</th>
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
