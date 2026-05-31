import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchReviews, fetchLatestReview, generateTodayReview } from '@/api/reviews'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { LoadingSpinner, LoadingRows } from '@/components/common/LoadingSpinner'
import {
  MARKET_PHASE_LABELS, EMOTION_CYCLE_LABELS,
} from '@/utils/format'
import { cn } from '@/utils/cn'
import { format } from 'date-fns'
import { zhCN } from 'date-fns/locale'
import { RefreshCw, BookOpen, TrendingUp, ShieldAlert, Eye } from 'lucide-react'
import type { DailyReview } from '@/types'

const PHASE_BADGE: Record<string, 'up' | 'down' | 'warn' | 'dragon' | 'accent'> = {
  bull_frenzy: 'dragon',
  warm: 'up',
  neutral: 'accent',
  caution: 'warn',
  bear_fear: 'down',
}

function ReviewCard({ review, isSelected, onClick }: {
  review: DailyReview
  isSelected: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'w-full text-left p-3 rounded border transition-all',
        isSelected
          ? 'border-accent/60 bg-bg-elevated'
          : 'border-bg-border hover:border-bg-hover hover:bg-bg-elevated card'
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="font-mono text-sm text-text-primary">
          {format(new Date(review.date), 'MM月dd日', { locale: zhCN })}
        </div>
        {review.market_phase && (
          <Badge variant={PHASE_BADGE[review.market_phase] ?? 'accent'} className="text-xs">
            {MARKET_PHASE_LABELS[review.market_phase] ?? review.market_phase}
          </Badge>
        )}
      </div>
      <div className="flex items-center gap-3 mt-1.5 text-xs">
        <span className="text-up font-mono">{review.profit_effect_score.toFixed(0)}</span>
        <span className="text-text-muted">/</span>
        <span className="text-down font-mono">{review.loss_effect_score.toFixed(0)}</span>
        <span className="text-text-muted ml-1">温 {review.emotional_temperature.toFixed(0)}</span>
      </div>
    </button>
  )
}

