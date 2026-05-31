/**
 * Shared sector tag chips + overflow badge.
 * Used by StockPool, LimitMovesPool, and any other table that shows sector tags.
 */
import { useState, useRef } from 'react'

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
  return (
    <span className="inline-flex items-center px-1 py-px text-[9px] font-bold rounded bg-amber-500/15 text-amber-400 border border-amber-500/30 whitespace-nowrap leading-tight">
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
