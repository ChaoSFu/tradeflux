import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchSignals } from '@/api/signals'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { RiskBadge } from '@/components/common/RiskBadge'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import {
  SIGNAL_TYPE_LABELS, ACTION_LABELS, ACTION_COLORS, RISK_LABELS,
} from '@/utils/format'
import { cn } from '@/utils/cn'
import { Zap, Filter } from 'lucide-react'
import type { RiskLevel } from '@/types'
import { RegulatoryTag, YesterdayLimitTag, SevereTargetTag } from '@/components/common/SectorTags'
import { useRegulatoryStatus } from '@/hooks/useRegulatoryStatus'
import { useStockByCode } from '@/hooks/useStockByCode'
import { useSevereTargets } from '@/hooks/useSevereTargets'

const SIGNAL_TYPES = [
  { value: '', label: '全部类型' },
  { value: 'weak_to_strong', label: '弱转强' },
  { value: 'broken_board_recovery', label: '炸板修复' },
  { value: 'divergence_repair', label: '分歧修复' },
  { value: 'rebound_acceleration', label: '反弹加速' },
  { value: 'sector_repair_sync', label: '板块修复' },
]

const RISK_LEVELS = [
  { value: '', label: '全部风险' },
  { value: 'low', label: '低风险' },
  { value: 'medium', label: '中风险' },
  { value: 'high', label: '高风险' },
]

const CONF_COLOR = (n: number) =>
  n >= 75 ? 'text-up' : n >= 55 ? 'text-accent' : 'text-text-secondary'

export default function Signals() {
  const [signalType, setSignalType] = useState('')
  const [riskLevel, setRiskLevel] = useState('')
  const [page, setPage] = useState(1)

  const { data, isLoading } = useQuery({
    queryKey: ['signals', page, signalType, riskLevel],
    queryFn: () =>
      fetchSignals({
        page,
        page_size: 20,
        signal_type: signalType || undefined,
        risk_level: riskLevel || undefined,
      }),
  })

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / 20)
  const regStatus = useRegulatoryStatus()
  const stockByCode = useStockByCode()
  const severeTargets = useSevereTargets()

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Disclaimer */}
      <div className="flex items-start gap-2 p-3 rounded bg-warn-dim border border-warn/20 text-xs text-warn">
        <Zap className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>
          弱转强信号为辅助分析工具，仅供市场研究参考。
          建议结合板块整体走势和个股量价关系综合判断。⚠️ 不构成任何投资建议或买卖指令。
        </span>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <Filter className="w-3.5 h-3.5 text-text-muted" />
        <select
          value={signalType}
          onChange={(e) => { setSignalType(e.target.value); setPage(1) }}
          className="bg-bg-card border border-bg-border rounded px-3 py-1.5 text-sm text-text-secondary focus:outline-none focus:border-accent/50"
        >
          {SIGNAL_TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>
        <select
          value={riskLevel}
          onChange={(e) => { setRiskLevel(e.target.value); setPage(1) }}
          className="bg-bg-card border border-bg-border rounded px-3 py-1.5 text-sm text-text-secondary focus:outline-none focus:border-accent/50"
        >
          {RISK_LEVELS.map((r) => (
            <option key={r.value} value={r.value}>{r.label}</option>
          ))}
        </select>
        <div className="ml-auto text-xs text-text-muted">共 {total} 个信号</div>
      </div>

      <div className="space-y-2">
        {isLoading ? (
          <Card><LoadingRows /></Card>
        ) : items.length === 0 ? (
          <div className="text-center text-text-muted py-16 text-sm">暂无信号数据</div>
        ) : (
          items.map((sig) => (
            <div key={sig.id} className="card p-4 hover:bg-bg-elevated transition-colors">
              <div className="flex flex-wrap items-start gap-3">
                {/* Stock info */}
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap mb-2">
                    <span className="font-semibold text-text-primary">{sig.stock_name ?? '未知'}</span>
                    <span className="font-mono text-xs text-accent">{sig.stock_code}</span>
                    {regStatus.get(sig.stock_code ?? '') && <RegulatoryTag status={regStatus.get(sig.stock_code ?? '')!} />}
                    <SevereTargetTag target={severeTargets.get(sig.stock_code ?? '')?.target_rate} approach={severeTargets.get(sig.stock_code ?? '')?.approach} room={stockByCode.get(sig.stock_code ?? '')?.severe_up_room ?? null} />
                    {stockByCode.get(sig.stock_code ?? '')?.yesterday_is_limit_up && <YesterdayLimitTag dir="up" />}
                    {stockByCode.get(sig.stock_code ?? '')?.yesterday_is_limit_down && <YesterdayLimitTag dir="down" />}
                    {sig.sector_name && (
                      <Badge variant="muted">{sig.sector_name}</Badge>
                    )}
                    <Badge variant="accent">
                      {SIGNAL_TYPE_LABELS[sig.signal_type] ?? sig.signal_type}
                    </Badge>
                  </div>
                  {sig.explanation && (
                    <p className="text-xs text-text-secondary leading-relaxed">{sig.explanation}</p>
                  )}
                </div>

                {/* Scores */}
                <div className="flex flex-col items-end gap-2 shrink-0">
                  <div className="flex items-center gap-2">
                    <RiskBadge level={sig.risk_level as RiskLevel} />
                    <span className={cn('text-sm font-medium', ACTION_COLORS[sig.suggested_action])}>
                      {ACTION_LABELS[sig.suggested_action] ?? sig.suggested_action}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 text-xs">
                    <span className="text-text-muted">置信度</span>
                    <span className={cn('font-mono font-bold text-base', CONF_COLOR(sig.confidence_score))}>
                      {sig.confidence_score.toFixed(0)}
                    </span>
                    <span className="text-text-muted">/100</span>
                  </div>
                  <div className="text-xs text-text-muted font-mono">{sig.date}</div>
                </div>
              </div>

              {/* Confidence bar */}
              <div className="mt-2.5 flex items-center gap-2">
                <div className="flex-1 h-1 bg-bg-elevated rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${sig.confidence_score}%`,
                      backgroundColor:
                        sig.confidence_score >= 75 ? '#FF4560'
                          : sig.confidence_score >= 55 ? '#4F9CF9'
                          : '#505570',
                    }}
                  />
                </div>
                <span className="text-xs font-mono text-text-muted w-8 text-right">
                  {sig.confidence_score.toFixed(0)}%
                </span>
              </div>
            </div>
          ))
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between text-xs text-text-muted">
          <span>第 {page} / {totalPages} 页</span>
          <div className="flex gap-2">
            <button
              disabled={page === 1}
              onClick={() => setPage(p => p - 1)}
              className="px-3 py-1.5 rounded bg-bg-elevated hover:bg-bg-hover disabled:opacity-30"
            >上一页</button>
            <button
              disabled={page === totalPages}
              onClick={() => setPage(p => p + 1)}
              className="px-3 py-1.5 rounded bg-bg-elevated hover:bg-bg-hover disabled:opacity-30"
            >下一页</button>
          </div>
        </div>
      )}
    </div>
  )
}
