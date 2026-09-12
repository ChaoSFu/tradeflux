import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, CheckCircle, ChevronDown, ChevronUp, Clock, Copy, Check as CheckIcon,
  ExternalLink, Inbox, Lock, Play, RefreshCw, Stethoscope, XCircle,
} from 'lucide-react'
import {
  EXPORT_SCRIPT_URL, fetchAuditJob, fetchAuditReport, fetchExportScript, fetchInbox, runAudit, runFix,
} from '@/api/dataAudit'
import type { AuditCheck, AuditItem, AuditJob, AuditReport, AuditStatus } from '@/api/dataAudit'
import { triggerUpdate } from '@/api/admin'
import { Card } from '@/components/ui/card'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { LoginModal } from '@/components/auth/LoginModal'
import { useAuthStore } from '@/store/auth'
import { cn } from '@/utils/cn'

// 数据体检：各张按日写的表缺了什么、为什么缺、怎么补。
// 检测只读（每次日更后自动跑一次，周六再兜一次）；补数一律登录后先试跑、再确认，补完后端自动复查。

const STATUS_META: Record<AuditStatus, { label: string; cls: string; icon: React.ElementType }> = {
  error:   { label: '检测出错', cls: 'text-danger bg-danger/10 border-danger/30', icon: XCircle },
  gap:     { label: '要补',     cls: 'text-danger bg-danger/10 border-danger/30', icon: AlertTriangle },
  warn:    { label: '提醒',     cls: 'text-warn bg-warn/10 border-warn/30', icon: AlertTriangle },
  expired: { label: '补不回来', cls: 'text-text-muted bg-bg-elevated border-bg-border', icon: Clock },
  ok:      { label: '正常',     cls: 'text-safe bg-safe/10 border-safe/30', icon: CheckCircle },
}

type Start = (fn: () => Promise<{ ok: boolean; message: string }>) => void

function fmtTime(iso?: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

// 生产站点是 http，不是安全上下文，navigator.clipboard 用不了——退回 execCommand
function copyText(text: string) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text)
  }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.focus()
  ta.select()
  const ok = document.execCommand('copy')
  document.body.removeChild(ta)
  return ok ? Promise.resolve() : Promise.reject(new Error('浏览器不让复制'))
}

function StatusBadge({ status }: { status: AuditStatus }) {
  const m = STATUS_META[status]
  const Icon = m.icon
  return (
    <span className={cn('inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border shrink-0', m.cls)}>
      <Icon className="w-3 h-3" />{m.label}
    </span>
  )
}

function ActionButton({
  children, onClick, disabled, primary, locked, onLogin, title,
}: {
  children: React.ReactNode
  onClick: () => void
  disabled?: boolean
  primary?: boolean
  locked?: boolean
  onLogin?: () => void
  title?: string
}) {
  if (locked) {
    return (
      <button
        onClick={onLogin}
        className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded text-warn bg-warn/10 border border-warn/30 hover:bg-warn/20 transition-colors"
        title="登录后才能操作"
      >
        <Lock className="w-3 h-3" />需要登录
      </button>
    )
  }
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        'inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded border transition-colors disabled:opacity-40 disabled:cursor-not-allowed',
        primary
          ? 'text-accent bg-accent/10 border-accent/30 hover:bg-accent/20'
          : 'text-text-secondary bg-bg-elevated border-bg-border hover:text-text-primary',
      )}
    >
      {children}
    </button>
  )
}

