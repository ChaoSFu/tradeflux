import client from './client'

export interface UpdateStatus {
  status: 'idle' | 'running' | 'done' | 'error'
  started_at: string | null
  finished_at: string | null
  message: string
  log_lines: string[]
  mode?: 'meta' | 'full' | null   // 板块同步专用：区分行情同步和全量同步
  degraded?: boolean              // true=有数据源API降级，数据可能不完整/过时
  warnings?: string[]
}

// ── 每日数据更新 ──────────────────────────────────────────────────────────────
// 流程：拉取全市场行情 → K线计算 → 更新强势股池 → 刷新板块统计 → 写入复盘
// 耗时：正常约 1-2 分钟；首次运行（大量板块关联补全）约 4-5 分钟
export const triggerUpdate = (skipBoards = true) =>
  client.post<{ ok: boolean; message: string }>('/admin/update', null, {
    params: { skip_boards: skipBoards },
  }).then((r) => r.data)

export const fetchUpdateStatus = () =>
  client.get<UpdateStatus>('/admin/update/status').then((r) => r.data)

// ── 板块全量同步 ──────────────────────────────────────────────────────────────
// 从东财拉取概念/行业/地区全部板块及成员，建立 stock_sector_relations
// 耗时：约 5-8 分钟（887 个板块，每板块一次 API）
// 建议频率：每周运行一次，或板块新增/成员大变动后手动触发
// meta_only=true：仅更新涨跌幅/换手/市值（~30s，每日调用）
// meta_only=false：全量同步含成份股数量+关联（~5-8min，每周一次）
export const triggerSyncBoards = (metaOnly = false) =>
  client.post<{ ok: boolean; message: string }>('/admin/sync-boards', null, {
    params: { meta_only: metaOnly },
  }).then((r) => r.data)

export const fetchSyncBoardsStatus = () =>
  client.get<UpdateStatus>('/admin/sync-boards/status').then((r) => r.data)

// ── 内置调度器状态 ────────────────────────────────────────────────────────────
export interface SchedulerStatus {
  running: boolean
  next_run: string | null   // ISO 时间字符串
  job_id?: string
  message?: string
}

export const fetchSchedulerStatus = () =>
  client.get<SchedulerStatus>('/admin/scheduler/status').then((r) => r.data)

// ── 最后一次更新结果（持久化）────────────────────────────────────────────────
export interface LastUpdateStatus {
  source: 'manual' | 'scheduled' | null
  status: 'done' | 'error' | null
  started_at: string | null
  finished_at: string | null
  message: string | null
  degraded?: boolean              // true=有数据源API降级，数据可能不完整/过时
  warnings?: string[]
}

export const fetchLastUpdateStatus = () =>
  client.get<LastUpdateStatus>('/admin/update/last').then((r) => r.data)

// ── 选股 API prompt 配置（强势池 / 涨跌停池）───────────────────────────────────
export interface PoolPrompts {
  strong_pool_keyword: string
  limit_move_keyword: string
  is_strong_custom: boolean
  is_limit_custom: boolean
  default_strong_pool_keyword: string
  default_limit_move_keyword: string
}

export const fetchPoolPrompts = () =>
  client.get<PoolPrompts>('/admin/pool-prompts').then((r) => r.data)

export const updatePoolPrompts = (payload: {
  strong_pool_keyword?: string | null
  limit_move_keyword?: string | null
}) => client.put<PoolPrompts>('/admin/pool-prompts', payload).then((r) => r.data)
