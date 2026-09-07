/**
 * 生命周期状态的**唯一**展示口径 —— 中文名、分组、以及「当日判不出时显示什么」。
 *
 * 2026-09-06 抽出来的。在此之前这套逻辑有两份：LeaderCyclePanel 一份、
 * StockPool 一份。结果是同一份数据在「强势股」tab 显示核心观察 8 只、在
 * 「全部」tab 显示未分类 59 只——因为我修 `last_valid_state` 回退时只改了前者。
 *
 * 这是本仓库第 N 次「同一个事实两套判定」。判定规则本身在后端（price_v1_1 是
 * 个透明状态机），前端只做展示映射，那也必须只有一份。
 */
import type { LeaderCycleItem, LifecycleState } from '@/api/stocks'

export const LIFECYCLE_ZH: Record<string, string> = {
  STREAKING: '连板中',
  BROKEN: '刚断板',
  REPAIRING: '修复中',
  CROSS_SUCCESS: '穿越成功',
  CROSS_WEAKENING: '成功后走弱',
  CROSS_FAILED: '修复失败',
  FADED: '周期结束',
  UNKNOWN: '数据不足',
  NO_CYCLE: '无有效周期',
}

/**
 * 用来分组和展示的状态。
 *
 * **盘前跑日更时，当日 bar 不是收盘终值，状态机按设计一律给 UNKNOWN**——那条
 * 规则是对的（盘中价不该推动跨日生命周期）。但界面不该因此把已知的也丢掉，
 * 所以退回 `last_valid_state`（后端一直保留着）。
 *
 * **这不是拿旧值冒充新值**：`isStale()` 会让调用方标出「昨收」和具体日期，
 * 而「今天还没收盘」本来就不该改变昨天的结论。
 */
export function shownState(
  r: Pick<LeaderCycleItem, 'lifecycle_state' | 'last_valid_state'>,
): LifecycleState {
  return (r.lifecycle_state && r.lifecycle_state !== 'UNKNOWN'
    ? r.lifecycle_state
    : (r.last_valid_state ?? 'UNKNOWN'))
}

/** 显示的是不是「截至上一个已结算交易日」的旧结论 */
export const isStale = (
  r: Pick<LeaderCycleItem, 'lifecycle_state' | 'last_valid_state'>,
) => r.lifecycle_state === 'UNKNOWN' && !!r.last_valid_state

// ── 分组 ───────────────────────────────────────────────────────────────────
export type CoreGroup = 'core' | 'waiting' | 'dropped' | 'pending'

export const GROUP_OF: Record<string, CoreGroup> = {
  REPAIRING: 'core',
  CROSS_SUCCESS: 'core',
  STREAKING: 'waiting',
  BROKEN: 'waiting',
  CROSS_WEAKENING: 'dropped',
  CROSS_FAILED: 'dropped',
  FADED: 'dropped',
  UNKNOWN: 'pending',
  NO_CYCLE: 'pending',
}

export const GROUP_META: Record<CoreGroup, { label: string; hint: string }> = {
  core: {
    label: '核心观察',
    hint: '第一次转强（修复中）+ 已完成二波结构（穿越成功）。'
      + '这里只是观察名单，买点由人确认',
  },
  waiting: {
    label: '待观察',
    hint: '还没进场：仍在连板中，或刚断板、结构未演化',
  },
  dropped: {
    label: '已剔除',
    hint: '已从核心池剔除：成功后走弱 / 修复失败 / 周期结束',
  },
  pending: {
    label: '数据待核',
    hint: '今天的价格事实不足以判断，或本地重算不出 ≥4 连板周期',
  },
}

export const groupOf = (st: string | null | undefined): CoreGroup =>
  GROUP_OF[st ?? 'UNKNOWN'] ?? 'pending'

/** 按展示口径分组一批股票 */
export function groupByLifecycle<T extends Pick<LeaderCycleItem,
  'lifecycle_state' | 'last_valid_state'>>(rows: T[]): Record<CoreGroup, T[]> {
  const g: Record<CoreGroup, T[]> = { core: [], waiting: [], dropped: [], pending: [] }
  rows.forEach((r) => g[groupOf(shownState(r))].push(r))
  return g
}

/** 生命周期推进方向。分段、排序都用它，不另维护第二个顺序表 */
export const STATE_ORDER: LifecycleState[] = [
  'STREAKING', 'BROKEN', 'REPAIRING', 'CROSS_SUCCESS',
  'CROSS_WEAKENING', 'CROSS_FAILED', 'FADED', 'UNKNOWN', 'NO_CYCLE',
]
