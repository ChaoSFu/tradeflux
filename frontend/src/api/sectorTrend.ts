import client from './client'

// ── 板块趋势 · 主升板块雷达（后端 /sector-trend，规则见 docs/SECTOR_MAINLINE.md）──────
// 事实 → 四道闸 → 状态 → 证据。不打分；只描述板块处在什么状态，不给买卖建议。
export type MainlineState =
  | 'ACCELERATION' | 'MAIN_RISE' | 'IGNITION' | 'CLIMAX'
  | 'DIVERGENCE' | 'WEAKENING' | 'NONE' | 'UNKNOWN'
export type GateStatus = 'PASS' | 'WARN' | 'FAIL' | 'UNKNOWN'
export type GateKey = 'trend' | 'rs' | 'ecology' | 'risk'
export type LuTrend = 'EXPANDING' | 'STABLE' | 'CONTRACTING' | 'SPIKE' | 'UNKNOWN'

export interface Gate {
  status: GateStatus
  reason: string
}

// 缺数据的事实一律是 null（不知道），不是 0
export interface SectorFacts {
  close: number | null
  pct: number | null
  r5: number | null
  r5_rank: number | null
  dev20: number | null          // 收盘偏离 MA20 %
  ma20_slope: number | null     // MA20 五日变化 %
  high20: boolean | null        // 收盘创 20 日新高
  amount_ratio: number | null   // 5 日 / 20 日成交额
  rs5: number | null            // 相对上证，百分点
  rs10: number | null
  rs20: number | null
  rs10_rank: number | null
  rs10_n: number | null
  lu: number | null
  lu_series: (number | null)[]  // 最近 6 个交易日（eco_dates）的涨停只数
  lu_3d: number | null
  lu_prev3d: number | null
  lu_trend: LuTrend
  height: number | null
  height_3d: number | null
  broken: number | null
  seal_rate: number | null
  ld: number | null
  up_ratio: number | null
}

export interface SectorTrendItem {
  code: string
  name: string
  stock_count: number | null
  members: number
  state: MainlineState
  state_label: string
  state_reason: string
  trail: { date: string; state: MainlineState }[]   // 最近 5 天
  gates: Record<GateKey, Gate>
  facts: SectorFacts
  evidence: string[]            // 数据质量说明：缺了什么、影响哪条事实
}

interface SectorTrendMeta {
  version: string
  state_date: string | null     // 状态基准：这一天的收盘
  notes: string[]
  benchmark: { code: string; name: string }
  thresholds: Record<string, number>
  eco_dates?: string[]
}

export interface SectorTrendList extends SectorTrendMeta {
  counts: Record<MainlineState, number>
  sectors: SectorTrendItem[]
}

export interface SectorTrendHistoryDay {
  date: string
  state: MainlineState
  state_label: string
  reason: string
  gates: Record<GateKey, Gate>
}

export interface SectorTrendDetail extends SectorTrendMeta {
  sector: SectorTrendItem
  history: SectorTrendHistoryDay[]   // 最近 10 天
  bars: {
    date: string
    close: number
    pct: number | null
    amount: number | null
    ma5: number | null
    ma10: number | null
    ma20: number | null
  }[]
  ecology: { date: string; lu: number | null; height: number | null; broken: number | null; ld: number | null }[]
}

export const fetchSectorTrend = () =>
  client.get<SectorTrendList>('/sector-trend').then((r) => r.data)

export const fetchSectorTrendDetail = (code: string) =>
  client.get<SectorTrendDetail>(`/sector-trend/${code}`).then((r) => r.data)
