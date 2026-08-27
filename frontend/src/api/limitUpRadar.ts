import client from './client'
import type { LimitUpRadarResponse, LimitUpRadarRefreshResponse } from '@/types'

export interface LimitUpRadarParams {
  date?: string
  include_core?: boolean
  group_mode?: 'all_watched_sectors' | 'primary'
  /** 板块排序主键，次级键各不相同，见后端 SECTOR_SORT_KEYS */
  sector_sort?: SectorSortKey
}

export type SectorSortKey =
  | 'board_height'          // 最高连板（默认）
  | 'broken_streak_height'  // 最高断板
  | 'continuation_count'    // 连板个数
  | 'today_limit_up_count'  // 涨停个数

export const fetchLimitUpRadar = (params: LimitUpRadarParams = {}) =>
  client.get<LimitUpRadarResponse>('/limit-up-radar', { params }).then((r) => r.data)

/**
 * 启动刷新（后台执行，立即返回 running=true）。进度/结果查 fetchRefreshStatus。
 *
 * 为什么是异步：整个刷新实测约40秒（东财3个接口 + 100只K线重算），而 axios 全局
 * 超时是15秒——同步返回的话请求会被前端掐断，onSuccess 不触发、页面不刷新，但后台
 * 其实已经把活干完了，表现就是"点了没反应，过一会儿手动切换才出来"。
 *
 * 只同步涨停/炸板明细 + 核心召回 + 本页股票的分数重算，不触发 daily_update /
 * 全市场选股 / Market State / 弱转强雷达。
 */
export const refreshLimitUpDetails = (date?: string) =>
  client.post<LimitUpRadarRefreshResponse>('/limit-up-radar/refresh', null, { params: { date } })
    .then((r) => r.data)

export const fetchRefreshStatus = () =>
  client.get<LimitUpRadarRefreshResponse>('/limit-up-radar/refresh/status').then((r) => r.data)
