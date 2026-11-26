/**
 * 涨跌停只数的两样共用件：近 N 个交易日的柱状缩略图、「今日只数 / 均值」四档切分。
 *
 * 顶栏「跌停 · 极端做空」（全市场）和强势股概览「跌停 · 高标退潮」（强势池，
 * 2026-11-26 加）共用。**阈值只在这里写一次**：同一个倍数在两个地方叫不同的档，
 * 用户就没法拿两格对着看。
 */
import { cn } from '@/utils/cn'

export type RatioLevel = 'low' | 'normal' | 'high' | 'extreme'

/**
 * 四档 —— **全部由「今日只数 / 均值」这一个比值定**，没有别的输入。
 * 这不是评分，是把一个比值切成四段；每一档叫什么由调用方定（顶栏的 low 叫「退潮」，
 * 说的是做空力量；强势池那格的 low 叫「偏少」，那里「退潮」说的是高标）。
 */
export function ratioLevel(ratio: number | null): RatioLevel | null {
  return ratio == null ? null
    : ratio >= 2 ? 'extreme'
    : ratio >= 1 ? 'high'
    : ratio <= 0.5 ? 'low'
    : 'normal'
}

/** 四档的阈值说明，悬停提示里用；names 依次是 low / normal / high / extreme 的叫法 */
export function ratioLevelRule(names: Record<RatioLevel, string>): string {
  return `≤0.5× ${names.low} · 1× 以下${names.normal} · ≥1× ${names.high} · ≥2× ${names.extreme}`
}

/**
 * 一条只数序列（日期升序、最后一个点是今天）的读数：今天、前一个点、均值、倍数、增减、
 * 最后 12 根柱子。只有今天一个点时没有均值可言；均值是 0（全是 0）时也不给倍数。
 */
export function seriesStat(series: { date: string; n: number }[]) {
  const today = series[series.length - 1]
  const prev = series.length >= 2 ? series[series.length - 2] : null
  const avg = series.length >= 2 ? series.reduce((a, p) => a + p.n, 0) / series.length : null
  return {
    today, prev, avg, days: series.length,
    ratio: avg ? today.n / avg : null,
    delta: prev ? today.n - prev.n : null,
    bars: series.slice(-12),
  }
}

/**
 * 近 N 个交易日的只数缩略图。
 *
 * **柱状不是折线**：只数是离散计数，柱子高度一眼能比大小；折线会在 0 附近画出
 * 根本不存在的斜坡。最后一根是今天，单独着色。
 * 那天真是 0 只就画 1px 的底座——**0 是事实，不是没数据**，逐根悬停给具体只数。
 */
export function MiniBars({ data, tone = 'down', height = 16, barWidth = 3 }: {
  data: { date: string; n: number }[]
  tone?: 'down' | 'up'
  height?: number
  barWidth?: number
}) {
  const max = Math.max(...data.map((d) => d.n), 1)
  const [last, rest] = tone === 'up' ? ['bg-up', 'bg-up/35'] : ['bg-down', 'bg-down/35']
  return (
    <div className="flex items-end gap-[2px]" style={{ height }}>
      {data.map((d, i) => (
        <div key={d.date} title={`${d.date.slice(5)} ${d.n} 只`}
             className={cn('rounded-sm', i === data.length - 1 ? last : rest)}
             style={{ width: barWidth, height: d.n === 0 ? 1 : Math.max(3, (d.n / max) * height) }} />
      ))}
    </div>
  )
}
