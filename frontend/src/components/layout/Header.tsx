import { useState, useEffect, useRef } from 'react'
import { NavLink } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchMarketState } from '@/api/marketState'
import {
  triggerUpdate, fetchUpdateStatus,
  triggerSyncBoards, fetchSyncBoardsStatus,
  fetchSchedulerStatus, fetchLastUpdateStatus,
} from '@/api/admin'
import type { UpdateStatus, SchedulerStatus, LastUpdateStatus } from '@/api/admin'
import { MARKET_PHASE_LABELS } from '@/utils/format'
import {
  Download, CheckCircle, XCircle,
  ChevronDown, ChevronUp, Settings2, Layers, AlertTriangle,
  LogIn, LogOut, User, Lock, RefreshCw,
} from 'lucide-react'
import { cn } from '@/utils/cn'
import { useAuthStore } from '@/store/auth'
import { LoginModal } from '@/components/auth/LoginModal'

const PHASE_DOT: Record<string, string> = {
  bull_frenzy: 'bg-dragon',
  warm:        'bg-up',
  neutral:     'bg-accent',
  caution:     'bg-warn',
  bear_fear:   'bg-down',
}

// ── 单个任务日志抽屉 ──────────────────────────────────────────────────────────

interface JobPanelProps {
  label: string
  icon: React.ReactNode
  status: UpdateStatus | null
  isRunning: boolean
  isDone: boolean
  isError: boolean
  onTrigger: () => void
  description: string
  estimatedTime: string
  locked?: boolean  // 未登录时锁定
}

