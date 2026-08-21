import client from './client'
import type {
  W2SCandidate, W2SCandidateDetail, W2SRefreshStatus, W2SEvent, W2SConfig,
} from '@/types'

export const fetchW2SCandidates = (activeOnly = true) =>
  client.get<W2SCandidate[]>('/weak-to-strong-radar/candidates', { params: { active_only: activeOnly } })
    .then((r) => r.data)

export const fetchW2SCandidateDetail = (code: string) =>
  client.get<W2SCandidateDetail>(`/weak-to-strong-radar/candidates/${code}`).then((r) => r.data)

export const triggerW2SRefresh = () =>
  client.post<{ ok: boolean; message: string }>('/weak-to-strong-radar/refresh').then((r) => r.data)

export const fetchW2SRefreshStatus = () =>
  client.get<W2SRefreshStatus>('/weak-to-strong-radar/refresh/status').then((r) => r.data)

export const fetchW2SEvents = (stockCode?: string, limit = 200) =>
  client.get<W2SEvent[]>('/weak-to-strong-radar/events', { params: { stock_code: stockCode, limit } })
    .then((r) => r.data)

export const fetchW2SConfig = () =>
  client.get<W2SConfig>('/weak-to-strong-radar/config').then((r) => r.data)

export const updateW2SConfig = (key: string, value: string | null) =>
  client.put<W2SConfig>('/weak-to-strong-radar/config', { key, value }).then((r) => r.data)
