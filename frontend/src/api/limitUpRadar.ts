import client from './client'
import type { LimitUpRadarResponse, LimitUpRadarRefreshResponse } from '@/types'

export interface LimitUpRadarParams {
  date?: string
  include_core?: boolean
  group_mode?: 'all_watched_sectors' | 'primary'
}

export const fetchLimitUpRadar = (params: LimitUpRadarParams = {}) =>
  client.get<LimitUpRadarResponse>('/limit-up-radar', { params }).then((r) => r.data)

/**
 * 只同步涨停/炸板明细，不触发 daily_update / 全量K线 / Market State / 弱转强雷达。
 * 后端同步执行（3个轻量外部请求），失败时返回 ok=false 而不是抛错——页面继续
 * 显示上一份数据并标注刷新失败，不伪装成最新。
 */
export const refreshLimitUpDetails = (date?: string) =>
  client.post<LimitUpRadarRefreshResponse>('/limit-up-radar/refresh', null, { params: { date } })
    .then((r) => r.data)
