import client from './client'

export interface UpdateStatus {
  status: 'idle' | 'running' | 'done' | 'error'
  started_at: string | null
  finished_at: string | null
  message: string
  log_lines: string[]
}

export const triggerUpdate = (skipBoards = true) =>
  client.post<{ ok: boolean; message: string }>('/admin/update', null, {
    params: { skip_boards: skipBoards },
  }).then((r) => r.data)

export const fetchUpdateStatus = () =>
  client.get<UpdateStatus>('/admin/update/status').then((r) => r.data)
