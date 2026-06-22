/**
 * useRegulatoryStatus — 全局股票监管状态映射（code → 状态）
 *
 * 复用 ['regulatory-watchlist'] queryKey（与重点监控页共享缓存，零额外请求）。
 * 任何展示股票的位置可据此打监管警示徽章。
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchRegulatoryWatchlist } from '@/api/watchlist'

export type RegStatus = 'monitoring' | 'ending_soon' | 'approaching' | 'released'

export function useRegulatoryStatus(): Map<string, RegStatus> {
  const { data } = useQuery({
    queryKey: ['regulatory-watchlist'],
    queryFn: fetchRegulatoryWatchlist,
    staleTime: 5 * 60 * 1000,
  })

  return useMemo(() => {
    const m = new Map<string, RegStatus>()
    // 优先级低 → 高写入，高优先级覆盖
    for (const it of data?.recently_released ?? []) m.set(it.security_code, 'released')
    for (const it of data?.approaching ?? []) m.set(it.security_code, 'approaching')
    for (const it of data?.ending_soon ?? []) m.set(it.security_code, 'ending_soon')
    for (const it of data?.monitoring ?? []) m.set(it.security_code, 'monitoring')
    return m
  }, [data])
}
