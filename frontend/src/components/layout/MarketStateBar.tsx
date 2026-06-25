/**
 * MarketStateBar — 全局市场状态条（市场阶段/情绪温度/赚钱效应/涨跌停家数/建议仓位）
 * 复用到所有页面顶部。后续可在此扩展更多情绪温度关键指标。
 */
import { useQuery } from '@tanstack/react-query'
import { fetchMarketState, fetchProfitEffect } from '@/api/marketState'
import { fetchLimitMoves, fetchLimitMovesTrend } from '@/api/stocks'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { MARKET_PHASE_LABELS, EMOTION_CYCLE_LABELS } from '@/utils/format'
import { cn } from '@/utils/cn'

const PHASE_BADGE: Record<string, 'up' | 'down' | 'warn' | 'dragon' | 'accent'> = {
  bull_frenzy: 'dragon', warm: 'up', neutral: 'accent', caution: 'warn', bear_fear: 'down',
}
const pctColor = (v: number) => (v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-secondary')
const pctSign = (v: number) => (v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`)

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

  if (!state) return null

  return (
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
    </div>
  )
}
