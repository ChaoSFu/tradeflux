/**
 * useLeaderUniverseMaxes — 龙头标签的对比基准（取全集前二名）
 *
 * 基准集合 = 强势股池 + 涨停池 + 跌停池 合并去重后的「全集」。
 * 每个指标取全集内的前两个不同值：r1=最高（龙1，可能多只并列）、r2=次高（龙2）。
 * 「10/20/60龙、60高板龙」据此分出 龙1/龙2 两档，代表真正的市场级强度，
 * 而非在某个子列表（如当日跌停池）内比出虚高的"龙"。
 *
 * 复用 sector-analysis 的 queryKey（useSectorLeaders 已触发），零额外请求。
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchStrongPool, fetchLimitMoves } from '@/api/stocks'
import type { Stock } from '@/types'

export type MetricRanks = { r1: number; r2: number }  // 全集前二名的不同值（无则 0）
export type LeaderMaxes = {
  board: MetricRanks   // today_board_count（连板龙：仅取 r1）
  d10: MetricRanks
  d20: MetricRanks
  d60: MetricRanks
  high: MetricRanks    // board_count_60d（60高板龙）
}

function topTwo(vals: number[]): MetricRanks {
  const distinct = [...new Set(vals.filter((v) => v > 0))].sort((a, b) => b - a)
  return { r1: distinct[0] ?? 0, r2: distinct[1] ?? 0 }
}

/**
 * 龙头标签（10/20/60龙、60高板龙 分 龙1/龙2；连板龙仅 龙1 单层）。
 * 标签按指标顺序入数组，供展示与排序使用。
 */
export function getLeaderTags(stock: Stock, m: LeaderMaxes): string[] {
  const tags: string[] = []
  const tier = (val: number | undefined, r: MetricRanks, base: string) => {
    const v = val ?? 0
    if (v <= 0) return
    if (v === r.r1) tags.push(`${base}1`)
    else if (r.r2 > 0 && v === r.r2) tags.push(`${base}2`)
  }
  tier(stock.limit_up_days_10d, m.d10, '10龙')
  tier(stock.limit_up_days_20d, m.d20, '20龙')
  tier(stock.limit_up_days_60d, m.d60, '60龙')
  tier(stock.board_count_60d, m.high, '60高板龙')
  // 连板龙：今日连板高度最高者，单层（仅 r1）
  if ((stock.today_board_count ?? 0) > 0 && (stock.today_board_count ?? 0) === m.board.r1) {
    tags.push('连板龙')
  }
  return tags
}

// 龙头排序优先级：龙1 组整体在 龙2 组之前；组内 10>20>60>高板>连板
export const DRAGON_TAG_ORDER = [
  '10龙1', '20龙1', '60龙1', '60高板龙1', '连板龙',
  '10龙2', '20龙2', '60龙2', '60高板龙2',
] as const

/** 取股票龙头标签里的最高优先级序号（越小越靠前，无标签返回 Infinity）。 */
export function dragonPrimary(tags: string[]): number {
  let best = Infinity
  for (const t of tags) {
    const i = (DRAGON_TAG_ORDER as readonly string[]).indexOf(t)
    if (i >= 0 && i < best) best = i
  }
  return best
}

export function useLeaderUniverseMaxes(): LeaderMaxes {
  const { data: strong } = useQuery({
    queryKey: ['strong-pool-sector-analysis'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
  } as any)
  const { data: up } = useQuery({
    queryKey: ['limit-up-sector-analysis'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up' }),
  } as any)
  const { data: down } = useQuery({
    queryKey: ['limit-down-sector-analysis'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down' }),
  } as any)

  return useMemo(() => {
    const seen = new Set<number>()
    const merged: Stock[] = []
    for (const s of [
      ...((strong as any)?.items ?? []),
      ...((up as any)?.items ?? []),
      ...((down as any)?.items ?? []),
    ] as Stock[]) {
      if (!seen.has(s.id)) { seen.add(s.id); merged.push(s) }
    }
    return {
      board: topTwo(merged.map((s) => s.today_board_count ?? 0)),
      d10:   topTwo(merged.map((s) => s.limit_up_days_10d  ?? 0)),
      d20:   topTwo(merged.map((s) => s.limit_up_days_20d  ?? 0)),
      d60:   topTwo(merged.map((s) => s.limit_up_days_60d  ?? 0)),
      high:  topTwo(merged.map((s) => s.board_count_60d    ?? 0)),
    }
  }, [strong, up, down])
}
