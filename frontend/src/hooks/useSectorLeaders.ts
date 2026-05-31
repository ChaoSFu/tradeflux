/**
 * useSectorLeaders — 贪心独占分配
 *
 * 数据：强股池 + 涨停池 + 跌停池（三路合并去重）
 * 规则：
 *   1. 每只股票只能是一个板块的龙头（独占）
 *   2. 板块按股票数量从大到小排队，大的优先"抢"最强可用股票
 *   3. 被大板块占用的股票，其余板块寻找下一个最强的未被占用股票
 *
 * 返回 Map<stockId, sectorName>  — 仅包含被分配为龙头的股票
 */
import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchStrongPool, fetchLimitMoves } from '@/api/stocks'
import type { Stock } from '@/types'

const LS_MIN_KEY = 'tradeflux:sector_analysis_min_stocks'
const DEFAULT_MIN = 3

function loadMinStocks(): number {
  try {
    const v = parseInt(localStorage.getItem(LS_MIN_KEY) ?? '', 10)
    return isNaN(v) || v < 1 ? DEFAULT_MIN : v
  } catch { return DEFAULT_MIN }
}

export function useSectorLeaders(): Map<number, string> {
  // 与 SectorAnalysis 共用 queryKey → 复用 React Query 缓存，零额外请求
  const { data: strongData } = useQuery({
    queryKey: ['strong-pool-sector-analysis'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
  } as any)

  const { data: upData } = useQuery({
    queryKey: ['limit-up-sector-analysis'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up' }),
  } as any)

  const { data: downData } = useQuery({
    queryKey: ['limit-down-sector-analysis'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down' }),
  } as any)

  return useMemo(() => {
    const strong:    Stock[] = (strongData as any)?.items ?? []
    const limitUp:   Stock[] = (upData    as any)?.items ?? []
    const limitDown: Stock[] = (downData  as any)?.items ?? []

    // ── 1. 三路合并去重（强股池优先）─────────────────────────────────────────
    const seenIds = new Set<number>()
    const merged: Stock[] = []
    for (const s of strong)    { seenIds.add(s.id); merged.push(s) }
    for (const s of limitUp)   { if (!seenIds.has(s.id)) { seenIds.add(s.id); merged.push(s) } }
    for (const s of limitDown) { if (!seenIds.has(s.id)) { seenIds.add(s.id); merged.push(s) } }
    if (merged.length === 0) return new Map()

    const minStocks = loadMinStocks()

    // ── 2. 统计板块股票数，过滤掉不足 min 的板块 ─────────────────────────────
    const sectorCount = new Map<string, number>()
    for (const s of merged) {
      for (const name of s.sectors ?? []) {
        sectorCount.set(name, (sectorCount.get(name) ?? 0) + 1)
      }
    }
    const displayedSectors = new Set(
      [...sectorCount.entries()].filter(([, cnt]) => cnt >= minStocks).map(([k]) => k)
    )

    // ── 3. 每个显示板块收集候选股票，按 leader_score 降序排列 ─────────────────
    const sectorCandidates = new Map<string, Stock[]>()
    for (const s of merged) {
      for (const name of s.sectors ?? []) {
        if (!displayedSectors.has(name)) continue
        if (!sectorCandidates.has(name)) sectorCandidates.set(name, [])
        sectorCandidates.get(name)!.push(s)
      }
    }
    for (const candidates of sectorCandidates.values()) {
      candidates.sort((a, b) => b.leader_score - a.leader_score)
    }

    // ── 4. 板块按股票数量降序排队（大的优先抢龙头）────────────────────────────
    const sortedSectors = [...sectorCandidates.keys()].sort(
      (a, b) => (sectorCandidates.get(b)?.length ?? 0) - (sectorCandidates.get(a)?.length ?? 0)
    )

    // ── 5. 贪心独占分配：每只股票只能被分配给一个板块 ─────────────────────────
    const assignedStocks = new Set<number>()
    const result = new Map<number, string>()  // stockId → 分配到的板块名

    for (const sectorName of sortedSectors) {
      const candidates = sectorCandidates.get(sectorName) ?? []
      for (const stock of candidates) {
        if (!assignedStocks.has(stock.id)) {
          assignedStocks.add(stock.id)
          result.set(stock.id, sectorName)
          break
        }
      }
    }

    return result
  }, [strongData, upData, downData])
}
