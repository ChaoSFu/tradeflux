/**
 * Core Stock —— 三种不同的「核心」，它们是三件事，不该混成一个榜。
 *
 *   活跃龙头  高辨识度连板龙头，用生命周期状态机描述它走到哪了
 *   涨停核心  今天在涨停梯队里的，看的是当下的高度和可交易性
 *   趋势龙头  成交额承接得住的，看的是容量而不是弹性
 *
 * 三者的选股逻辑、风险和买点完全不同，V1 把它们混在同一个池子里排序，
 * 结果是"最强"这个词失去了含义。
 *
 * ## 生命周期直接复用后端，不在前端重算
 *
 * price_v1_1 是个透明状态机（不是黑箱分），后端 replay 出来带着入场原因。
 * 前端只做**中文显示和分组**，一条判定规则都不复制过来——同一个事实两套判定
 * 是这个仓库栽过 8 次的坑。
 */
import type { LifecycleState } from '@/api/stocks'

export const LIFECYCLE_ZH: Record<string, string> = {
  STREAKING: '连板中',
  BROKEN: '刚断板',
  REPAIRING: '修复中',
  CROSS_SUCCESS: '二波确认',
  CROSS_WEAKENING: '确认后走弱',
  CROSS_FAILED: '修复失败',
  FADED: '周期结束',
  UNKNOWN: '数据不足',
  NO_CYCLE: '无有效周期',
}

export type CoreGroup = 'focus' | 'turning' | 'downgraded' | 'pending'

/**
 * 分组。**「重点跟踪」不等于可以买**——它只表示"值得占用今天的注意力"。
 * 这一点在页面上必须写出来，否则分组名本身会变成买入暗示。
 */
export const GROUP_OF: Record<string, CoreGroup> = {
  STREAKING: 'focus',
  REPAIRING: 'focus',
  CROSS_SUCCESS: 'focus',
  BROKEN: 'turning',
  CROSS_WEAKENING: 'downgraded',
  CROSS_FAILED: 'downgraded',
  FADED: 'downgraded',
  UNKNOWN: 'pending',
  NO_CYCLE: 'pending',
}

export const GROUP_META: Record<CoreGroup, { label: string; hint: string }> = {
  focus: {
    label: '重点跟踪',
    hint: '连板中 / 修复中 / 二波确认。只是观察名单，不是买入信号 —— '
      + '生命周期回答"它在哪"，回答不了"能不能买"',
  },
  turning: {
    label: '转折观察',
    hint: '刚断板，结构还没演化。最多两个交易日（D+0/D+1）就必须表态',
  },
  downgraded: {
    label: '降级',
    hint: '确认后走弱 / 修复失败 / 周期结束。已从重点跟踪移出',
  },
  pending: {
    label: '数据待核',
    hint: '今天的价格事实不足以判断，或本地重算不出 ≥4 连板周期',
  },
}

export const groupOf = (st: LifecycleState | null | undefined): CoreGroup =>
  GROUP_OF[st ?? 'UNKNOWN'] ?? 'pending'

/** 核心股 gate：只有「重点跟踪」才算通过，其余都不是 BLOCK 而是 WAIT */
export function coreStockGate(st: LifecycleState | null | undefined) {
  const g = groupOf(st)
  const zh = LIFECYCLE_ZH[st ?? 'UNKNOWN'] ?? '未知'
  if (g === 'focus') return { status: 'PASS' as const, label: zh }
  if (g === 'downgraded') return { status: 'BLOCK' as const, label: zh }
  return { status: 'WAIT' as const, label: zh }
}