function ReviewDetail({ review }: { review: DailyReview }) {
  return (
    <div className="space-y-4">
      {/* Header metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="card-elevated p-3">
          <p className="label">市场阶段</p>
          <div className="mt-1">
            {review.market_phase && (
              <Badge variant={PHASE_BADGE[review.market_phase] ?? 'accent'}>
                {MARKET_PHASE_LABELS[review.market_phase] ?? review.market_phase}
              </Badge>
            )}
          </div>
        </div>
        <div className="card-elevated p-3">
          <p className="label">赚钱 / 亏钱效应</p>
          <div className="flex items-center gap-2 mt-1">
            <span className="font-mono text-up">{review.profit_effect_score.toFixed(0)}</span>
            <span className="text-text-muted">/</span>
            <span className="font-mono text-down">{review.loss_effect_score.toFixed(0)}</span>
          </div>
        </div>
        <div className="card-elevated p-3">
          <p className="label">情绪温度</p>
          <div className="flex items-center gap-2 mt-1">
            <span className="font-mono text-accent">{review.emotional_temperature.toFixed(0)}</span>
            <Progress value={review.emotional_temperature} className="flex-1" color="#4F9CF9" />
          </div>
        </div>
        <div className="card-elevated p-3">
          <p className="label">建议仓位</p>
          <div className="flex items-center gap-2 mt-1">
            <span className="font-mono text-warn">{review.suggested_position_level.toFixed(0)}%</span>
            <Progress value={review.suggested_position_level} className="flex-1" color="#F59E0B" />
          </div>
        </div>
      </div>

      {/* Sector summary */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="card p-3">
          <div className="flex items-center gap-1.5 mb-2">
            <TrendingUp className="w-3.5 h-3.5 text-up" />
            <span className="text-xs font-medium text-up">强势板块</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(review.strong_sectors ?? []).map((s) => (
              <Badge key={s} variant="up">{s}</Badge>
            ))}
            {!review.strong_sectors?.length && <span className="text-text-muted text-xs">暂无</span>}
          </div>
        </div>
        <div className="card p-3">
          <div className="flex items-center gap-1.5 mb-2">
            <ShieldAlert className="w-3.5 h-3.5 text-down" />
            <span className="text-xs font-medium text-down">危险板块</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(review.dangerous_sectors ?? []).map((s) => (
              <Badge key={s} variant="down">{s}</Badge>
            ))}
            {!review.dangerous_sectors?.length && <span className="text-text-muted text-xs">暂无</span>}
          </div>
        </div>
      </div>

      {/* Market summary narrative */}
      {review.market_summary && (
        <Card title="市场复盘" action={<BookOpen className="w-3.5 h-3.5 text-text-muted" />}>
          <pre className="whitespace-pre-wrap text-sm text-text-secondary leading-relaxed font-sans">
            {review.market_summary}
          </pre>
        </Card>
      )}

      {/* Tomorrow watchlist */}
      {review.tomorrow_watchlist && review.tomorrow_watchlist.length > 0 && (
        <Card title="明日关注" action={<Eye className="w-3.5 h-3.5 text-accent" />}>
          <div className="flex flex-wrap gap-2">
            {review.tomorrow_watchlist.map((code) => (
              <div key={code} className="px-2.5 py-1 rounded bg-accent-dim border border-accent/20 font-mono text-sm text-accent">
                {code}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

export default function DailyReview() {
  const qc = useQueryClient()
  const [selectedDate, setSelectedDate] = useState<string | null>(null)

  const { data: listData, isLoading: loadingList } = useQuery({
    queryKey: ['reviews'],
    queryFn: () => fetchReviews({ page: 1, page_size: 30 }),
  })

  const { data: latestReview, isLoading: loadingLatest } = useQuery({
    queryKey: ['review-latest'],
    queryFn: fetchLatestReview,
  })

  const generateMutation = useMutation({
    mutationFn: generateTodayReview,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['reviews'] })
      qc.invalidateQueries({ queryKey: ['review-latest'] })
    },
  })

  const reviews = listData?.items ?? []
  const displayReview = selectedDate
    ? reviews.find((r) => r.date === selectedDate)
    : latestReview

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex items-center justify-between">
        <div className="text-xs text-text-muted">
          共 {listData?.total ?? 0} 日复盘记录
        </div>
        <button
          onClick={() => generateMutation.mutate()}
          disabled={generateMutation.isPending}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-accent-dim border border-accent/30 text-accent text-sm hover:bg-accent/10 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={cn('w-3.5 h-3.5', generateMutation.isPending && 'animate-spin')} />
          生成今日复盘
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
        {/* Date list */}
        <div className="space-y-1.5 overflow-y-auto max-h-[80vh] pr-1">
          {loadingList ? (
            <LoadingRows rows={8} />
          ) : (
            reviews.map((r) => (
              <ReviewCard
                key={r.id}
                review={r}
                isSelected={selectedDate === r.date}
                onClick={() => setSelectedDate(selectedDate === r.date ? null : r.date)}
              />
            ))
          )}
        </div>

        {/* Review detail */}
        <div className="lg:col-span-3">
          {loadingLatest ? (
            <LoadingSpinner />
          ) : displayReview ? (
            <>
              <div className="flex items-center gap-2 mb-4">
                <h2 className="font-semibold text-text-primary">
                  {format(new Date(displayReview.date), 'yyyy年MM月dd日', { locale: zhCN })}
                </h2>
                {displayReview.emotion_cycle && (
                  <Badge variant="accent">
                    {EMOTION_CYCLE_LABELS[displayReview.emotion_cycle] ?? displayReview.emotion_cycle}
                  </Badge>
                )}
              </div>
              <ReviewDetail review={displayReview} />
            </>
          ) : (
            <div className="flex items-center justify-center h-64 text-text-muted text-sm">
              点击左侧日期查看复盘，或点击"生成今日复盘"
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
