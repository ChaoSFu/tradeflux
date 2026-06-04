/**
 * useSectorTags — 全局共享板块排名 tag 数据
 *
 * 复用 ['sectors-ranking'] QueryKey（与 SectorRanking 页共享缓存，不产生额外请求）。
 * 返回 Map<sectorCode, TagData>，任何组件都可以通过板块 code 查询其 tag。
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchSectors } from '@/api/sectors'
import type { Sector } from '@/types'

export interface SectorTagData {
  rank_5d:     number | null
  rank_10d:    number | null
  rank_20d:    number | null
  rank_60d:    number | null
  rank_lu:     number | null
  rank_board:  number | null
  rank_strong: number | null
  limit_down_count: number
  // 板块级指标（供卡片头展示）
  pct_today:   number   // 今日涨幅（板块指数，pct_change_30d 历史命名实为今日）
  pct_10d:     number
  pct_20d:     number
  pct_60d:     number
  strong_stock_count: number
  board_height: number
  phase: number   // 生命周期阶段 0-6
}

export interface SectorTagMaps {
  byCode: Map<string, SectorTagData>  // sector.code → tags
  byName: Map<string, SectorTagData>  // sector.name → tags（用于 SectorSection 等只有 name 的场景）
}

export function useSectorTags(): SectorTagMaps {
  const { data } = useQuery({
    queryKey: ['sectors-ranking'],
    queryFn: fetchSectors,
    staleTime: 5 * 60 * 1000,
  })

  return useMemo(() => {
    const sectors: Sector[] = data?.items ?? []
    const byCode = new Map<string, SectorTagData>()
    const byName = new Map<string, SectorTagData>()
    for (const s of sectors) {
      const tagData: SectorTagData = {
        rank_5d:     s.rank_5d     ?? null,
        rank_10d:    s.rank_10d    ?? null,
        rank_20d:    s.rank_20d    ?? null,
        rank_60d:    s.rank_60d    ?? null,
        rank_lu:     s.rank_lu     ?? null,
        rank_board:  s.rank_board  ?? null,
        rank_strong: s.rank_strong ?? null,
        limit_down_count: s.limit_down_count ?? 0,
        pct_today:   s.pct_change_30d ?? 0,
        pct_10d:     s.pct_change_10d ?? 0,
        pct_20d:     s.pct_change_20d ?? 0,
        pct_60d:     s.pct_change_60d ?? 0,
        strong_stock_count: s.strong_stock_count ?? 0,
        board_height: s.board_height ?? 0,
        phase: s.phase ?? 0,
      }
      byCode.set(s.code, tagData)
      byName.set(s.name, tagData)
    }
    return { byCode, byName }
  }, [data])
}
