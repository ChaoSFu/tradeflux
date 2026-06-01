import client from './client'

export interface UpdateStatus {
  status: 'idle' | 'running' | 'done' | 'error'
  started_at: string | null
  finished_at: string | null
  message: string
  log_lines: string[]
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
export const triggerSyncBoards = () =>
  client.post<{ ok: boolean; message: string }>('/admin/sync-boards').then((r) => r.data)

export const fetchSyncBoardsStatus = () =>
  client.get<UpdateStatus>('/admin/sync-boards/status').then((r) => r.data)
