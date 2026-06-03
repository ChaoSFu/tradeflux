/**
 * useLeaderUniverseMaxes — 龙头标签的对比基准
 *
 * 基准集合 = 强势股池 + 涨停池 + 跌停池 合并去重后的「全集」。
 * 「10龙/20龙/60龙/60高板龙」等标签代表真正的强势品种，应与全集最高标对比，
 * 而非在某个子列表（如当日跌停池）内比出虚高的"龙"——例如某股仅 3 个涨停，
 * 只因是当日跌停股里历史最强者就被误标为"10龙"。
 *
 * 复用 sector-analysis 的 queryKey（useSectorLeaders 已触发），零额外请求。
 * 指标取 UP 方向（涨停天数 / 当日连板 / 60日最高连板），为市场级强度标尺。
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchStrongPool, fetchLimitMoves } from '@/api/stocks'
import type { Stock } from '@/types'

export type LeaderMaxes = { board: number; d10: number; d20: number; d60: number; high: number }

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
      board: Math.max(0, ...merged.map((s) => s.today_board_count ?? 0)),
      d10:   Math.max(0, ...merged.map((s) => s.limit_up_days_10d  ?? 0)),
      d20:   Math.max(0, ...merged.map((s) => s.limit_up_days_20d  ?? 0)),
      d60:   Math.max(0, ...merged.map((s) => s.limit_up_days_60d  ?? 0)),
      high:  Math.max(0, ...merged.map((s) => s.board_count_60d    ?? 0)),
    }
  }, [strong, up, down])
}
