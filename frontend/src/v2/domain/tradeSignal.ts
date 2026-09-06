/**
 * Signal Ready —— 四道 gate 的合成。**全系统只有这一处定义"能不能进入人工确认"。**
 *
 *   SignalReady = MarketPASS ∧ SectorPASS ∧ CorePASS ∧ ¬RegulatoryBlock
 *
 * ## 它不是什么
 *
 *   ≠ 自动下单      系统不下单，买点由人确认
 *   ≠ 预测上涨      四个条件同时成立不代表会涨
 *   ≠ 盈利保证
 *
 * 它只表示：指数、板块、个股结构、监管四项没有互相矛盾，**可以拿来看盘**。
 *
 * ## 合成规则
 *
 *   任一 BLOCK  → BLOCKED，并给出是哪一项
 *   全部 PASS   → READY
 *   其余        → WAIT，并列出还差哪几项（含"判不出来"的）
 *
 * 注意 WAIT 里混着两种完全不同的情况：条件没满足、和我们判不出来。所以
 * `unknowns` 要一路带上来——人看到"板块 WAIT"时必须知道那是"板块不够强"还是
 * "板块趋势我们根本算不了"。
 */
import type { GateResult, GateStatus } from './gate'

export interface SignalInput {
  market: GateResult
  sector: GateResult
  core: { status: GateStatus; label: string }
  regulatory: { status: GateStatus; label: string; reason?: string }
}

export interface SignalResult {
  status: 'READY' | 'WAIT' | 'BLOCKED'
  /** 挡住的/还差的项，直接显示 */
  blockers: string[]
  /** 判不出来的项 —— 跟"不满足"分开列 */
  unknowns: string[]
}

export function tradeSignal(i: SignalInput): SignalResult {
  const items: Array<[string, GateStatus, string]> = [
    ['市场', i.market.status, i.market.label],
    ['主线', i.sector.status, i.sector.label],
    ['核心股', i.core.status, i.core.label],
    ['监管', i.regulatory.status, i.regulatory.reason || i.regulatory.label],
  ]
  const unknowns = [...i.market.unknowns, ...i.sector.unknowns]
  const blocked = items.filter(([, s]) => s === 'BLOCK')
  if (blocked.length) {
    return {
      status: 'BLOCKED',
      blockers: blocked.map(([n, , l]) => `${n}：${l}`),
      unknowns,
    }
  }
  const waiting = items.filter(([, s]) => s === 'WAIT')
  if (waiting.length) {
    return { status: 'WAIT', blockers: waiting.map(([n, , l]) => `${n}：${l}`), unknowns }
  }
  return { status: 'READY', blockers: [], unknowns }
}

export const signalTone = (s: SignalResult['status']) =>
  s === 'READY' ? 'text-up' : s === 'BLOCKED' ? 'text-down' : 'text-warn'

export const signalText = (s: SignalResult['status']) =>
  s === 'READY' ? 'SIGNAL READY' : s === 'BLOCKED' ? 'BLOCKED' : 'WAIT'
