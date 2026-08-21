/**
 * 弱转强雷达 — Phase 1
 * 核心原则："弱转强成立 ≠ 值得买入"：必须同时通过板块闸门、龙头闸门、结构确认
 * 三道硬性关卡才会给出 BUYABLE 信号，不是把指标线性加权后直接吐 BUY。
 * Market Gate / Space Gate 降级判断 / Stress R/R / 三层止损为 Phase 2，本页
 * 明确用灰色"Phase 2"标签占位，不伪造完整度。
 */
import { useEffect, useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchW2SCandidates, fetchW2SCandidateDetail, triggerW2SRefresh, fetchW2SRefreshStatus,
} from '@/api/weakToStrongRadar'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { StateBadge } from '@/components/weakToStrong/StateBadge'
import { ChecklistPanel } from '@/components/weakToStrong/ChecklistPanel'
import { fmt, pct, pctColor } from '@/utils/format'
import { cn } from '@/utils/cn'
import { RefreshCw, Crosshair, AlertTriangle } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import type { W2SCandidate, W2SState } from '@/types'

const STATE_PRIORITY: Record<W2SState, number> = {
  BUYABLE: 0, CONFIRMING: 1, REPAIRING: 2, READY: 3, WATCH: 4, WAIT: 5, BLOCK: 6,
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
        <td className="px-3 py-2"><StateBadge state={cand.current_state} /></td>
        <td className="px-2 py-2 whitespace-nowrap">
          <div className="font-mono text-accent">{cand.stock_code}</div>
          <div className="text-text-primary font-medium">{cand.stock_name}</div>
        </td>
        <td className="px-2 py-2 max-w-[180px]">
          {cand.sector_name ? (
            <>
              <div className="text-text-secondary truncate">{cand.sector_name}</div>
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
          <td colSpan={9} className="px-4 py-3 bg-bg-elevated/40">
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

  const { data: refreshStatus } = useQuery({
    queryKey: ['w2s-refresh-status'],
    queryFn: fetchW2SRefreshStatus,
    refetchInterval: (query) => (query.state.data?.running ? 1500 : false),
  })

  const refreshMutation = useMutation({
    mutationFn: triggerW2SRefresh,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['w2s-refresh-status'] }),
  })

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
        <span>
          「弱转强成立」不等于「值得买入」——本页只在板块闸门、龙头闸门、回踩结构确认
          三项硬性条件同时通过后才给出 BUYABLE 信号，Space/Chips/Risk 相关判断仍是 Phase 2
          占位（详见展开的 Checklist）。⚠️ 不构成任何投资建议或买卖指令。
        </span>
      </div>

      {/* Gate bar */}
      <div className="flex items-center gap-2 px-3 py-2 rounded border border-bg-border bg-bg-card text-xs">
        <Crosshair className="w-3.5 h-3.5 text-accent shrink-0" />
        <span className="text-text-secondary font-medium">大盘闸门（Market Gate）</span>
        <span className="px-1.5 py-0.5 rounded bg-bg-elevated text-text-muted">Phase 2 建设中</span>
        <span className="text-text-muted ml-2">当前仅生效板块闸门 + 龙头闸门 + 结构确认</span>
      </div>

      {/* Header / refresh control */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-xs text-text-muted">
          候选 {candidates?.length ?? 0} 只
          {refreshStatus?.last_result && (
            <span className="ml-2">
              上次刷新：{refreshStatus.last_result.refreshed} 只 / 状态变化 {refreshStatus.last_result.state_changed} 只
              · 耗时 {formatDuration(refreshStatus.last_result.duration_ms)}
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
          {isRefreshing ? '刷新中…' : '立即刷新'}
        </button>
      </div>

      {/* Table */}
      <div className="card overflow-hidden p-0">
        {isLoading ? (
          <div className="p-4"><LoadingRows rows={6} /></div>
        ) : sorted.length === 0 ? (
          <div className="p-8 text-center text-text-muted text-sm">
            暂无候选。候选池由每日更新流程盘前发现，也可点击右上角「立即刷新」触发一次快速状态刷新。
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
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">涨跌幅</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">MA5</th>
                  <th className="text-right px-2 py-1.5 text-text-secondary/70 font-medium">涨停空间</th>
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
