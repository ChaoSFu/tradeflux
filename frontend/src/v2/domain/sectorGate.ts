/**
 * Sector Gate —— 板块是不是主线。
 *
 * ## 这一层现在有一个诚实的缺口，必须先说
 *
 * **板块没有均线。** `SectorIndexDaily` 只有当天一根，历史回填被数据源
 * （push2his）限流拦着。所以：
 *
 *   板块趋势 alignment = unknown
 *
 * **绝不用 5/10/20 日涨幅冒充均线多头。** 涨幅为正只说明这段时间涨了，说不出
 * 结构——一只从高位跌下来的票 20 日涨幅仍可能是正的。这两件事混为一谈，是这个
 * 仓库明确禁止的那一类错。
 *
 * 所以 Sector Gate 现在最多给到 WAIT，给不出 PASS。这不是 bug，是数据缺口的
 * 如实表达；补上板块 K 线之后它才可能变 PASS。
 *
 * ## 现在能给的：领先证据（Leadership Evidence）
 *
 * 这些是真实的、后端已经算好的排名事实，跟"趋势"是两个不同的东西，界面上必须
 * 分开摆，不能让人误以为"证据多 = 趋势好"：
 *
 *   rank_5d / rank_10d / rank_20d   短中期涨幅排名
 *   rank_lu                          涨停数排名
 *   board_height                     最高连板
 *   rank_strong                      强势股数量排名
 *   amount                           成交额
 *
 * 排序用**透明的字典序**，不合成分数：先看有几条证据进了前 N，再看最高板，
 * 再看成交额。每一步都能还原。
 */
import type { Sector } from '@/types'
import type { GateResult } from './gate'

/** 进前几名才算一条"领先证据"。写死在这里，不散落在组件里 */
export const TOP_N = 5

export interface SectorEvidence {
  sector: Sector
  /** 命中的领先证据，逐条可还原 */
  hits: string[]
  boardHeight: number
  amount: number
}

export function sectorEvidence(s: Sector): SectorEvidence {
  const hits: string[] = []
  const rank = (r: number | null, label: string) => {
    if (r !== null && r <= TOP_N) hits.push(`${label}#${r}`)
  }
  rank(s.rank_5d, '5日')
  rank(s.rank_10d, '10日')
  rank(s.rank_20d, '20日')
  rank(s.rank_lu, '涨停')
  rank(s.rank_strong, '强势股')
  if (s.board_height >= 4) hits.push(`${s.board_height}板`)
  return { sector: s, hits, boardHeight: s.board_height, amount: s.amount }
}

/**
 * 主线排序：**字典序，不是加权分**。
 * 1) 领先证据条数  2) 最高连板  3) 成交额
 * 每一层都是单一事实，任何一行的位次都能用一句话解释。
 */
export function rankMainlines(sectors: Sector[]): SectorEvidence[] {
  return sectors
    .map(sectorEvidence)
    .filter((e) => e.hits.length > 0)
    .sort(
      (a, b) =>
        b.hits.length - a.hits.length ||
        b.boardHeight - a.boardHeight ||
        b.amount - a.amount,
    )
}

/**
 * 单个板块的 gate。**趋势判不出来时给 WAIT 并说明**，不猜也不用涨幅顶替。
 */
export function sectorGate(s: Sector | undefined): GateResult {
  if (!s) {
    return { status: 'WAIT', label: '未知板块', evidence: [], unknowns: ['该股票没有主板块'] }
  }
  const e = sectorEvidence(s)
  const ev = [
    `领先证据 ${e.hits.length} 条${e.hits.length ? '：' + e.hits.join('、') : ''}`,
    `涨停 ${s.limit_up_count}｜最高 ${s.board_height} 板｜强势股 ${s.strong_stock_count}`,
    `5日 ${s.pct_change_5d.toFixed(1)}%｜10日 ${s.pct_change_10d.toFixed(1)}%｜20日 ${s.pct_change_20d.toFixed(1)}%`,
  ]
  return {
    status: 'WAIT',
    label: e.hits.length >= 2 ? '有领先证据，但趋势判不出' : '领先证据不足',
    evidence: ev,
    // **这条缺口必须一路带到界面上**，否则人会以为 WAIT 是"板块不好"
    unknowns: ['板块无均线数据（板块指数历史未回填），趋势无法判定'],
  }
}
