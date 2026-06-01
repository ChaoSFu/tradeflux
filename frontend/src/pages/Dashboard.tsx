import { useState, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { fetchMarketState, fetchMarketHistory, fetchProfitEffect } from '@/api/marketState'
import { fetchStrongPool } from '@/api/stocks'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { EmotionChart } from '@/components/charts/EmotionChart'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { PhaseTag } from '@/components/common/PhaseTag'
import { RiskBadge } from '@/components/common/RiskBadge'
import { SectorSection, buildSectorGroups } from '@/components/common/SectorSection'
import {
  MARKET_PHASE_LABELS, EMOTION_CYCLE_LABELS, ACTION_LABELS,
  ACTION_COLORS, LEADER_TYPE_LABELS, SIGNAL_TYPE_LABELS,
  PHASE_COLORS, pct, PHASE_NAME_TO_NUM,
} from '@/utils/format'
import { cn } from '@/utils/cn'
import { TrendingUp, Zap, ChevronDown, ChevronUp } from 'lucide-react'
import type { RiskLevel, ProfitEffectGroup, SectorProfitEffect, Stock } from '@/types'
import { useSectorTags, type SectorTagData } from '@/hooks/useSectorTags'
import { SectorRankTags } from '@/components/common/SectorTags'

const PHASE_BADGE: Record<string, 'up' | 'down' | 'warn' | 'dragon' | 'accent'> = {
  bull_frenzy: 'dragon',
  warm: 'up',
  neutral: 'accent',
  caution: 'warn',
  bear_fear: 'down',
}

// ─── Profit effect helpers ────────────────────────────────────────────────────

function pctColor(v: number) {
  if (v > 0) return 'text-up'
  if (v < 0) return 'text-down'
  return 'text-text-secondary'
}

function pctSign(v: number) {
  return v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`
}

/** Horizontal stacked bar: up (green) | flat (muted) | down (red) */
function UpDownBar({ up, flat, down }: { up: number; flat: number; down: number }) {
  const total = up + flat + down
  if (total === 0) return <div className="h-2 rounded-full bg-bg-elevated w-full" />
  const upPct = (up / total) * 100
  const flatPct = (flat / total) * 100
  const downPct = (down / total) * 100
  return (
    <div className="flex h-2 rounded-full overflow-hidden w-full gap-px">
      {upPct > 0 && (
        <div className="bg-up rounded-l-full" style={{ width: `${upPct}%` }} />
      )}
      {flatPct > 0 && (
        <div className="bg-text-secondary/50" style={{ width: `${flatPct}%` }} />
      )}
      {downPct > 0 && (
        <div className="bg-down rounded-r-full" style={{ width: `${downPct}%` }} />
      )}
    </div>
  )
}

const GROUP_STYLES: Record<string, { border: string; dot: string }> = {
  limit_up:    { border: 'border-up/30',   dot: 'bg-up' },
  oscillation: { border: 'border-accent/30', dot: 'bg-accent' },
  weakening:   { border: 'border-warn/30',  dot: 'bg-warn' },
  broken:      { border: 'border-down/30',  dot: 'bg-down' },
}

function SectorRow({
  s,
  active,
  onClick,
  tagData,
}: {
  s: SectorProfitEffect
  active?: boolean
  onClick?: () => void
  tagData?: SectorTagData
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full flex items-center gap-3 px-2 py-1.5 rounded transition-colors text-left',
        active ? 'bg-accent/10 ring-1 ring-accent/30' : 'hover:bg-bg-elevated',
      )}
    >
      <div className="w-24 shrink-0">
        <div className="text-sm text-text-primary font-medium truncate">{s.sector_name}</div>
        {tagData && (
          <div className="flex flex-wrap gap-0.5 mt-0.5">
            <SectorRankTags tagData={tagData} />
          </div>
        )}
      </div>
      <div className="flex-1">
        <UpDownBar up={s.up_count} flat={s.stock_count - s.up_count - s.down_count} down={s.down_count} />
      </div>
      <span className={cn('text-sm font-mono font-medium w-16 text-right shrink-0', pctColor(s.sector_pct_today))}>
        {pctSign(s.sector_pct_today)}
      </span>
      <span className="text-xs text-text-muted w-20 text-center shrink-0 font-mono">
        <span className="text-up">{s.up_count}</span>
        <span className="text-text-muted/60 mx-0.5">/</span>
        <span className="text-text-muted">{s.stock_count - s.up_count - s.down_count}</span>
        <span className="text-text-muted/60 mx-0.5">/</span>
        <span className="text-down">{s.down_count}</span>
      </span>
      <span className={cn('text-sm font-mono font-medium w-16 text-right shrink-0', pctColor(s.avg_pct))}>
        {pctSign(s.avg_pct)}
      </span>
      {active
        ? <ChevronUp   className="w-3.5 h-3.5 text-accent shrink-0" />
        : <ChevronDown className="w-3.5 h-3.5 text-text-muted/40 shrink-0" />
      }
    </button>
  )
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [expandedSector, setExpandedSector] = useState<string | null>(null)

  const { data: state, isLoading: loadingState } = useQuery({
    queryKey: ['market-state'],
    queryFn: fetchMarketState,
  })
  const { data: history, isLoading: loadingHistory } = useQuery({
    queryKey: ['market-history', 30],
    queryFn: () => fetchMarketHistory(30),
  })
  const { data: pe } = useQuery({
    queryKey: ['profit-effect'],
    queryFn: fetchProfitEffect,
  })
  // Reuse same cache key as SectorPool — no extra network request when user visited SectorPool first
  const { data: poolData } = useQuery({
    queryKey: ['strong-pool-all-for-sector'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
  } as any)

  const allStocks: Stock[] = (poolData as any)?.items ?? []

  // sector name → SectorGroup (for expanding a clicked row)
  const sectorGroupMap = useMemo(() => {
    const groups = buildSectorGroups(allStocks)
    return new Map(groups.map((g) => [g.name, g]))
  }, [allStocks])

  const { byCode: sectorTagsByCode } = useSectorTags()

  const toggleSector = (name: string) =>
    setExpandedSector((prev) => (prev === name ? null : name))

  if (loadingState) return <LoadingSpinner />

  return (
    <div className="space-y-5 animate-fade-in">

      {/* ── Market State Banner ── */}
      {state && (
        <div className="card p-4 border-l-4" style={{ borderLeftColor: '#4F9CF9' }}>
          <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
            <div>
              <p className="label">市场阶段</p>
              <div className="flex items-center gap-2 mt-1">
                <Badge variant={PHASE_BADGE[state.market_phase] ?? 'accent'}>
                  {MARKET_PHASE_LABELS[state.market_phase] ?? state.market_phase}
                </Badge>
                <span className="text-text-secondary text-xs">
                  {EMOTION_CYCLE_LABELS[state.emotion_cycle] ?? state.emotion_cycle}
                </span>
              </div>
            </div>
            <div>
              <p className="label">情绪温度</p>
              <div className="mt-1 flex items-center gap-3">
                <span className="font-mono text-lg text-accent">{state.emotional_temperature.toFixed(0)}</span>
                <Progress value={state.emotional_temperature} className="w-24" />
              </div>
            </div>
            <div>
              <p className="label">赚钱效应</p>
              {pe?.has_data ? (
                <div className="mt-1 flex items-center gap-2">
                  <span className={cn('font-mono text-lg font-bold', pctColor(pe.overall_avg_pct))}>
                    {pctSign(pe.overall_avg_pct)}
                  </span>
                  <span className="text-xs text-text-muted">
                    <span className="text-up">↑{pe.overall_up_count}</span>
                    {' / '}
                    <span className="text-down">↓{pe.overall_down_count}</span>
                  </span>
                </div>
              ) : (
                <div className="mt-1 flex items-center gap-2">
                  <span className="font-mono text-up">{state.profit_effect_score.toFixed(0)}</span>
                  <span className="text-text-muted">/</span>
                  <span className="font-mono text-down">{state.loss_effect_score.toFixed(0)}</span>
                  <span className="text-xs text-text-muted">亏钱效应</span>
                </div>
              )}
            </div>
            <div>
              <p className="label">建议仓位</p>
              <div className="mt-1 flex items-center gap-3">
                <span className="font-mono text-lg text-warn">{state.suggested_position_level.toFixed(0)}%</span>
                <Progress value={state.suggested_position_level} className="w-20" color="#F59E0B" />
              </div>
            </div>
            <div className="ml-auto text-xs text-text-muted">
              ⚠️ 仅供辅助分析，不构成投资建议
            </div>
          </div>
        </div>
      )}

      {/* ════════════════════════════════════════════════════════════════════════
          赚钱效应模块
      ════════════════════════════════════════════════════════════════════════ */}
      {pe && (
        <div className="space-y-4">
          <h2 className="text-sm font-semibold text-text-secondary uppercase tracking-wider">
            赚钱效应
          </h2>

          {!pe.has_data ? (
            <div className="card p-6 text-center text-text-muted text-sm">暂无当日数据</div>
          ) : (
            <>
              {/* ── 整体赚钱效应 ── */}
              <div className="card p-4">
                <div className="flex flex-wrap items-start gap-6">
                  {/* 大数字 */}
                  <div>
                    <p className="label mb-1">当日均涨幅</p>
                    <span className={cn('text-3xl font-mono font-bold', pctColor(pe.overall_avg_pct))}>
                      {pctSign(pe.overall_avg_pct)}
                    </span>
                  </div>

                  {/* 涨跌分布 */}
                  <div className="flex-1 min-w-[200px]">
                    <div className="flex justify-between text-xs mb-1.5">
                      <span className="text-up">↑ {pe.overall_up_count} 涨</span>
                      <span className="text-text-muted">{pe.overall_flat_count} 平</span>
                      <span className="text-down">{pe.overall_down_count} 跌 ↓</span>
                    </div>
                    <UpDownBar
                      up={pe.overall_up_count}
                      flat={pe.overall_flat_count}
                      down={pe.overall_down_count}
                    />
                    <div className="flex gap-3 mt-2 text-xs text-text-muted">
                      <span>共 {pe.overall_up_count + pe.overall_flat_count + pe.overall_down_count} 只强势股</span>
                    </div>
                  </div>

                  {/* 涨跌停数 */}
                  <div className="flex gap-4 items-center">
                    <div className="text-center">
                      <p className="label mb-0.5">涨停</p>
                      <span className="text-xl font-mono font-bold text-up">{pe.overall_limit_up_count}</span>
                    </div>
                    <div className="w-px h-8 bg-border" />
                    <div className="text-center">
                      <p className="label mb-0.5">跌停</p>
                      <span className="text-xl font-mono font-bold text-down">{pe.overall_limit_down_count}</span>
                    </div>
                  </div>
                </div>
              </div>

              {/* ── 情绪曲线 ── */}
              <div className="card p-3 h-52">
                {loadingHistory ? (
                  <LoadingSpinner />
                ) : history?.length ? (
                  <EmotionChart data={history} />
                ) : (
                  <div className="text-center text-text-muted text-sm py-10">暂无历史数据</div>
                )}
              </div>

              {/* ── 分组赚钱效应 ── */}
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {pe.groups.map((g: ProfitEffectGroup) => {
                  const s = GROUP_STYLES[g.key] ?? { border: 'border-border', dot: 'bg-text-muted' }
                  return (
                    <div key={g.key} className={cn('card p-3 border', s.border)}>
                      <div className="flex items-center gap-1.5 mb-2">
                        <span className={cn('w-1.5 h-1.5 rounded-full shrink-0', s.dot)} />
                        <p className="text-xs text-text-secondary font-medium truncate">{g.label}</p>
                      </div>
                      <div className="flex items-baseline gap-1 mb-2">
                        <span className={cn('text-xl font-mono font-bold', pctColor(g.avg_pct))}>
                          {g.stock_count > 0 ? pctSign(g.avg_pct) : '--'}
                        </span>
                        {g.stock_count > 0 && (
                          <span className="text-xs text-text-muted">{g.stock_count}只</span>
                        )}
                      </div>
                      {g.stock_count > 0 ? (
                        <>
                          <UpDownBar up={g.up_count} flat={g.flat_count} down={g.down_count} />
                          <div className="flex justify-between text-xs mt-1.5 text-text-muted">
                            <span className="text-up">↑{g.up_count}</span>
                            <span>{g.flat_count}</span>
                            <span className="text-down">{g.down_count}↓</span>
                          </div>
                        </>
                      ) : (
                        <p className="text-xs text-text-muted mt-1">暂无数据</p>
                      )}
                    </div>
                  )
                })}
              </div>

              {/* ── 板块赚钱 / 亏钱效应（并列） ── */}
              {pe.sectors.length > 0 && (() => {
                const profitSectors = pe.sectors.filter((s: SectorProfitEffect) => s.avg_pct >= 0)
                const lossSectors = [...pe.sectors.filter((s: SectorProfitEffect) => s.avg_pct < 0)]
                  .sort((a, b) => a.avg_pct - b.avg_pct)
                const expandedGroup = expandedSector ? sectorGroupMap.get(expandedSector) : null

                return (
                  <>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                      <Card title={`板块赚钱效应 (${profitSectors.length})`}>
                        {profitSectors.length > 0 ? (
                          <div className="space-y-1 max-h-72 overflow-y-auto pr-1">
                            {profitSectors.map((s: SectorProfitEffect) => (
                              <SectorRow
                                key={s.sector_code}
                                s={s}
                                active={expandedSector === s.sector_name}
                                onClick={() => toggleSector(s.sector_name)}
                                tagData={sectorTagsByCode.get(s.sector_code)}
                              />
                            ))}
                          </div>
                        ) : (
                          <div className="text-center text-text-muted text-sm py-6">暂无上涨板块</div>
                        )}
                      </Card>

                      <Card title={`板块亏钱效应 (${lossSectors.length})`}>
                        {lossSectors.length > 0 ? (
                          <div className="space-y-1 max-h-72 overflow-y-auto pr-1">
                            {lossSectors.map((s: SectorProfitEffect) => (
                              <SectorRow
                                key={s.sector_code}
                                s={s}
                                active={expandedSector === s.sector_name}
                                onClick={() => toggleSector(s.sector_name)}
                                tagData={sectorTagsByCode.get(s.sector_code)}
                              />
                            ))}
                          </div>
                        ) : (
                          <div className="text-center text-text-muted text-sm py-6">暂无下跌板块</div>
                        )}
                      </Card>
                    </div>

                    {/* ── 展开的板块详情 ── */}
                    {expandedGroup && (
                      <SectorSection
                        group={expandedGroup}
                        collapsed={false}
                        onToggle={() => setExpandedSector(null)}
                        onClickStock={(code) => navigate(`/stocks/${code}`)}
                      />
                    )}
                  </>
                )
              })()}
            </>
          )}
        </div>
      )}

      {/* ── Active Sectors ── */}
      <Card title="活跃板块" className="overflow-auto max-h-64">
        {state?.active_sectors.length ? (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2">
            {state.active_sectors.map((s) => (
              <div key={s.sector_code} className="flex items-center justify-between gap-2 p-2 rounded bg-bg-elevated">
                <div>
                  <div className="text-sm font-medium text-text-primary">{s.sector_name}</div>
                  <div className="text-xs text-text-muted mt-0.5">
                    强股 {s.strong_stock_count} · 连板高度 {s.board_height}
                  </div>
                </div>
                <div className="text-right">
                  <PhaseTag phase={s.phase} />
                  <div className="text-xs font-mono text-accent mt-0.5">{s.emotion_score.toFixed(0)}</div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-center text-text-muted text-sm py-10">暂无活跃板块</div>
        )}
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* ── Dragon Leaders ── */}
        <Card title="龙头股" action={<TrendingUp className="w-3.5 h-3.5 text-dragon" />}>
          {state?.dragon_leaders.length ? (
            <div className="space-y-2">
              {state.dragon_leaders.slice(0, 5).map((l) => (
                <div key={l.stock_code} className="flex items-center justify-between gap-2 p-2 rounded bg-bg-elevated">
                  <div className="flex items-center gap-2 min-w-0">
                    <Badge variant="dragon">{LEADER_TYPE_LABELS[l.leader_type] ?? l.leader_type}</Badge>
                    <div>
                      <span className="font-medium text-sm text-text-primary">{l.stock_name}</span>
                      <span className="text-xs text-text-muted ml-1">{l.stock_code}</span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 text-xs">
                    <span className="text-text-muted">{l.sector_name}</span>
                    <span className="font-mono text-dragon">龙:{l.leader_score.toFixed(0)}</span>
                    <Progress value={l.risk_score} className="w-12" />
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center text-text-muted text-sm py-6">暂无龙头数据</div>
          )}
        </Card>

        {/* ── Weak-to-Strong ── */}
        <Card title="弱转强候选" action={<Zap className="w-3.5 h-3.5 text-accent" />}>
          {state?.weak_to_strong_candidates.length ? (
            <div className="space-y-2">
              {state.weak_to_strong_candidates.slice(0, 5).map((c) => (
                <div key={c.stock_code} className="p-2 rounded bg-bg-elevated">
                  <div className="flex items-center justify-between gap-2 mb-1">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm text-text-primary">{c.stock_name}</span>
                      <span className="text-xs text-text-muted">{c.stock_code}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <RiskBadge level={c.risk_level as RiskLevel} />
                      <span className={cn('text-xs font-medium', ACTION_COLORS[c.suggested_action])}>
                        {ACTION_LABELS[c.suggested_action]}
                      </span>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant="accent">{SIGNAL_TYPE_LABELS[c.signal_type] ?? c.signal_type}</Badge>
                    <div className="flex items-center gap-1 text-xs text-text-muted">
                      置信 <span className="font-mono text-accent ml-0.5">{c.confidence_score.toFixed(0)}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-center text-text-muted text-sm py-6">暂无弱转强信号</div>
          )}
        </Card>
      </div>

    </div>
  )
}