function JobLog({ job, report }: { job: AuditJob; report?: AuditReport | null }) {
  const [open, setOpen] = useState(true)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight
  }, [job.log_lines])
  const title = report?.checks.find((c) => c.id === job.check_id)?.title ?? job.check_id
  const what = job.kind === 'audit'
    ? '检测'
    : `${job.apply ? '补数' : '试跑'}：${title}${job.file ? `（${job.file}）` : ''}`
  return (
    <div className="mt-3 border border-bg-border rounded">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-2 px-3 py-1.5 text-xs"
      >
        <span className={cn(
          'flex items-center gap-1.5 min-w-0 truncate',
          job.status === 'error' ? 'text-danger' : job.status === 'running' ? 'text-accent' : 'text-text-secondary',
        )}>
          {job.status === 'running' && <RefreshCw className="w-3 h-3 animate-spin shrink-0" />}
          {what} · {job.status === 'running' ? '进行中…' : job.message}
          {job.finished_at && <span className="text-text-muted">（{fmtTime(job.finished_at)}）</span>}
        </span>
        {open ? <ChevronUp className="w-3 h-3 shrink-0" /> : <ChevronDown className="w-3 h-3 shrink-0" />}
      </button>
      {open && (
        <div
          ref={ref}
          className="max-h-72 overflow-auto border-t border-bg-border bg-bg-base px-3 py-2 font-mono text-[11px] leading-relaxed text-text-secondary whitespace-pre-wrap"
        >
          {job.log_lines.length ? job.log_lines.join('\n') : '（还没有输出）'}
        </div>
      )}
    </div>
  )
}

function DateChips({ label, dates }: { label: string; dates: string[] }) {
  const [all, setAll] = useState(false)
  const shown = all ? dates : dates.slice(0, 12)
  return (
    <div className="text-[11px] leading-relaxed">
      <span className="text-text-muted mr-1.5">{label}（{dates.length}）：</span>
      {shown.map((d) => (
        <span key={d} title={d} className="inline-block mr-1 mb-1 px-1.5 py-0.5 rounded bg-bg-elevated font-mono text-text-secondary">
          {d.slice(5)}
        </span>
      ))}
      {dates.length > 12 && (
        <button onClick={() => setAll((v) => !v)} className="text-accent">
          {all ? '收起' : `全部 ${dates.length} 天`}
        </button>
      )}
    </div>
  )
}

function describeItem(c: AuditCheck, it: AuditItem) {
  const brief = (ds: string[], n: number) =>
    ds.slice(0, n).map((d) => d.slice(5)).join('、') + (ds.length > n ? '…' : '')
  if (c.id === 'sector_index') {
    const holes = it.holes ?? []
    return it.reason === '有洞'
      ? `${it.bars ?? 0} 根，窗口里缺 ${holes.length} 天：${brief(holes, 5)}`
      : `${it.bars ?? 0} 根（不足 ${c.min_bars ?? 70} 根）`
  }
  if (it.missing_dates) return `缺 ${it.missing} 天：${brief(it.missing_dates, 6)}`
  if (it.missing != null) return `缺 ${it.missing} 天`
  return ''
}

