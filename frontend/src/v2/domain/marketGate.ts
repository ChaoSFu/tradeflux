/**
 * Market Gate —— 指数层面今天有没有交易许可。
 *
 * ## 市场含义
 *
 * 高标龙头的生存环境由指数决定：指数走坏时连板梯队会整体坍塌，再好的个股结构
 * 也扛不住。所以这一层不是"预测大盘"，是"确认今天值不值得下场"。
 *
 * ## 只用现成的客观事实
 *
 * 全部来自 `IndexTrendAnalysis`（后端 market-trend 已经算好的）：
 *   alignment          均线排列 bull / bear / mixed
 *   above_ma5/10/20/60 收盘在各条均线之上
 *   ma20_slope_pct     MA20 斜率
 *   state              strong / bullish / range / bearish / weak
 *
 * **刻意不用情绪温度**：那是自造的复合分，说不清它由什么构成。
 *
 * ## 规则（第一版刻意保守）
 *
 *   BLOCK  过半核心指数处于 bearish/weak
 *   PASS   过半核心指数处于 bullish/strong，且没有指数是 bearish/weak
 *   WAIT   其余一切，包括"拿不到数据"
 *
 * PASS 的条件里那句"且没有指数明显走坏"是刻意的：主板强而创业板崩的时候，
 * 高标龙头往往正在受伤，这种分化不该给通行证。
 */
import type { IndexTrendAnalysis, MarketTrendResponse } from '@/types'
import type { GateResult } from './gate'

/** 核心指数：高标龙头主要活在这几个池子里 */
const CORE = ['000001', '399001', '399006', '000688']

const BULL = new Set(['bullish', 'strong'])
const BEAR = new Set(['bearish', 'weak'])

export interface MarketFacts {
  indices: IndexTrendAnalysis[]
  bullCount: number
  bearCount: number
  total: number
}

export function marketFacts(data?: MarketTrendResponse): MarketFacts {
  const idx = (data?.indices ?? []).filter((i) => CORE.includes(i.code))
  return {
    indices: idx,
    bullCount: idx.filter((i) => BULL.has(i.state)).length,
    bearCount: idx.filter((i) => BEAR.has(i.state)).length,
    total: idx.length,
  }
}

export function marketGate(data?: MarketTrendResponse): GateResult {
  const f = marketFacts(data)
  if (!f.total) {
    return {
      status: 'WAIT',
      label: '指数数据不足',
      evidence: [],
      // 拿不到数据不是"市场不好"，是"我们不知道"——所以是 WAIT 不是 BLOCK
      unknowns: ['未取到核心指数趋势数据'],
    }
  }
  const ev = [
    `核心指数多头 ${f.bullCount}/${f.total}`,
    ...f.indices.map(
      (i) =>
        `${i.name} ${i.state_label}｜均线${
          i.alignment === 'bull' ? '多头' : i.alignment === 'bear' ? '空头' : '交织'
        }｜MA20斜率 ${i.ma20_slope_pct.toFixed(2)}%`,
    ),
  ]
  const majority = Math.floor(f.total / 2) + 1
  if (f.bearCount >= majority) {
    return { status: 'BLOCK', label: '多数核心指数走坏', evidence: ev, unknowns: [] }
  }
  if (f.bullCount >= majority && f.bearCount === 0) {
    return { status: 'PASS', label: '核心指数多头', evidence: ev, unknowns: [] }
  }
  return {
    status: 'WAIT',
    label: f.bearCount > 0 ? '指数分化，有指数走坏' : '多头不足过半',
    evidence: ev,
    unknowns: [],
  }
}
