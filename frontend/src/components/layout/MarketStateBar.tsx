/**
 * MarketStateBar — 全局市场状态条（市场阶段/情绪温度/赚钱效应/涨跌停家数/建议仓位）
 * 复用到所有页面顶部。后续可在此扩展更多情绪温度关键指标。
 */
import { useState, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchMarketState, fetchProfitEffect, fetchMarketHistory } from '@/api/marketState'
import { fetchLimitMoves, fetchLimitMovesTrend, fetchStrongPool } from '@/api/stocks'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { MARKET_PHASE_LABELS, EMOTION_CYCLE_LABELS } from '@/utils/format'
import { useSectorTags } from '@/hooks/useSectorTags'
import { SectorTag } from '@/components/common/SectorTags'
import { SectorSection, buildSectorGroups } from '@/components/common/SectorSection'
import type { Stock } from '@/types'
import { cn } from '@/utils/cn'

const PHASE_BADGE: Record<string, 'up' | 'down' | 'warn' | 'dragon' | 'accent'> = {
  bull_frenzy: 'dragon', warm: 'up', neutral: 'accent', caution: 'warn', bear_fear: 'down',
}
const pctColor = (v: number) => (v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-secondary')
const pctSign = (v: number) => (v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`)

function ClickSector({ name, pct, active, onClick }: { name: string; pct?: number | null; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={cn('inline-flex items-center gap-0.5 rounded transition-shadow', active && 'ring-1 ring-accent')} title="查看该板块强势股">
      <SectorTag name={name} />
      {pct != null && (
        <span className={cn('text-[10px] font-mono font-medium', pctColor(pct))}>{pctSign(pct)}</span>
      )}
    </button>
  )
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="shrink-0">
      <p className="text-[10px] text-text-muted leading-none mb-1">{label}</p>
      <div className="flex items-center gap-1.5">{children}</div>
    </div>
  )
}

export function MarketStateBar() {
  const { data: state } = useQuery({ queryKey: ['market-state'], queryFn: fetchMarketState })
  const { data: pe } = useQuery({ queryKey: ['profit-effect'], queryFn: fetchProfitEffect })
  // 全市场涨跌停家数（与「涨跌停概览」同源同缓存）
  const { data: up } = useQuery({
    queryKey: ['limit-moves', 'limit_up'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up' }),
  } as any)
  const { data: down } = useQuery({
    queryKey: ['limit-moves', 'limit_down'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down' }),
  } as any)
  const limitUpCount = (up as any)?.items?.length ?? null
  const limitDownCount = (down as any)?.items?.length ?? null

  // 点击板块 → 展开该板块强势股列表（与板块赚钱效应点击一致）
  const navigate = useNavigate()
  const [expandedSector, setExpandedSector] = useState<string | null>(null)
  const { data: strongPool } = useQuery({
    queryKey: ['strong-pool-sector-analysis'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
  } as any)
  const sectorGroupMap = useMemo(() => {
    const seen = new Set<number>(); const merged: Stock[] = []
    for (const s of [
      ...((strongPool as any)?.items ?? []),
      ...((up as any)?.items ?? []),
      ...((down as any)?.items ?? []),
    ] as Stock[]) { if (!seen.has(s.id)) { seen.add(s.id); merged.push(s) } }
    return new Map(buildSectorGroups(merged).map((g) => [g.name, g]))
  }, [strongPool, up, down])
  const toggleSector = (name: string) => setExpandedSector((p) => (p === name ? null : name))

  // 30日均值（与走势图同源）→ 比值 = 当日 / 30日均值
  const { data: trend } = useQuery({
    queryKey: ['limit-moves-trend', 30],
    queryFn: () => fetchLimitMovesTrend(30),
  } as any)
  const last30: any[] = ((trend as any) ?? []).slice(-30)
  const avgUp30 = last30.length ? last30.reduce((s, p) => s + p.limit_up_count, 0) / last30.length : null
  const avgDown30 = last30.length ? last30.reduce((s, p) => s + p.limit_down_count, 0) / last30.length : null
  const upRatio = limitUpCount != null && avgUp30 ? limitUpCount / avgUp30 : null
  const downRatio = limitDownCount != null && avgDown30 ? limitDownCount / avgDown30 : null

  // 进攻板块：5日 / 10日涨幅排名前5（5日龙1~5 / 10日龙1~5）
  const { byName: sectorTags } = useSectorTags()
  const rankTop5 = (key: 'rank_5d' | 'rank_10d' | 'rank_20d') => {
    const arr: { name: string; rank: number }[] = []
    sectorTags.forEach((t, name) => {
      const r = t[key]
      if (r != null && r >= 1 && r <= 5) arr.push({ name, rank: r })
    })
    return arr.sort((a, b) => a.rank - b.rank)
  }
  const attack5 = rankTop5('rank_5d')
  const attack10 = rankTop5('rank_10d')
  const attack20 = rankTop5('rank_20d')
  // 归并：跨 5/10/20 日强出现次数（越多越有持续力），只取出现 ≥2 次的
  const sustained = (() => {
    const cnt = new Map<string, number>()
    for (const a of [attack5, attack10, attack20]) for (const s of a) cnt.set(s.name, (cnt.get(s.name) ?? 0) + 1)
    return [...cnt.entries()].filter(([, c]) => c >= 2).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  })()

  // 龙头分组赚钱效应：今日 avg_pct（来自 profit-effect）+ 30日均值（来自 market-history）
  const { data: history } = useQuery({ queryKey: ['market-history', 30], queryFn: () => fetchMarketHistory(30) })
  const groupAvg30: Record<string, number> = (() => {
    const acc: Record<string, { sum: number; n: number }> = {}
    for (const pt of (history ?? []) as any[]) {
      for (const g of (pt.profit_effect_groups ?? []) as any[]) {
        if ((g.stock_count ?? 0) <= 0) continue
        const a = acc[g.key] ?? { sum: 0, n: 0 }
        a.sum += g.avg_pct; a.n += 1; acc[g.key] = a
      }
    }
    const out: Record<string, number> = {}
    for (const k in acc) out[k] = acc[k].n ? acc[k].sum / acc[k].n : 0
    return out
  })()
  const groupToday: Record<string, any> = {}
  for (const g of ((pe as any)?.groups ?? [])) groupToday[g.key] = g

  if (!state) return null

  return (
    <>
    <div className="card px-4 py-2.5 border-l-4 mb-4" style={{ borderLeftColor: '#4F9CF9' }}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <Cell label="市场阶段">
          <Badge variant={PHASE_BADGE[state.market_phase] ?? 'accent'}>
            {MARKET_PHASE_LABELS[state.market_phase] ?? state.market_phase}
          </Badge>
          <span className="text-text-secondary text-xs">
            {EMOTION_CYCLE_LABELS[state.emotion_cycle] ?? state.emotion_cycle}
          </span>
        </Cell>

        <Cell label="情绪温度">
          <span className="font-mono text-base text-accent">{state.emotional_temperature.toFixed(0)}</span>
          <Progress value={state.emotional_temperature} className="w-20" />
        </Cell>

        <Cell label="赚钱效应">
          {pe?.has_data ? (
            <>
              <span className={cn('font-mono text-base font-bold', pctColor(pe.overall_avg_pct))}>
                {pctSign(pe.overall_avg_pct)}
              </span>
              <span className="text-xs text-text-muted">
                <span className="text-up">↑{pe.overall_up_count}</span>
                {' / '}
                <span className="text-down">↓{pe.overall_down_count}</span>
              </span>
            </>
          ) : <span className="text-text-muted text-xs">—</span>}
        </Cell>

        {(['limit_up', 'oscillation'] as const).map((key) => {
          const g = groupToday[key]
          if (!g || g.stock_count <= 0) return null
          const avg30 = groupAvg30[key]
          const ratio = avg30 && avg30 > 0 ? g.avg_pct / avg30 : null
          const label = key === 'limit_up' ? '涨停龙头赚钱' : '震荡龙头赚钱'
          return (
            <Cell key={key} label={label}>
              <span className={cn('font-mono text-base font-bold', pctColor(g.avg_pct))}>{pctSign(g.avg_pct)}</span>
              {ratio != null && (
                <span
                  title={`今日 ${pctSign(g.avg_pct)} / 30日均值 ${pctSign(avg30)} = ${ratio.toFixed(2)}（>1 强于近月均值）`}
                  className={cn('text-xs font-mono font-medium', ratio >= 1 ? 'text-up' : 'text-text-muted')}
                >
                  {ratio.toFixed(2)}×
                </span>
              )}
            </Cell>
          )
        })}

        {limitUpCount != null && (
          <Cell label="涨停 · 极端做多">
            <span className="font-mono text-base font-bold text-up">{limitUpCount}</span>
            {upRatio != null && (
              <span
                title={`当日涨停 ${limitUpCount} / 涨停30日均值 ${avgUp30!.toFixed(1)} = ${upRatio.toFixed(2)}（>1 做多意愿强）`}
                className={cn('text-xs font-mono font-medium', upRatio >= 1 ? 'text-up' : 'text-text-muted')}
              >
                {upRatio.toFixed(2)}×
              </span>
            )}
          </Cell>
        )}
        {limitDownCount != null && (
          <Cell label="跌停 · 极端做空">
            <span className="font-mono text-base font-bold text-down">{limitDownCount}</span>
            {downRatio != null && (
              <span
                title={`当日跌停 ${limitDownCount} / 跌停30日均值 ${avgDown30!.toFixed(1)} = ${downRatio.toFixed(2)}（远大于1 风险极大）`}
                className={cn('text-xs font-mono font-bold',
                  downRatio >= 2 ? 'text-down' : downRatio >= 1 ? 'text-down/80' : 'text-text-muted')}
              >
                {downRatio.toFixed(2)}×
              </span>
            )}
          </Cell>
        )}

        <Cell label="建议仓位">
          <span className="font-mono text-base text-warn">{state.suggested_position_level.toFixed(0)}%</span>
          <Progress value={state.suggested_position_level} className="w-16" color="#F59E0B" />
        </Cell>

        <div className="ml-auto text-[10px] text-text-muted/70 self-center">⚠️ 仅供辅助分析，不构成投资建议</div>
      </div>

      {/* 进攻板块：5日 / 10日涨幅前5 */}
      {(attack5.length > 0 || attack10.length > 0) && (
        <div className="mt-2 pt-2 border-t border-bg-border/40 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs">
          <span className="text-[10px] text-text-muted shrink-0">进攻板块</span>
          {attack5.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[10px] text-up shrink-0">5日强</span>
              {attack5.map((s) => <ClickSector key={`5-${s.name}`} name={s.name} pct={sectorTags.get(s.name)?.pct_today} active={expandedSector === s.name} onClick={() => toggleSector(s.name)} />)}
            </div>
          )}
          {attack10.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[10px] text-accent shrink-0">10日强</span>
              {attack10.map((s) => <ClickSector key={`10-${s.name}`} name={s.name} pct={sectorTags.get(s.name)?.pct_today} active={expandedSector === s.name} onClick={() => toggleSector(s.name)} />)}
            </div>
          )}
          {attack20.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[10px] text-warn shrink-0">20日强</span>
              {attack20.map((s) => <ClickSector key={`20-${s.name}`} name={s.name} pct={sectorTags.get(s.name)?.pct_today} active={expandedSector === s.name} onClick={() => toggleSector(s.name)} />)}
            </div>
          )}
        </div>
      )}

      {/* 持续板块：5/10/20 日强归并，出现≥2次（有持续力），次数越多越靠前 */}
      {sustained.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-[10px] text-text-muted shrink-0" title="在 5/10/20 日强势板块中出现 ≥2 次，体现持续力">持续板块</span>
          {sustained.map(([name, c]) => (
            <span key={`sus-${name}`} className="inline-flex items-center gap-1">
              <ClickSector name={name} pct={sectorTags.get(name)?.pct_today} active={expandedSector === name} onClick={() => toggleSector(name)} />
              <span className={cn('text-[10px] font-mono font-bold', c >= 3 ? 'text-up' : 'text-text-secondary')}>×{c}</span>
            </span>
          ))}
        </div>
      )}
      </div>

      {/* 点击板块展开：该板块强势股列表（沿用 SectorSection） */}
      {expandedSector && sectorGroupMap.get(expandedSector) && (
        <SectorSection
          group={sectorGroupMap.get(expandedSector)!}
          collapsed={false}
          onToggle={() => setExpandedSector(null)}
          onClickStock={(code) => navigate(`/stocks/${code}`)}
        />
      )}
    </>
  )
}
