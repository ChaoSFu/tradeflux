/**
 * MarketStateBar — 全局市场状态条（市场阶段/情绪温度/赚钱效应/涨跌停家数/建议仓位）
 * 复用到所有页面顶部。后续可在此扩展更多情绪温度关键指标。
 */
import { useQuery } from '@tanstack/react-query'
import { fetchMarketState, fetchProfitEffect } from '@/api/marketState'
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

        {pe?.has_data && (
          <Cell label="涨停 · 极端做多">
            <span className="font-mono text-base font-bold text-up">{pe.overall_limit_up_count}</span>
          </Cell>
        )}
        {pe?.has_data && (
          <Cell label="跌停 · 极端做空">
            <span className="font-mono text-base font-bold text-down">{pe.overall_limit_down_count}</span>
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