function ItemsTable({ c }: { c: AuditCheck }) {
  const [all, setAll] = useState(false)
  const items = c.items ?? []
  const shown = all ? items : items.slice(0, 8)
  const label = c.id === 'sector_index'
    ? `要补的板块（${items.length}）`
    : c.id === 'stock_snapshots'
      ? `缺得最多的股票（前 ${items.length} 只）`
      : `明细（${items.length}）`
  return (
    <div className="mt-1">
      <p className="text-[11px] text-text-muted mb-1">{label}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px]">
          <tbody>
            {shown.map((it) => (
              <tr key={it.code} className="border-t border-bg-border/40">
                <td className="py-1 pr-3 font-mono text-text-secondary whitespace-nowrap">{it.code}</td>
                <td className="py-1 pr-3 text-text-primary whitespace-nowrap">{it.name}</td>
                <td className="py-1 text-text-muted">{describeItem(c, it)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {items.length > 8 && (
        <button onClick={() => setAll((v) => !v)} className="mt-1 text-[11px] text-accent">
          {all ? '收起' : `展开全部 ${items.length} 个`}
        </button>
      )}
    </div>
  )
}

function Details({ c }: { c: AuditCheck }) {
  const rows: [string, string[] | undefined][] = [
    ['缺的日子', c.missing_dates],
    ['整天没有快照', c.empty_days],
    ['两融缺', c.margin_missing],
    ['成交额缺', c.amount_missing],
    ['已过当天、补不回来', c.expired_dates],
  ]
  const shown = rows.filter(([, v]) => v && v.length > 0)
  const counts = c.counts
  if (!shown.length && !(c.items && c.items.length) && !counts) return null
  return (
    <div className="mt-2 space-y-1.5">
      {counts && c.id === 'stock_snapshots' && (
        <p className="text-[11px] text-text-muted">
          关注 {counts.tracked?.toLocaleString()} 只，存档里有 {counts.in_archive?.toLocaleString()} 只；
          缺 {counts.missing_rows?.toLocaleString()} 行，成交量可补 {counts.volume_fillable?.toLocaleString()} 行，
          收盘价为空 {counts.null_close_rows?.toLocaleString()} 行（已有行不覆盖，只列出来）
        </p>
      )}
      {shown.map(([label, v]) => <DateChips key={label} label={label} dates={v!} />)}
      {c.items && c.items.length > 0 && <ItemsTable c={c} />}
    </div>
  )
}

function ExportSteps({
  c, report, running, isLoggedIn, onLogin, start,
}: {
  c: AuditCheck
  report: AuditReport
  running: boolean
  isLoggedIn: boolean
  onLogin: () => void
  start: Start
}) {
  const n = c.items?.length ?? 0
  const [copied, setCopied] = useState<string | null>(null)
  const [err, setErr] = useState<string | null>(null)
  // 预先拉好脚本：点「复制」时同步写剪贴板（http 站点只能用 execCommand，它要求在点击的当下执行）
  const { data: script } = useQuery({
    queryKey: ['data-audit-export-script', report.updated_at ?? report.generated_at],
    queryFn: fetchExportScript,
    enabled: n > 0,
    staleTime: 60_000,
  })
  const { data: inbox } = useQuery({
    queryKey: ['data-audit-inbox'],
    queryFn: fetchInbox,
    enabled: isLoggedIn,
    refetchInterval: 30_000,
  })
  const scp = `scp ~/Desktop/sector_klines_*.jsonl ${report.ssh_user}@${window.location.hostname}:${report.inbox_dir}/`

  const doCopy = (key: string, text?: string) => {
    if (!text) { setErr('脚本还没加载好，稍等一下再点'); return }
    copyText(text)
      .then(() => { setCopied(key); setErr(null); setTimeout(() => setCopied(null), 2500) })
      .catch((e: Error) => setErr(`复制失败（${e.message}）——可以点「新标签页打开」手动全选复制`))
  }

  const CopyBtn = ({ k, text }: { k: string; text?: string }) => (
    <button
      onClick={() => doCopy(k, text)}
      className="inline-flex items-center gap-1 ml-2 text-[11px] px-1.5 py-0.5 rounded border border-bg-border bg-bg-elevated text-text-secondary hover:text-text-primary"
    >
      {copied === k ? <CheckIcon className="w-3 h-3 text-safe" /> : <Copy className="w-3 h-3" />}
      {copied === k ? '已复制' : '复制'}
    </button>
  )

  return (
    <div className="mt-2 text-xs text-text-secondary">
      <ol className="space-y-2.5 list-decimal pl-5 leading-relaxed">
        <li>
          复制导出脚本（已填好这 {n} 个板块）
          <CopyBtn k="script" text={script} />
          <a href={EXPORT_SCRIPT_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 ml-2 text-[11px] text-accent">
            新标签页打开<ExternalLink className="w-3 h-3" />
          </a>
        </li>
        <li>
          打开
          <a href="https://quote.eastmoney.com/center/boardlist.html" target="_blank" rel="noreferrer" className="text-accent mx-1">东财行情页</a>
          ，先在页面空白处点一下（免得浏览器拦「下载多个文件」），再 F12 → Console → 粘贴 → 回车。
          <span className="text-text-muted">
            起步 15 秒一个，被掐会自己等 10～30 分钟、自己放慢，不用管；电脑别睡、标签页别关。
            Console 里输入 __tfStatus() 看进度。
          </span>
        </li>
        <li>
          文件下好后，在你的电脑上传到服务器收件箱（下载目录不是桌面就改一下路径）：
          <div className="mt-1 flex items-start gap-1">
            <code className="flex-1 min-w-0 overflow-x-auto whitespace-nowrap rounded bg-bg-base border border-bg-border px-2 py-1 font-mono text-[11px] text-text-primary">
              {scp}
            </code>
            <CopyBtn k="scp" text={scp} />
          </div>
        </li>
        <li>
          收件箱里的文件：先试跑导入看数字，再确认导入（已有行一律不覆盖；导完文件挪进 done/，并自动复查这一项）。
          <div className="mt-1.5 space-y-1.5">
            {!isLoggedIn ? (
              <ActionButton locked onLogin={onLogin} onClick={() => {}}>需要登录</ActionButton>
            ) : !inbox ? (
              <span className="text-text-muted">加载中…</span>
            ) : inbox.files.length === 0 ? (
              <span className="inline-flex items-center gap-1 text-text-muted"><Inbox className="w-3 h-3" />还没有文件</span>
            ) : inbox.files.map((f) => (
              <div key={f.name} className="flex items-center gap-2 flex-wrap">
                <span className="font-mono text-[11px] text-text-primary">{f.name}</span>
                <span className="text-[10px] text-text-muted">{(f.size / 1048576).toFixed(1)}MB · {fmtTime(f.modified)}</span>
                <ActionButton disabled={running} onClick={() => start(() => runFix('sector_index', false, f.name))}>
                  <Play className="w-3 h-3" />试跑导入
                </ActionButton>
                <ActionButton
                  primary
                  disabled={running}
                  onClick={() => {
                    if (window.confirm(`确认导入 ${f.name}？会写数据库（已有行一律不覆盖）。`)) {
                      start(() => runFix('sector_index', true, f.name))
                    }
                  }}
                >
                  确认导入
                </ActionButton>
              </div>
            ))}
          </div>
        </li>
      </ol>
      {err && <p className="mt-2 text-[11px] text-warn">{err}</p>}
    </div>
  )
}

function FixArea({
  c, report, job, running, isLoggedIn, onLogin, start, onRerun,
}: {
  c: AuditCheck
  report: AuditReport
  job?: AuditJob
  running: boolean
  isLoggedIn: boolean
  onLogin: () => void
  start: Start
  onRerun: () => void
}) {
  const f = c.fix
  if (!f || (!f.label && !f.note)) return null
  const mine = job && job.kind === 'fix' && job.check_id === c.id
  return (
    <div className="mt-3 pt-3 border-t border-bg-border/40">
      {f.label && (
        <p className="text-xs text-text-secondary">
          <span className="text-text-muted">补法：</span>{f.label}
        </p>
      )}
      {f.note && <p className="text-[11px] text-text-muted mt-0.5 leading-relaxed">{f.note}</p>}

      {f.kind === 'server' && (
        <div className="mt-2 flex items-center gap-2 flex-wrap">
          <ActionButton
            locked={!isLoggedIn}
            onLogin={onLogin}
            disabled={running}
            title="只列出将补什么，不写库"
            onClick={() => start(() => runFix(c.id, false))}
          >
            <Play className="w-3 h-3" />试跑
          </ActionButton>
          {isLoggedIn && (
            <ActionButton
              primary
              disabled={running}
              onClick={() => {
                if (window.confirm(`确认补「${c.title}」？会写数据库，补完自动复查。`)) start(() => runFix(c.id, true))
              }}
            >
              确认补上
            </ActionButton>
          )}
          {mine && (
            <span className={cn('text-[11px]', job!.status === 'error' ? 'text-danger' : 'text-text-muted')}>
              {job!.status === 'running' ? `${job!.apply ? '补数' : '试跑'}中…（日志在上面）` : `上次${job!.apply ? '补数' : '试跑'}：${job!.message}`}
            </span>
          )}
        </div>
      )}

      {f.kind === 'rerun_update' && (
        <div className="mt-2">
          <ActionButton locked={!isLoggedIn} onLogin={onLogin} onClick={onRerun}>
            <RefreshCw className="w-3 h-3" />重跑今天的日更
          </ActionButton>
        </div>
      )}

      {f.kind === 'local_export' && (
        <ExportSteps c={c} report={report} running={running} isLoggedIn={isLoggedIn} onLogin={onLogin} start={start} />
      )}
    </div>
  )
}

function CheckCard(props: {
  c: AuditCheck
  report: AuditReport
  job?: AuditJob
  running: boolean
  isLoggedIn: boolean
  onLogin: () => void
  start: Start
  onRerun: () => void
}) {
  const { c } = props
  return (
    <Card>
      <div className="flex items-start gap-3">
        <StatusBadge status={c.status} />
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-2 flex-wrap">
            <h3 className="text-sm font-semibold text-text-primary">{c.title}</h3>
            <span className="text-[10px] text-text-muted font-mono">{c.table}</span>
            <span className="text-[10px] text-text-muted ml-auto">查于 {fmtTime(c.checked_at)}</span>
          </div>
          <p className="text-xs text-text-primary mt-1 leading-relaxed">{c.summary}</p>
          {c.status !== 'ok' && <p className="text-[11px] text-text-muted mt-1">为什么会缺：{c.why}</p>}
          {c.status !== 'ok' && <Details c={c} />}
          {c.status !== 'ok' && <FixArea {...props} />}
        </div>
      </div>
    </Card>
  )
}

function Group({
  title, hint, count, defaultOpen, children,
}: {
  title: string
  hint?: string
  count: number
  defaultOpen: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  if (!count) return null
  return (
    <section>
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-xs font-semibold text-text-secondary mb-2 hover:text-text-primary"
      >
        {open ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        {title}（{count}）
        {hint && <span className="font-normal text-text-muted">{hint}</span>}
      </button>
      {open && <div className="space-y-3">{children}</div>}
    </section>
  )
}

export default function DataHealth() {
  const qc = useQueryClient()
  const { isLoggedIn } = useAuthStore()
  const [showLogin, setShowLogin] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  const { data: report, isLoading } = useQuery({
    queryKey: ['data-audit-report'],
    queryFn: fetchAuditReport,
    staleTime: 30_000,
  })
  const { data: job } = useQuery({
    queryKey: ['data-audit-job'],
    queryFn: fetchAuditJob,
    refetchInterval: (q) => (q.state.data?.status === 'running' ? 1500 : false),
  })
  const running = job?.status === 'running'

  // 任务从进行中变成完成 / 出错：报告可能刚被复查改写、收件箱文件可能被挪走，重拉
  const prevStatus = useRef(job?.status)
  useEffect(() => {
    if (prevStatus.current === 'running' && job && job.status !== 'running') {
      qc.invalidateQueries({ queryKey: ['data-audit-report'] })
      qc.invalidateQueries({ queryKey: ['data-audit-inbox'] })
    }
    prevStatus.current = job?.status
  }, [job, qc])

  const start: Start = (fn) => {
    if (!isLoggedIn) { setShowLogin(true); return }
    fn()
      .then((r) => {
        setNotice(r.ok ? null : r.message)
        qc.invalidateQueries({ queryKey: ['data-audit-job'] })
      })
      .catch((e: Error) => setNotice(e.message))
  }
  const onRerun = () => {
    if (!isLoggedIn) { setShowLogin(true); return }
    triggerUpdate()
      .then((r) => setNotice(r.ok ? '今天的日更已启动，进度看顶栏「数据更新」；跑完会自动再体检一次' : r.message))
      .catch((e: Error) => setNotice(e.message))
  }

  const checks = report?.checks ?? []
  // 真正要补的排前面：检测出错 → 缺口 → 提醒（同级内保持后端注册顺序，sort 是稳定的）
  const rank: Record<AuditStatus, number> = { error: 0, gap: 1, warn: 2, expired: 3, ok: 4 }
  const todo = checks
    .filter((c) => c.status === 'error' || c.status === 'gap' || c.status === 'warn')
    .sort((a, b) => rank[a.status] - rank[b.status])
  const expired = checks.filter((c) => c.status === 'expired')
  const ok = checks.filter((c) => c.status === 'ok')
  const cardProps = {
    report: report!, job, running, isLoggedIn, start, onRerun,
    onLogin: () => setShowLogin(true),
  }
  const s = report?.summary

  return (
    <div className="space-y-4 max-w-5xl">
      {showLogin && <LoginModal onClose={() => setShowLogin(false)} />}

      <Card
        title="数据体检"
        action={
          <ActionButton
            locked={!isLoggedIn}
            onLogin={() => setShowLogin(true)}
            disabled={running}
            onClick={() => start(runAudit)}
          >
            <Stethoscope className="w-3 h-3" />{running && job?.kind === 'audit' ? '检测中…' : '立即检测'}
          </ActionButton>
        }
      >
        <p className="text-xs text-text-secondary leading-relaxed">
          每次日更跑完会自动体检一次，周六 11:00 再兜一次。检测只读，不改任何数据；
          要补的缺口登录后先「试跑」看将补什么，再「确认」，补完自动复查。
        </p>
        {report && (
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-text-muted">
            <span>检测于 {fmtTime(report.updated_at ?? report.generated_at)}</span>
            <span>截止 {report.through ?? '—'}</span>
            <span>窗口 {report.window.days} 个交易日（{report.window.start} ~ {report.window.end}）</span>
          </div>
        )}
        {s && (
          <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
            <span className={cn('px-2 py-0.5 rounded border', s.todo ? STATUS_META.gap.cls : STATUS_META.ok.cls)}>待处理 {s.todo}</span>
            <span className={cn('px-2 py-0.5 rounded border', STATUS_META.warn.cls)}>提醒 {s.warn}</span>
            <span className={cn('px-2 py-0.5 rounded border', STATUS_META.expired.cls)}>补不回来 {s.expired}</span>
            <span className={cn('px-2 py-0.5 rounded border', STATUS_META.ok.cls)}>正常 {s.ok}</span>
          </div>
        )}
        {notice && <p className="mt-2 text-xs text-warn">{notice}</p>}
        {job && job.status !== 'idle' && <JobLog job={job} report={report} />}
      </Card>

      {isLoading && <LoadingSpinner />}
      {!isLoading && !report && (
        <Card>
          <p className="text-xs text-text-muted">还没检测过。登录后点「立即检测」，或者等今天日更跑完自动体检。</p>
        </Card>
      )}

      {report && (
        <>
          <Group title="需要处理" count={todo.length} defaultOpen>
            {todo.map((c) => <CheckCard key={c.id} c={c} {...cardProps} />)}
          </Group>
          {todo.length === 0 && (
            <Card>
              <p className="text-xs text-safe flex items-center gap-1.5"><CheckCircle className="w-3.5 h-3.5" />没有要处理的缺口</p>
            </Card>
          )}
          <Group title="补不回来（只记录）" hint=" · 只能当天拍的数据，过了当天就没了" count={expired.length} defaultOpen={false}>
            {expired.map((c) => <CheckCard key={c.id} c={c} {...cardProps} />)}
          </Group>
          <Group title="正常" count={ok.length} defaultOpen={false}>
            {ok.map((c) => <CheckCard key={c.id} c={c} {...cardProps} />)}
          </Group>
        </>
      )}
    </div>
  )
}
