/**
 * useSevereTargets — code → 今日还需涨幅%触发严重异常波动（涨幅、未触发）
 * 全平台共享缓存（5分钟），供任意展示个股的位置标注「再涨 X% 触发严重异动」。
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchSevereTargets, type SevereTarget } from '@/api/watchlist'

export type { SevereTarget }

export function useSevereTargets(): Map<string, SevereTarget> {
  const { data } = useQuery({
    queryKey: ['severe-targets'],
    queryFn: fetchSevereTargets,
    staleTime: 5 * 60 * 1000,
  })
  return useMemo(() => new Map(Object.entries(data ?? {})), [data])
}
