import { useState, useEffect, useRef } from 'react'
import { NavLink } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchMarketState } from '@/api/marketState'
import { triggerUpdate, fetchUpdateStatus } from '@/api/admin'
import type { UpdateStatus } from '@/api/admin'
import { MARKET_PHASE_LABELS } from '@/utils/format'
import { RefreshCw, Download, CheckCircle, XCircle, ChevronDown, ChevronUp, Settings2 } from 'lucide-react'
import { cn } from '@/utils/cn'

const PHASE_DOT: Record<string, string> = {
  bull_frenzy: 'bg-dragon',
  warm: 'bg-up',
  neutral: 'bg-accent',
  caution: 'bg-warn',
  bear_fear: 'bg-down',
}

// ── Update button with log drawer ─────────────────────────────────────────────

function UpdateButton() {
  const qc = useQueryClient()
  const [polling, setPolling] = useState(false)
  const [status, setStatus] = useState<UpdateStatus | null>(null)
  const [showLog, setShowLog] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  // Poll status while running
  useEffect(() => {
    if (!polling) return
    const id = setInterval(async () => {
      try {
        const s = await fetchUpdateStatus()
        setStatus(s)
        if (s.status !== 'running') {
          setPolling(false)
          // Refresh all data queries after successful update
          if (s.status === 'done') {
            qc.invalidateQueries()
          }
        }
      } catch {
        setPolling(false)
      }
    }, 2000)
    return () => clearInterval(id)
  }, [polling, qc])

  // Auto-scroll log to bottom
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [status?.log_lines])

  const handleClick = async () => {
    if (status?.status === 'running') return
    try {
      const res = await triggerUpdate(true)   // skip_boards=true by default
      if (res.ok) {
        setPolling(true)
        setShowLog(false)
        setStatus({ status: 'running', started_at: null, finished_at: null, message: '启动中…', log_lines: [] })
      }
    } catch (e: any) {
      setStatus({ status: 'error', started_at: null, finished_at: null, message: e.message, log_lines: [] })
    }
  }

  const isRunning = status?.status === 'running'
  const isDone = status?.status === 'done'
  const isError = status?.status === 'error'

  return (
    <div className="relative">
      <div className="flex items-center gap-1">
        <button
          onClick={handleClick}
          disabled={isRunning}
          title="拉取最新行情数据并更新强势池"
          className={cn(
            'flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium transition-all',
            isRunning && 'opacity-60 cursor-not-allowed bg-bg-elevated text-text-muted',
            isDone && 'bg-up/15 text-up border border-up/30',
            isError && 'bg-down/15 text-down border border-down/30',
            !status || status.status === 'idle'
              ? 'bg-bg-elevated text-text-secondary hover:text-text-primary hover:bg-bg-border border border-bg-border'
              : '',
          )}
        >
          {isRunning ? (
            <RefreshCw className="w-3 h-3 animate-spin" />
          ) : isDone ? (
            <CheckCircle className="w-3 h-3" />
          ) : isError ? (
            <XCircle className="w-3 h-3" />
          ) : (
            <Download className="w-3 h-3" />
          )}
          {isRunning ? '更新中…' : isDone ? '已完成' : isError ? '失败' : '更新数据'}
        </button>

        {/* Log toggle — only show when there's something to see */}
        {status && status.status !== 'idle' && (
          <button
            onClick={() => setShowLog((v) => !v)}
            className="p-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-elevated transition-colors"
            title={showLog ? '收起日志' : '查看日志'}
          >
            {showLog ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
        )}
      </div>

      {/* Log drawer */}
      {showLog && status && (
        <div className="absolute right-0 top-full mt-1.5 z-50 w-[480px] rounded-lg border border-bg-border bg-bg-card shadow-xl overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2 border-b border-bg-border">
            <span className="text-xs font-medium text-text-secondary">
              {isRunning ? '🔄 更新中…' : isDone ? '✅ 更新完成' : '❌ 更新失败'}
            </span>
            {status.finished_at && (
              <span className="text-xs text-text-muted">{status.finished_at}</span>
            )}
          </div>
          {status.message && (
            <div className={cn('px-3 py-1.5 text-xs border-b border-bg-border/50', isError ? 'text-down' : 'text-text-secondary')}>
              {status.message}
            </div>
          )}
          <div
            ref={logRef}
            className="max-h-72 overflow-y-auto font-mono text-xs text-text-muted p-3 space-y-0.5"
            style={{ background: 'rgba(0,0,0,0.25)' }}
          >
            {status.log_lines.length === 0 ? (
              <div className="text-text-muted">等待输出…</div>
            ) : (
              status.log_lines.map((line, i) => (
                <div key={i} className={cn(
                  'leading-relaxed',
                  line.includes('✅') || line.includes('成功') ? 'text-up' :
                  line.includes('❌') || line.includes('失败') ? 'text-down' :
                  line.includes('⚠') ? 'text-warn' :
                  line.startsWith('  ') ? 'text-text-muted' : 'text-text-secondary'
                )}>
                  {line}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Header ────────────────────────────────────────────────────────────────────

export function Header({ title }: { title: string }) {
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['market-state'],
    queryFn: fetchMarketState,
    staleTime: 60_000,
  })

  return (
    <header className="h-14 flex items-center justify-between px-6 border-b border-bg-border bg-bg-card shrink-0">
      <h1 className="text-sm font-semibold text-text-primary">{title}</h1>

      <div className="flex items-center gap-3">
        {data && (
          <div className="flex items-center gap-3 text-xs">
            <div className="flex items-center gap-1.5">
              <div className={cn('w-2 h-2 rounded-full', PHASE_DOT[data.market_phase] ?? 'bg-text-muted')} />
              <span className="text-text-secondary font-medium">
                {MARKET_PHASE_LABELS[data.market_phase] ?? data.market_phase}
              </span>
            </div>
            <div className="text-text-muted">|</div>
            <div className="font-mono">
              <span className="text-up">{data.profit_effect_score.toFixed(0)}</span>
              <span className="text-text-muted mx-1">/</span>
              <span className="text-down">{data.loss_effect_score.toFixed(0)}</span>
            </div>
            <div className="text-text-muted">温度</div>
            <div className="font-mono text-accent">{data.emotional_temperature.toFixed(0)}</div>
            <div className="text-text-muted">仓位建议</div>
            <div className="font-mono text-warn">{data.suggested_position_level.toFixed(0)}%</div>
          </div>
        )}

        <UpdateButton />

        <button
          onClick={() => refetch()}
          className={cn(
            'p-1.5 rounded text-text-muted hover:text-text-primary hover:bg-bg-elevated transition-colors',
            isFetching && 'animate-spin text-accent'
          )}
          disabled={isLoading || isFetching}
          title="刷新市场状态"
        >
          <RefreshCw className="w-3.5 h-3.5" />
        </button>

        {/* Sector config shortcut */}
        <NavLink
          to="/sector-config"
          title="板块配置"
          className={({ isActive }) =>
            cn(
              'p-1.5 rounded transition-colors',
              isActive
                ? 'text-accent bg-accent/10'
                : 'text-text-muted hover:text-text-primary hover:bg-bg-elevated',
            )
          }
        >
          <Settings2 className="w-3.5 h-3.5" />
        </NavLink>
      </div>
    </header>
  )
}
