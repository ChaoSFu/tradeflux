/**
 * 板块生命周期分布条 —— 全部板块按各自 phase(0-6) 计数的宏观分布。
 * phase 由后端 sector_phase_service 根据板块聚合指标判定（强股数/涨停数/
 * 连板高度/连续性/风险/情绪分），刻画板块所处的生命周期阶段（板块趋势视角）。
 */
import { useQuery } from '@tanstack/react-query'
import { fetchSectors } from '@/api/sectors'
import { PHASE_COLORS, PHASE_LABELS_ZH } from '@/utils/format'
import { cn } from '@/utils/cn'
import type { Sector } from '@/types'

const PHASE_ORDER = [3, 2, 1, 0, 4, 5, 6]  // 高潮→扩张→启动→隐匿→分歧→衰退→死亡

export function PhaseLifecycleBar({
  selected = null,
  onSelect,
  sectorNames,
}: {
  /** 选中的阶段(0-6)，用于高亮；null 表示未选 */
  selected?: number | null
  /** 点击某阶段回调（再次点击同一阶段应传回 null 取消） */
  onSelect?: (phase: number | null) => void
  /** 限定计数的板块名集合（仅统计这些板块的阶段分布）；不传则统计全部 is_watched */
  sectorNames?: string[] | null
} = {}) {
  const { data } = useQuery({ queryKey: ['sectors-lifecycle'], queryFn: fetchSectors })
  const sectors: Sector[] = (data as any)?.items ?? []

  const nameSet = sectorNames ? new Set(sectorNames) : null
  const counts: Record<number, number> = {}
  sectors.forEach((s) => {
    if (nameSet && !nameSet.has(s.name)) return
    counts[s.phase] = (counts[s.phase] ?? 0) + 1
  })
  const total = Object.values(counts).reduce((a, b) => a + b, 0) || 1
  const clickable = !!onSelect

  return (
    <div className="card p-3">
      <div className="text-xs text-text-secondary mb-2">
        板块生命周期分布
        {selected != null && (
          <span className="ml-2 text-accent">· 已筛选「{PHASE_LABELS_ZH[selected]}」，点此清除
            <button className="ml-1 underline" onClick={() => onSelect?.(null)}>清除</button>
          </span>
        )}
      </div>
      <div className="flex items-end gap-1.5 h-16">
        {PHASE_ORDER.map((phase) => {
          const count = counts[phase] ?? 0
          const color = PHASE_COLORS[phase]
          const isSel = selected === phase
          const dim = selected != null && !isSel
          return (
            <button
              key={phase}
              type="button"
              disabled={!clickable || count === 0}
              onClick={() => onSelect?.(isSel ? null : phase)}
              className={cn(
                'flex flex-col items-center gap-0.5 flex-1 h-full justify-end rounded transition-all',
                clickable && count > 0 && 'cursor-pointer hover:bg-bg-elevated',
                isSel && 'ring-1 ring-accent bg-accent/5',
                dim && 'opacity-45',
              )}
            >
              <div
                className="w-full rounded-t transition-all"
                style={{ height: `${Math.max((count / total) * 100, 4)}%`, backgroundColor: color, opacity: count ? 1 : 0.15 }}
              />
              <div className="text-center leading-none">
                <div className="text-xs font-mono" style={{ color }}>{count}</div>
                <div className="text-text-secondary whitespace-nowrap" style={{ fontSize: 11 }}>{PHASE_LABELS_ZH[phase]}</div>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
