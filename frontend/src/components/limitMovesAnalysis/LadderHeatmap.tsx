import { useMemo, useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import type { HeightPoint } from '@/api/marketTrend'

const LABEL_W = 34

/**
 * 连板梯队 heatmap。**每行按自己的峰值归一**——首板动辄五六十只、8板常年只有
 * 1 只，全局归一之后除了首板那行全是黑的，而"2板这两天比平时厚"恰恰是这张图
 * 要回答的问题。这条口径原样从 SpeculationRadar 搬过来，没改。
 *
 * 默认压缩到只显示 3 板以上（高位梯队才是每天要扫的），点开看全部。
 */
export function LadderHeatmap({ points }: { points: HeightPoint[] }) {
  const [open, setOpen] = useState(false)

  const rank = (k: string) => parseInt(k) + (k.endsWith('+') ? 0.5 : 0)
  const levels = useMemo(() => {
    const s = new Set<string>()
    for (const p of points) for (const k of Object.keys(p.ladder)) s.add(k)
    return [...s].sort((a, b) => rank(b) - rank(a))
  }, [points])

  const rowMax = useMemo(() => {
    const m: Record<string, number> = {}
    for (const lv of levels) m[lv] = Math.max(1, ...points.map((p) => p.ladder[lv] ?? 0))
    return m
  }, [levels, points])

  const shown = open ? levels : levels.filter((lv) => rank(lv) >= 3)

  if (!points.length) {
    return <div className="text-xs text-text-muted py-4 text-center">暂无数据</div>
  }
  return (
    <div className="space-y-1.5">
      <button onClick={() => setOpen((v) => !v)}
              className="w-full flex items-baseline justify-between gap-2 group">
        <span className="text-xs font-semibold text-text-primary">
          连板梯队 · 近 {points.length} 个交易日
        </span>
        <span className="text-[10px] text-text-muted flex items-center gap-0.5
                         group-hover:text-text-secondary">
          {open ? '收起' : `展开全部 ${levels.length} 档`}
          {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </span>
      </button>
      <div className="overflow-x-auto">
        <div style={{ minWidth: LABEL_W + points.length * 9 }}>
          {shown.map((lv) => (
            <div key={lv} className="flex items-center" style={{ height: 16 }}>
              <div className="text-[10px] text-text-muted text-right pr-1.5 shrink-0"
                   style={{ width: LABEL_W }}>{lv}板</div>
              <div className="flex gap-px flex-1">
                {points.map((p) => {
                  const n = p.ladder[lv] ?? 0
                  return (
                    <div key={p.date} className="flex-1 rounded-[1px]"
                         style={{
                           height: 13,
                           background: n === 0 ? '#1A1F30'
                             : `rgba(255,45,85,${0.15 + (n / rowMax[lv]) * 0.85})`,
                         }}
                         title={`${p.date}  ${lv}板 ${n} 只`} />
                  )
                })}
              </div>
            </div>
          ))}
          <div className="flex items-center pt-1">
            <div className="shrink-0" style={{ width: LABEL_W }} />
            <div className="flex-1 flex justify-between text-[10px] text-text-muted">
              <span>{points[0]?.date.slice(5)}</span>
              <span>{points[points.length - 1]?.date.slice(5)}</span>
            </div>
          </div>
        </div>
      </div>
      {!open && (
        <p className="text-[10px] text-text-muted">
          默认只显示 3 板以上。每行按自己的峰值着色（各板级只数量级差几十倍）。
        </p>
      )}
    </div>
  )
}
