/**
 * Shared sector tag chips + overflow badge.
 * Used by StockPool, LimitMovesPool, and any other table that shows sector tags.
 */
import { useState, useRef } from 'react'
import type { SectorTagData } from '@/hooks/useSectorTags'

// ─── Color palette ────────────────────────────────────────────────────────────

const SECTOR_COLORS = [
  '#4F9CF9', '#26C281', '#F59E0B', '#A78BFA', '#EC4899',
  '#14B8A6', '#F97316', '#64748B', '#8B5CF6', '#10B981',
]

export function getSectorColor(name: string): string {
  let hash = 0
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  }
  return SECTOR_COLORS[hash % SECTOR_COLORS.length]
}

// ─── Single chip ──────────────────────────────────────────────────────────────

export function SectorTag({ name }: { name: string }) {
  const color = getSectorColor(name)
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-xs font-medium whitespace-nowrap"
      style={{
        color,
        backgroundColor: `${color}18`,
        border: `1px solid ${color}35`,
      }}
    >
      {name}
    </span>
  )
}

// ─── Overflow badge (shows hidden count, expands on hover) ───────────────────

export function OverflowBadge({ count, hidden }: { count: number; hidden: string[] }) {
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null)
  const ref = useRef<HTMLSpanElement>(null)
  const MAX_W = 360

  const handleEnter = () => {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect()
      const vpW = window.innerWidth
      let left = r.left
      if (left + MAX_W > vpW - 8) left = Math.max(8, vpW - MAX_W - 8)
      setPos({ top: r.top - 8, left })
    }
  }

  return (
    <span
      ref={ref}
      className="relative inline-flex items-center px-1.5 py-0.5 rounded text-xs text-text-muted bg-bg-elevated border border-bg-border cursor-default select-none"
      onMouseEnter={handleEnter}
      onMouseLeave={() => setPos(null)}
    >
      +{count}
      {pos && (
        <div
          className="fixed z-50 p-2 rounded-lg shadow-xl border border-bg-border bg-bg-card flex flex-wrap gap-1"
          style={{
            top: pos.top,
            left: pos.left,
            transform: 'translateY(-100%)',
            minWidth: 160,
            maxWidth: MAX_W,
          }}
          onMouseEnter={handleEnter}
          onMouseLeave={() => setPos(null)}
        >
          {hidden.map((name) => (
            <SectorTag key={name} name={name} />
          ))}
        </div>
      )}
    </span>
  )
}

// ─── Leader badge ─────────────────────────────────────────────────────────────

export function LeaderTag({ label }: { label: string }) {
  // 龙2（标签以 "2" 结尾）用更淡的颜色，与龙1（"1"结尾）区分主次
  const isRank2 = label.endsWith('2')
  const cls = isRank2
    ? 'bg-amber-500/8 text-amber-400/55 border-amber-500/20'
    : 'bg-amber-500/15 text-amber-400 border-amber-500/30'
  return (
    <span className={`inline-flex items-center px-1 py-px text-[9px] font-bold rounded border whitespace-nowrap leading-tight ${cls}`}>
      {label}
    </span>
  )
}

// ─── Negative feedback badge (red, for 跌停 consecutive streaks) ─────────────

export function NegativeTag({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center px-1 py-px text-[9px] font-bold rounded bg-down/15 text-down border border-down/30 whitespace-nowrap leading-tight">
      {label}
    </span>
  )
}

// ─── 监管状态徽章（全局警示：监管中/即将解除/即将进入/近期解除）────────────────

export type RegStatus = 'monitoring' | 'ending_soon' | 'approaching' | 'released'

