/**
 * useLeaderUniverseMaxes — 龙头标签的对比基准（取全集前二名；区间涨幅龙取前三名）
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
/** 区间涨幅前三名的不同值（降序，只取 >0 的；不足三个就少几个） */
export type GainRanks = number[]
export type LeaderMaxes = {
  board: MetricRanks   // today_board_count（连板龙：仅取 r1）
  d10: MetricRanks
  d20: MetricRanks
  d60: MetricRanks
  high: MetricRanks    // board_count_60d（60高板龙）
  // 区间涨幅龙（5/10/20/60 日，龙1~龙3）。跟上面「10龙/20龙/60龙」不是一回事：那几个比的是
  // 涨停天数，这几个比的是累计涨幅（跟表里「N日涨幅」列同一个数）
  gain: { d5: GainRanks; d10: GainRanks; d20: GainRanks; d60: GainRanks }
}

function topThreeGain(vals: (number | null | undefined)[]): GainRanks {
  return [...new Set(vals.filter((v): v is number => v != null && v > 0))].sort((a, b) => b - a).slice(0, 3)
}

/**
 * 区间涨幅龙：60/20/10/5 日累计涨幅在全集里排前三（并列同名次）。只展示，**不并入
 * getLeaderTags**——那份决定「总龙头」分组，强势股概览也在用，不能顺手改了它的成员。
 */
export const GAIN_WINDOWS = [
  ['d60', 'pct_change_60d', '60'], ['d20', 'pct_change_20d', '20'],
  ['d10', 'pct_change_10d', '10'], ['d5', 'pct_change_5d', '5'],
] as const

export function getGainLeaderTags(stock: Stock, m: LeaderMaxes): string[] {
  const tags: string[] = []
  for (const [k, field, n] of GAIN_WINDOWS) {
    const v = stock[field]
    if (v == null || v <= 0) continue
    const i = m.gain[k].indexOf(v)
    if (i >= 0) tags.push(`${n}涨幅龙${i + 1}`)
  }
  return tags
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
      gain: {
        d5:  topThreeGain(merged.map((s) => s.pct_change_5d)),
        d10: topThreeGain(merged.map((s) => s.pct_change_10d)),
        d20: topThreeGain(merged.map((s) => s.pct_change_20d)),
        d60: topThreeGain(merged.map((s) => s.pct_change_60d)),
      },
    }
  }, [strong, up, down])
}
