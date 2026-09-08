import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, X } from 'lucide-react'
import { cn } from '@/utils/cn'
import { fetchLadderMembers, type HeightPoint } from '@/api/marketTrend'
import { QueryState } from './QueryState'

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
  // 点开的格子：哪天、哪一档。再点同一个格子收起
  const [picked, setPicked] = useState<{ date: string; lv: string } | null>(null)

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
                  const on = picked?.date === p.date && picked?.lv === lv
                  return (
                    // 空格子不给点——点开必然是空列表，那不是信息
                    <div key={p.date}
                         onClick={n ? () => setPicked(
                           on ? null : { date: p.date, lv }) : undefined}
                         className={cn('flex-1 rounded-[1px]',
                           n ? 'cursor-pointer hover:opacity-70' : '',
                           on ? 'ring-1 ring-accent' : '')}
                         style={{
                           height: 13,
                           background: n === 0 ? '#1A1F30'
                             : `rgba(255,45,85,${0.15 + (n / rowMax[lv]) * 0.85})`,
                         }}
                         title={n ? `${p.date}  ${lv}板 ${n} 只 · 点开看是哪几只`
                                  : `${p.date}  ${lv}板 0 只`} />
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
      {picked && (
        <LadderMembers date={picked.date} lv={picked.lv}
                       expected={points.find((p) => p.date === picked.date)
                         ?.ladder[picked.lv] ?? 0}
                       onClose={() => setPicked(null)} />
      )}

      {!open && (
        <p className="text-[10px] text-text-muted">
          默认只显示 3 板以上。每行按自己的峰值着色（各板级只数量级差几十倍）。
          点格子看当天这一档是哪几只。
        </p>
      )}
    </div>
  )
}

/**
 * 点开的那个格子：当天该档位的股票。
 *
 * **列表长度必须等于格子里的数字**（后端同源，见 fetchLadderMembers）。对不上时
 * 明说，不闷着——那种不一致正是"两套判定"的信号，藏起来只会让它活得更久。
 */
function LadderMembers({ date, lv, expected, onClose }: {
  date: string; lv: string; expected: number; onClose: () => void
}) {
  const q = useQuery({
    queryKey: ['ladder-members', date, lv],
    queryFn: () => fetchLadderMembers(date, lv),
    staleTime: 10 * 60 * 1000,
  })
  const members = q.data?.members ?? []
  const mismatch = q.data && members.length !== expected

  return (
    <div className="mt-1.5 rounded bg-bg-elevated/60 px-2.5 py-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] text-text-primary">
          {date} · <span className="text-up font-medium">{lv}板</span>
          <span className="text-text-muted ml-1.5">{expected} 只</span>
        </span>
        <button onClick={onClose} className="text-text-muted hover:text-text-secondary">
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      {mismatch && (
        <div className="text-[11px] text-warn mt-1">
          明细 {members.length} 只跟格子上的 {expected} 只对不上——两边口径分叉了
        </div>
      )}
      <div className="mt-1.5">
        <QueryState qs={[q]} isEmpty={!members.length}
                    emptyText="这一档没有股票" rows={2}>
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            {members.map((m) => (
              <span key={m.code} className="text-[11px] whitespace-nowrap">
                <span className="text-text-primary">{m.name ?? m.code}</span>
                <span className="ml-1 font-mono tabular-nums text-text-muted">
                  {m.code}
                </span>
                {/* 封顶档里混着 9 板 10 板，具体几板要看得见 */}
                {String(m.board_count) !== lv && (
                  <span className="ml-1 font-mono tabular-nums text-up/80">
                    {m.board_count}板
                  </span>
                )}
              </span>
            ))}
          </div>
        </QueryState>
      </div>
    </div>
  )
}