function JobPanel({
  label, icon, status, isRunning, isDone, isError,
  onTrigger, description, estimatedTime, locked = false,
}: JobPanelProps) {
  const [showLog, setShowLog] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [status?.log_lines])

  return (
    <div className="border-b border-bg-border/30 last:border-0">
      {/* 任务头部 */}
      <div className="flex items-start gap-3 px-4 py-3">
        <div className={cn(
          'mt-0.5 shrink-0',
          isRunning ? 'text-accent animate-spin' : isDone ? 'text-up' : isError ? 'text-down' : 'text-text-muted',
        )}>
          {isRunning ? <RefreshCw className="w-3.5 h-3.5" /> :
           isDone    ? <CheckCircle className="w-3.5 h-3.5" /> :
           isError   ? <XCircle className="w-3.5 h-3.5" /> :
           icon}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-text-primary">{label}</span>
            <span className="text-[10px] text-text-muted shrink-0">约 {estimatedTime}</span>
          </div>
          <p className="text-[10px] text-text-muted mt-0.5 leading-relaxed">{description}</p>

          {/* 状态消息 */}
          {status?.message && (
            <p className={cn(
              'text-[10px] mt-1 truncate',
              isError ? 'text-down' : isRunning ? 'text-accent' : 'text-text-secondary',
            )}>
              {status.message}
            </p>
          )}

          {/* 上次运行时间 + 耗时 */}
          {status?.started_at && (
            <p className="text-[10px] text-text-muted mt-0.5">
              {(() => {
                const start = new Date(status.started_at)
                const timeStr = start.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
                if (status.finished_at) {
                  const end = new Date(status.finished_at)
                  const secs = Math.round((end.getTime() - start.getTime()) / 1000)
                  const dur = secs >= 60 ? `${Math.floor(secs / 60)}分${secs % 60}秒` : `${secs}秒`
                  return `上次运行 ${timeStr}，耗时 ${dur}`
                }
                return `开始于 ${timeStr}`
              })()}
            </p>
          )}
        </div>

        {/* 操作按钮 */}
        <div className="flex items-center gap-1 shrink-0">
          {status && status.status !== 'idle' && (
            <button
              onClick={() => setShowLog(v => !v)}
              className="p-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-elevated transition-colors"
              title={showLog ? '收起日志' : '查看日志'}
            >
              {showLog ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            </button>
          )}
          {locked ? (
            <span
              className="flex items-center gap-1 text-[10px] px-2 py-1 rounded text-text-muted bg-bg-elevated border border-bg-border cursor-default"
              title="请先登录"
            >
              <Lock className="w-3 h-3" />
              需要登录
            </span>
          ) : (
            <button
              onClick={onTrigger}
              disabled={isRunning}
              className={cn(
                'text-[10px] px-2 py-1 rounded transition-colors font-medium whitespace-nowrap',
                isRunning
                  ? 'bg-bg-elevated text-text-muted cursor-not-allowed'
                  : isDone
                    ? 'bg-up/15 text-up border border-up/30 hover:bg-up/25'
                    : isError
                      ? 'bg-down/15 text-down border border-down/30 hover:bg-down/25'
                      : 'bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25',
              )}
            >
              {isRunning ? '运行中…' : isDone ? '再次运行' : isError ? '重试' : '立即运行'}
            </button>
          )}
        </div>
      </div>

      {/* 日志抽屉 */}
      {showLog && status && (
        <div
          ref={logRef}
          className="max-h-48 overflow-y-auto font-mono text-[10px] text-text-muted px-4 pb-3 space-y-0.5"
          style={{ background: 'rgba(0,0,0,0.2)' }}
        >
          {status.log_lines.length === 0 ? (
            <div className="py-2">等待输出…</div>
          ) : (
            status.log_lines.map((line, i) => (
              <div key={i} className={cn(
                'leading-relaxed',
                line.includes('✅') || line.includes('成功') ? 'text-up' :
                line.includes('❌') || line.includes('失败') ? 'text-down' :
                line.includes('⚠')                          ? 'text-warn' :
                line.startsWith('  ')                       ? 'text-text-muted' : 'text-text-secondary',
              )}>
                {line}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}

// ── 数据更新入口（整合按钮 + 下拉面板）────────────────────────────────────────

function DataUpdateMenu() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const { isLoggedIn } = useAuthStore()

  // ── 每日更新任务 ────────────────────────────────────────────────────────────
  const [updatePolling, setUpdatePolling] = useState(false)
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus | null>(null)

  useEffect(() => {
    if (!updatePolling) return
    const id = setInterval(async () => {
      try {
        const s = await fetchUpdateStatus()
        setUpdateStatus(s)
        if (s.status !== 'running') {
          setUpdatePolling(false)
          if (s.status === 'done') qc.invalidateQueries()
          // 更新完成后刷新持久化状态
          fetchLastUpdateStatus().then(setLastUpdate).catch(() => {})
        }
      } catch { setUpdatePolling(false) }
    }, 1000)
    return () => clearInterval(id)
  }, [updatePolling, qc])

  // ── 板块同步任务 ────────────────────────────────────────────────────────────
  const [syncPolling, setSyncPolling] = useState(false)
  const [syncStatus, setSyncStatus] = useState<UpdateStatus | null>(null)
  const [schedulerStatus, setSchedulerStatus] = useState<SchedulerStatus | null>(null)
  const [lastUpdate, setLastUpdate] = useState<LastUpdateStatus | null>(null)

  // ── 挂载时拉取初始状态，若已在运行则自动启动轮询 ─────────────────────────────
  useEffect(() => {
    fetchUpdateStatus().then(s => {
      setUpdateStatus(s)
      // 页面刷新/导航回来时，若后端任务仍在运行，自动接续轮询
      if (s.status === 'running') setUpdatePolling(true)
    }).catch(() => {})
    fetchSyncBoardsStatus().then(s => {
      setSyncStatus(s)
      if (s.status === 'running') setSyncPolling(true)
    }).catch(() => {})
    fetchSchedulerStatus().then(setSchedulerStatus).catch(() => {})
    fetchLastUpdateStatus().then(setLastUpdate).catch(() => {})
  }, [])

  useEffect(() => {
    if (!syncPolling) return
    const id = setInterval(async () => {
      try {
        const s = await fetchSyncBoardsStatus()
        setSyncStatus(s)
        if (s.status !== 'running') setSyncPolling(false)
      } catch { setSyncPolling(false) }
    }, 1000)
    return () => clearInterval(id)
  }, [syncPolling])

  // ── 关闭菜单（点击外部）────────────────────────────────────────────────────
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const showToast = (msg: string) => {
    setToast(msg)
    setTimeout(() => setToast(null), 3500)
  }

  const handleUpdate = async () => {
    if (!isLoggedIn) { showToast('请先登录后再执行数据更新'); return }
    if (updateStatus?.status === 'running') { setOpen(true); return }
    // 本地快速判断：板块同步运行中时给出提示
    if (syncStatus?.status === 'running') {
      showToast('板块同步运行中，请等待完成后再执行数据更新')
      setOpen(true)
      return
    }
    try {
      const res = await triggerUpdate(true)
      if (res.ok) {
        setUpdatePolling(true)
        setUpdateStatus({ status: 'running', started_at: null, finished_at: null, message: '启动中…', log_lines: [] })
        setOpen(true)
      } else {
        showToast(res.message)
        if (res.message.includes('运行中')) {
          const cur = await fetchUpdateStatus()
          setUpdateStatus(cur)
          setOpen(true)
          if (cur.status === 'running') setUpdatePolling(true)
        }
      }
    } catch (e: any) {
      showToast(e.message ?? '请求失败')
    }
  }

  const handleSyncBoards = async () => {
    if (!isLoggedIn) { showToast('请先登录后再执行板块同步'); return }
    if (syncStatus?.status === 'running') { setOpen(true); return }
    // 本地快速判断：日更运行中时给出提示
    if (updateStatus?.status === 'running') {
      showToast('数据更新运行中，请等待完成后再执行板块同步')
      setOpen(true)
      return
    }
    try {
      const res = await triggerSyncBoards(true)  // meta_only：只更新涨跌幅/换手/市值
      if (res.ok) {
        setSyncPolling(true)
        setSyncStatus({ status: 'running', started_at: null, finished_at: null, message: '启动中…', log_lines: [] })
        setOpen(true)
      } else {
        showToast(res.message)
        if (res.message.includes('运行中')) {
          const cur = await fetchSyncBoardsStatus()
          setSyncStatus(cur)
          setOpen(true)
          if (cur.status === 'running') setSyncPolling(true)
        }
      }
    } catch (e: any) {
      showToast(e.message ?? '请求失败')
    }
  }

  const updateRunning = updateStatus?.status === 'running'
  const syncRunning   = syncStatus?.status   === 'running'
  const anyRunning    = updateRunning || syncRunning

  // 最后更新：优先用持久化数据（服务重启后仍存在）
  const lastFinished = lastUpdate?.finished_at ?? updateStatus?.finished_at
  const lastResult   = lastUpdate?.status ?? updateStatus?.status
  const lastSource   = lastUpdate?.source  // 'manual' | 'scheduled'
  const lastTimeStr  = lastFinished
    ? new Date(lastFinished).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    : null
  const sourceLabel  = lastSource === 'scheduled' ? '定时' : lastSource === 'manual' ? '手动' : ''

  // 下次调度时间
  const nextRunStr = schedulerStatus?.next_run
    ? new Date(schedulerStatus.next_run).toLocaleString('zh-CN', {
        month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      })
    : null

  return (
    <div className="relative flex items-center gap-2" ref={menuRef}>
      {/* 最后更新时间 + 来源 + 结果（运行中也保持显示） */}
      {lastTimeStr && (
        <div className={cn(
          'flex items-center gap-1 text-[10px] px-2 py-0.5 rounded border',
          lastResult === 'done'
            ? 'text-up border-up/30 bg-up/5'
            : lastResult === 'error'
              ? 'text-down border-down/30 bg-down/5'
              : 'text-text-muted border-bg-border',
        )}>
          {lastResult === 'done'
            ? <CheckCircle className="w-3 h-3" />
            : lastResult === 'error'
              ? <XCircle className="w-3 h-3" />
              : null
          }
          <span>{sourceLabel && `${sourceLabel} `}{lastTimeStr}</span>
        </div>
      )}

      {/* 调度器下次执行时间（运行中也保持显示） */}
      {nextRunStr && schedulerStatus?.running && (
        <div className="flex items-center gap-1 text-[10px] px-2 py-0.5 rounded border text-text-muted border-bg-border">
          <RefreshCw className="w-2.5 h-2.5" />
          <span>定时 {nextRunStr}</span>
        </div>
      )}

      {/* Toast 提示 */}
      {toast && (
        <div className="absolute right-0 top-full mt-1.5 z-50 max-w-xs px-3 py-2 rounded-lg border border-warn/40 bg-bg-card shadow-lg text-xs text-warn">
          {toast}
        </div>
      )}

      {/* 触发按钮 */}
      <button
        onClick={() => setOpen(v => !v)}
        className={cn(
          'flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium transition-all border',
          anyRunning
            ? 'bg-accent/10 text-accent border-accent/30'
            : updateStatus?.status === 'done' || syncStatus?.status === 'done'
              ? 'bg-up/10 text-up border-up/30'
              : 'bg-bg-elevated text-text-secondary border-bg-border hover:text-text-primary hover:bg-bg-border',
        )}
      >
        {anyRunning
          ? <RefreshCw className="w-3 h-3 animate-spin" />
          : <Download className="w-3 h-3" />
        }
        {anyRunning ? '更新中…' : '更新数据'}
        <ChevronDown className={cn('w-3 h-3 transition-transform', open && 'rotate-180')} />
      </button>

      {/* 下拉面板 */}
      {open && (
        <div className="absolute right-0 top-full mt-1.5 z-50 w-[360px] rounded-lg border border-bg-border bg-bg-card shadow-xl overflow-hidden">
          {/* 面板标题 */}
          <div className="px-4 py-2.5 border-b border-bg-border/50 bg-bg-elevated/50">
            <p className="text-xs font-semibold text-text-primary">数据更新</p>
            <p className="text-[10px] text-text-muted mt-0.5">
              两个入口相互独立，可单独触发；建议每周运行一次板块同步
            </p>
          </div>

          {/* 每日数据更新 */}
          <JobPanel
            label="每日数据更新"
            icon={<Download className="w-3.5 h-3.5" />}
            status={updateStatus}
            isRunning={updateRunning}
            isDone={updateStatus?.status === 'done'}
            isError={updateStatus?.status === 'error'}
            onTrigger={handleUpdate}
            description="选股 API 获取候选股 → K线计算 → 更新强势池 → 刷新板块统计 → 写入复盘。每日收盘后运行。"
            estimatedTime="30 秒"
            locked={!isLoggedIn}
          />

          {/* 板块全量同步 */}
          <JobPanel
            label="板块全量同步"
            icon={<Layers className="w-3.5 h-3.5" />}
            status={syncStatus}
            isRunning={syncRunning}
            isDone={syncStatus?.status === 'done'}
            isError={syncStatus?.status === 'error'}
            onTrigger={handleSyncBoards}
            description="更新板块涨跌幅/换手率/市值等行情数据。成份股/关联变更请在板块配置页执行全量同步。"
            estimatedTime="30 秒"
            locked={!isLoggedIn}
          />

          {/* 耗时参考 */}
          <div className="px-4 py-2 bg-bg-elevated/30 border-t border-bg-border/30">
            <p className="text-[10px] text-text-muted leading-relaxed">
              <span className="text-text-secondary font-medium">耗时参考</span>（基于日志实测）：
              拉取行情 ~23s · K线 ~40s · 板块同步 ~50s · 其余步骤 &lt;2s
            </p>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Header ────────────────────────────────────────────────────────────────────

export function Header({ title }: { title: string }) {
  const { data } = useQuery({
    queryKey: ['market-state'],
    queryFn: fetchMarketState,
    staleTime: 60_000,
  })
  const { isLoggedIn, username, logout } = useAuthStore()
  const [showLogin, setShowLogin] = useState(false)

  return (
    <header className="h-14 flex items-center justify-between px-6 border-b border-bg-border bg-bg-card shrink-0">
      <h1 className="text-sm font-semibold text-text-primary">{title}</h1>

      {showLogin && <LoginModal onClose={() => setShowLogin(false)} />}

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

        <DataUpdateMenu />

        {isLoggedIn && (
          <NavLink
            to="/sector-config"
            title="板块配置"
            className={({ isActive }) => cn(
              'p-1.5 rounded transition-colors',
              isActive ? 'text-accent bg-accent/10' : 'text-text-muted hover:text-text-primary hover:bg-bg-elevated',
            )}
          >
            <Settings2 className="w-3.5 h-3.5" />
          </NavLink>
        )}

        {/* 登录/登出 */}
        {isLoggedIn ? (
          <div className="flex items-center gap-1.5">
            <span className="flex items-center gap-1 text-xs text-text-muted">
              <User className="w-3 h-3" />
              {username}
            </span>
            <button
              onClick={logout}
              title="退出登录"
              className="p-1.5 rounded text-text-muted hover:text-down hover:bg-bg-elevated transition-colors"
            >
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <button
            onClick={() => setShowLogin(true)}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded text-xs text-accent border border-accent/30 hover:bg-accent/10 transition-colors"
          >
            <LogIn className="w-3 h-3" />
            登录
          </button>
        )}
      </div>
    </header>
  )
}
