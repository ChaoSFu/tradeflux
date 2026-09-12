import client from './client'

// ── 数据体检（后端 /admin/data-audit，报告由 scripts/data_audit.py 生成）──────────
// ok 正常 / gap 可补的缺口 / expired 过了当天补不回来（只记录）/ warn 提醒 / error 检测本身出错
export type AuditStatus = 'ok' | 'gap' | 'expired' | 'warn' | 'error'
// server 服务器上一键补 / local_export 本机导出再导入 / rerun_update 重跑今天的日更 / none 没有补法
export type FixKind = 'server' | 'local_export' | 'rerun_update' | 'none'

export interface AuditFix {
  kind: FixKind
  label: string
  note: string
}

export interface AuditItem {
  code: string
  name?: string
  missing?: number
  missing_dates?: string[]
  bars?: number
  holes?: string[]
  reason?: string
}

export interface AuditCheck {
  id: string
  title: string
  table: string
  why: string
  status: AuditStatus
  summary: string
  fix: AuditFix
  checked_at: string
  missing_dates?: string[]
  expired_dates?: string[]
  empty_days?: string[]
  margin_missing?: string[]
  amount_missing?: string[]
  items?: AuditItem[]
  counts?: Record<string, number>
  no_data?: string[]
  today_missing?: number
  watched?: number
  min_bars?: number
  first_date?: string
}

export interface AuditReport {
  generated_at: string
  updated_at?: string          // 补完复查只替换那一项时写
  through: string | null       // 截止日：最后一个「应该已经有数据」的交易日
  window: { start: string | null; end: string | null; days: number }
  calendar: { source: string; last: string | null; behind: boolean | null; is_trading_day: boolean | null }
  checks: AuditCheck[]
  summary: Record<AuditStatus | 'todo', number>
  inbox_dir: string            // 服务器收件箱绝对路径（scp 目标）
  ssh_user: string
}

export interface AuditJob {
  status: 'idle' | 'running' | 'done' | 'error'
  kind: 'audit' | 'fix' | null
  check_id: string | null
  apply: boolean
  file: string | null
  started_at: string | null
  finished_at: string | null
  message: string
  log_lines: string[]
}

export interface InboxFile {
  name: string
  size: number
  modified: string
}

type StartResult = { ok: boolean; message: string }

export const fetchAuditReport = () =>
  client.get<{ report: AuditReport | null }>('/admin/data-audit').then((r) => r.data.report)

export const fetchAuditJob = () =>
  client.get<AuditJob>('/admin/data-audit/job').then((r) => r.data)

export const runAudit = () =>
  client.post<StartResult>('/admin/data-audit/run').then((r) => r.data)

// apply=false 试跑（只列出将补什么），apply=true 真的补，补完后端自动复查这一项
export const runFix = (checkId: string, apply: boolean, file?: string) =>
  client.post<StartResult>(`/admin/data-audit/fix/${checkId}`, null, {
    params: { apply, file },
  }).then((r) => r.data)

export const EXPORT_SCRIPT_URL = '/api/admin/data-audit/sector-index/export-script'

export const fetchExportScript = () =>
  client.get<string>('/admin/data-audit/sector-index/export-script', {
    responseType: 'text',
    transformResponse: (d) => d,
  }).then((r) => r.data)

export const fetchInbox = () =>
  client.get<{ dir: string; files: InboxFile[] }>('/admin/data-audit/inbox').then((r) => r.data)
