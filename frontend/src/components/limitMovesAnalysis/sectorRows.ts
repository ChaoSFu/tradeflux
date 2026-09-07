import type { Stock, LimitUpRadarSector } from '@/types'

/**
 * 板块行 = 涨停侧（涨停板块雷达）+ 跌停侧（涨跌停总览）合并。
 *
 * **两侧的板块归组规则不是同一套**：
 *   涨停侧 sector_name 来自后端 limit_up_radar 的 watched sector 分组
 *   跌停侧 sectors[]   来自 Stock 上按展示口径过滤的多板块标签
 * 都源出 StockSectorRelation，但过滤条件不同，所以**一个板块可能只出现在一侧**。
 * 合并用板块名做键，并把「只有一侧有」标出来——静默合并等于宣称两边同口径。
 *
 * 前端只做合并与排序，不重新判定「哪只票属于哪个板块」。
 */
export interface SectorRow {
  name: string
  /** 涨停侧（来自 limit-up-radar）。null = 这个板块没进雷达（未达门槛或口径不同） */
  up: LimitUpRadarSector | null
  /** 跌停侧统计（来自 limit-moves 的跌停股按 sectors 分组） */
  limitDown: number
  downMaxBoard: number
  downOneWord: number
  downStocks: Stock[]
}

export function buildSectorRows(
  sectors: LimitUpRadarSector[],
  downStocks: Stock[],
): SectorRow[] {
  const rows = new Map<string, SectorRow>()
  const blank = (name: string): SectorRow => ({
    name, up: null, limitDown: 0, downMaxBoard: 0, downOneWord: 0, downStocks: [],
  })

  for (const s of sectors) {
    rows.set(s.sector_name, { ...blank(s.sector_name), up: s })
  }
  for (const st of downStocks) {
    for (const name of st.sectors ?? []) {
      const r = rows.get(name) ?? blank(name)
      r.limitDown += 1
      r.downStocks.push(st)
      const b = st.today_limit_down_count ?? 0
      if (b > r.downMaxBoard) r.downMaxBoard = b
      if (st.today_is_one_word_limit_down) r.downOneWord += 1
      rows.set(name, r)
    }
  }
  return [...rows.values()]
}

export type SectorSortKey =
  | 'name' | 'limit_up' | 'continuation' | 'height' | 'broken'
  | 'limit_down' | 'seal_rate' | 'core'

/** 每个排序键取哪个数。**拿不到就是 null，交给 compareWithNullsLast 沉底** */
export function sectorSortValue(r: SectorRow, k: SectorSortKey): number | string | null {
  switch (k) {
    case 'name':         return r.name
    case 'limit_up':     return r.up?.today_limit_up_count ?? null
    case 'continuation': return r.up?.continuation_count ?? null
    case 'height':       return r.up?.board_height ?? null
    case 'broken':       return r.up?.broken_count ?? null
    case 'seal_rate':    return r.up?.seal_rate ?? null
    case 'core':         return r.up?.core_avg_pct_change ?? null
    case 'limit_down':   return r.limitDown || null
  }
}