const REG_META: Record<RegStatus, { label: string; cls: string }> = {
  monitoring:  { label: '监管中',   cls: 'bg-down/20 text-down border-down/40' },
  ending_soon: { label: '即将解除', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/30' },
  approaching: { label: '即将监管', cls: 'bg-orange-500/15 text-orange-400 border-orange-500/35' },
  released:    { label: '近期解除', cls: 'bg-text-muted/15 text-text-muted border-text-muted/30' },
}

export function RegulatoryTag({ status, title }: { status: RegStatus; title?: string }) {
  const m = REG_META[status]
  return (
    <span
      title={title ?? `重点监控：${m.label}`}
      className={`inline-flex items-center px-1 py-px text-[9px] font-bold rounded border whitespace-nowrap leading-tight ${m.cls}`}
    >
      {m.label}
    </span>
  )
}

// ─── 昨日涨停/跌停徽章（一致性强、需谨慎）────────────────────────────────────

export function YesterdayLimitTag({ dir }: { dir: 'up' | 'down' }) {
  const up = dir === 'up'
  return (
    <span
      title={up ? '昨日涨停，一致性强，谨慎追高' : '昨日跌停，一致性强，谨慎抄底'}
      className={`inline-flex items-center px-1 py-px text-[9px] font-bold rounded border whitespace-nowrap leading-tight ${
        up ? 'bg-up/15 text-up border-up/30' : 'bg-down/15 text-down border-down/30'
      }`}
    >
      {up ? '昨涨停' : '昨跌停'}
    </span>
  )
}

// ─── Sector leader badge (colored like SectorTag, ★ prefix) ──────────────────

export function SectorLeaderTag({ name }: { name: string }) {
  const color = getSectorColor(name)
  return (
    <span
      className="inline-flex items-center gap-0.5 px-1 py-px text-[9px] font-bold rounded whitespace-nowrap leading-tight"
      style={{
        color,
        backgroundColor: `${color}22`,
        border: `1px solid ${color}55`,
      }}
    >
      <span style={{ opacity: 0.9 }}>★</span>{name}
    </span>
  )
}

// ─── Sector rank tags（落库的排名 tag，全局共享）─────────────────────────────

const RANK_TAG_STYLES = [
  { color: '#FFD700', bg: 'rgba(255,215,0,0.14)',   border: 'rgba(255,215,0,0.40)'   }, // 金
  { color: '#C8C8C8', bg: 'rgba(200,200,200,0.12)', border: 'rgba(200,200,200,0.35)' }, // 银
  { color: '#CD7F32', bg: 'rgba(205,127,50,0.14)',  border: 'rgba(205,127,50,0.40)'  }, // 铜
  { color: '#5EA6FF', bg: 'rgba(94,166,255,0.12)',  border: 'rgba(94,166,255,0.35)'  }, // 蓝
  { color: '#4ADE80', bg: 'rgba(74,222,128,0.10)',  border: 'rgba(74,222,128,0.30)'  }, // 绿
]

const RANK_COL_LABELS: Record<string, string> = {
  rank_5d: '5日', rank_10d: '10日', rank_20d: '20日', rank_60d: '60日',
  rank_lu: '涨停', rank_board: '连板', rank_strong: '强势',
}

function SectorRankTag({ label, rank }: { label: string; rank: number }) {
  const style = RANK_TAG_STYLES[rank - 1]
  if (!style) return null
  return (
    <span
      className="inline-flex items-center px-1 py-px text-[9px] font-bold rounded whitespace-nowrap leading-tight"
      style={{ color: style.color, backgroundColor: style.bg, border: `1px solid ${style.border}` }}
    >
      {label}龙{rank}
    </span>
  )
}

/**
 * 渲染一个板块的全部排名 tag + 跌停预警。
 * tagData 来自 useSectorTags() 查询结果。
 */
export function SectorRankTags({ tagData }: { tagData: SectorTagData | undefined }) {
  if (!tagData) return null
  const tags: React.ReactNode[] = []

  const COLS: Array<keyof SectorTagData> = [
    'rank_5d', 'rank_10d', 'rank_20d', 'rank_60d', 'rank_lu', 'rank_board', 'rank_strong',
  ]
  for (const col of COLS) {
    const rank = tagData[col] as number | null
    if (rank != null && rank >= 1 && rank <= 5) {
      tags.push(
        <SectorRankTag key={col} label={RANK_COL_LABELS[col]} rank={rank} />
      )
    }
  }
  if (tagData.limit_down_count > 0) {
    tags.push(
      <span
        key="risk"
        className="inline-flex items-center gap-0.5 px-1 py-px text-[9px] font-bold rounded whitespace-nowrap leading-tight"
        style={{ color: '#26C281', backgroundColor: 'rgba(38,194,129,0.15)', border: '1px solid rgba(38,194,129,0.45)' }}
        title={`跌停 ${tagData.limit_down_count} 只，负反馈风险较大`}
      >
        ⚠ 跌停×{tagData.limit_down_count}
      </span>
    )
  }

  if (tags.length === 0) return null
  return <>{tags}</>
}

// ─── Convenience wrapper ──────────────────────────────────────────────────────

const DEFAULT_MAX = 6

export function SectorTagList({
  sectors,
  max = DEFAULT_MAX,
}: {
  sectors: string[]
  max?: number
}) {
  const visible  = sectors.slice(0, max)
  const overflow = sectors.length - max

  if (sectors.length === 0) return <span className="text-xs text-text-muted">—</span>

  return (
    <div className="flex flex-wrap gap-1">
      {visible.map((name) => <SectorTag key={name} name={name} />)}
      {overflow > 0 && <OverflowBadge count={overflow} hidden={sectors.slice(max)} />}
    </div>
  )
}
