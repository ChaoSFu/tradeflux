/**
 * V2 决策链的公共类型。**只有 PASS / WAIT / BLOCK，没有分数。**
 *
 * 为什么不给 0~100 的分：一个 83 分说不清它是"三项满足一项差一点"还是"四项
 * 都刚过线"，而这两种情况的交易含义完全不同。三态 + 明确理由能还原，分数不能。
 *
 * WAIT 和 BLOCK 必须分开：
 *   WAIT  = 条件没满足，**或者我们判不出来**（数据缺失）
 *   BLOCK = 条件明确不满足，有硬证据
 * 把"不知道"归进 BLOCK 就是拿缺失当否定，这个仓库为那类错栽过很多次。
 */
export type GateStatus = 'PASS' | 'WAIT' | 'BLOCK'

export interface GateResult {
  status: GateStatus
  /** 一句话结论，直接显示给人看 */
  label: string
  /** 支撑这个结论的**事实**，每条都要能对回原始字段 */
  evidence: string[]
  /** 判不出来的原因；非空即表示这个 gate 含有"不知道"的成分 */
  unknowns: string[]
}

export const gateTone = (s: GateStatus) =>
  s === 'PASS' ? 'text-up' : s === 'BLOCK' ? 'text-down' : 'text-warn'

export const gateText = (s: GateStatus) =>
  s === 'PASS' ? '通过' : s === 'BLOCK' ? '不通过' : '等待'

/** 缺值一律显示 —，绝不用 0 / 持平 / 空头顶替 */
export const val = (v: number | null | undefined, digits = 1, suffix = '') =>
  v === null || v === undefined ? '—' : `${v.toFixed(digits)}${suffix}`
