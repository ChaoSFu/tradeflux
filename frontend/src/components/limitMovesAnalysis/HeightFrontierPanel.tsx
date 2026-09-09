import { useMemo } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceDot,
} from 'recharts'
import { format } from 'date-fns'
import type { HeightPoint } from '@/api/marketTrend'

const C_UP = '#FF4560'
const LABEL_W = 34

/**
 * 市场高度前沿。**语义原样保留 SpeculationRadar 的三条**：
 *   - 上沿窗口不满时 frontier = null，`connectNulls={false}`——不画一段并不
 *     存在的基线
 *   - 只画 `is_breakout === true` 的突破点。`null` 是"不知道"，画上去就是把
 *     不确定当结论
 *   - 上沿是 stepAfter，不是插值
 */
export function HeightFrontierPanel({
  points, frontierWindow,
}: { points: HeightPoint[]; frontierWindow: number }) {
  const chart = useMemo(() => points.map((p) => ({
    date: format(new Date(p.date), 'MM/dd'),
    _p: p,
    最高连板: p.height,
    上沿: p.frontier,
  })), [points])

  const breakouts = points.filter((p) => p.is_breakout === true)
  const unknown = points.filter((p) => p.is_breakout === null && p.has_data).length

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <span className="text-xs font-semibold text-text-primary">市场高度前沿</span>
        <span className="text-[10px] text-text-muted">
          实线=当日最高连板 · 虚线=近{frontierWindow}日上沿 · 圈=确认突破
          {unknown > 0 && (
            <span className="text-warn ml-1">· {unknown} 天上沿窗口缺数据，不判突破</span>
          )}
        </span>
      </div>
      <div className="h-44">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chart} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: '#737A96', fontSize: 10 }}
                   axisLine={false} tickLine={false}
                   interval="preserveStartEnd" minTickGap={28} />
            <YAxis width={LABEL_W} tick={{ fill: '#737A96', fontSize: 10 }}
                   axisLine={false} tickLine={false} allowDecimals={false}
                   tickFormatter={(v: number) => `${v}板`} />
            <Tooltip content={<HeightTip />} />
            <Line type="stepAfter" dataKey="上沿" stroke="#737A96" strokeWidth={1.5}
                  strokeDasharray="5 4" dot={false} connectNulls={false} />
            <Line type="monotone" dataKey="最高连板" stroke={C_UP} strokeWidth={2}
                  dot={false} activeDot={{ r: 4 }} />
            {breakouts.map((p) => (
              <ReferenceDot key={p.date} x={format(new Date(p.date), 'MM/dd')} y={p.height}
                            r={5} fill="none" stroke={C_UP} strokeWidth={2} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

interface TipPayload { payload: { _p: HeightPoint } }
function HeightTip({ active, payload }: { active?: boolean; payload?: TipPayload[] }) {
  const p = active ? payload?.[0]?.payload._p : null
  if (!p) return null
  return (
    <div className="bg-bg-elevated border border-bg-border rounded px-2.5 py-1.5
                    text-[11px] space-y-0.5 shadow-lg">
      <div className="text-text-primary font-medium">{p.date}</div>
      <div className="text-text-secondary">最高连板 <b className="text-up">{p.height}板</b></div>
      <div className="text-text-secondary">
        近{20}日上沿 {p.frontier ?? <span className="text-text-muted">不知道</span>}
        {p.is_breakout === true && <span className="text-up ml-1">突破</span>}
        {p.is_breakout === null && (
          <span className="text-warn ml-1">窗口覆盖 {p.frontier_covered} 天，不判</span>
        )}
      </div>
      <div className="text-text-muted">
        天花板附近 {p.near_top_count} 只 · 3板以上 {p.multi_board_count} 只 ·
        涨停 {p.limit_up_count}
      </div>
    </div>
  )
}
