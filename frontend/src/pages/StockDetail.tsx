import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchStock, fetchStockSnapshots } from '@/api/stocks'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { PhaseTag } from '@/components/common/PhaseTag'
import { StockPriceChart } from '@/components/charts/StockPriceChart'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { ArrowLeft, TrendingUp, ShieldAlert, Zap, BarChart2 } from 'lucide-react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { format } from 'date-fns'
import { cn } from '@/utils/cn'
import { pct, pctColor } from '@/utils/format'

export default function StockDetail() {
  const { code } = useParams<{ code: string }>()
  const navigate = useNavigate()

  const { data: stock, isLoading: loadingStock } = useQuery({
    queryKey: ['stock', code],
    queryFn: () => fetchStock(code!),
    enabled: !!code,
  })

  const { data: snapshots, isLoading: loadingSnaps } = useQuery({
    queryKey: ['stock-snapshots', code],
    queryFn: () => fetchStockSnapshots(code!, 30),
    enabled: !!code,
  })

  if (loadingStock) return <LoadingSpinner />
  if (!stock) return <div className="text-text-muted text-sm p-8">未找到该股票</div>

  const latest = snapshots?.at(-1)

  const scoreData = snapshots?.map((s) => ({
    date: format(new Date(s.date), 'MM/dd'),
    leader: s.leader_score,
    risk: s.risk_score,
    emotion: s.emotion_score,
    is_lu: s.is_limit_up,
  })) ?? []

  return (
    <div className="space-y-4 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => navigate(-1)}
          className="p-1.5 rounded text-text-muted hover:text-text-primary hover:bg-bg-elevated transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-text-primary">{stock.name}</h2>
            <span className="font-mono text-sm text-accent">{stock.code}</span>
            {stock.is_leader && <Badge variant="dragon">龙头</Badge>}
            {stock.in_strong_pool && <Badge variant="up">强股池</Badge>}
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-xs text-text-muted">
            <span>{stock.market}</span>
            <span>{stock.primary_sector ?? '未分类'}</span>
            <PhaseTag phase={stock.phase} />
          </div>
        </div>
      </div>

      {/* Score row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: '龙头得分', value: stock.leader_score, icon: <TrendingUp className="w-4 h-4" />, color: 'accent' as const, bar: '#4F9CF9' },
          { label: '风险得分', value: stock.risk_score, icon: <ShieldAlert className="w-4 h-4" />, color: 'down' as const, bar: undefined },
          { label: '情绪得分', value: stock.emotion_score, icon: <Zap className="w-4 h-4" />, color: 'dragon' as const, bar: '#FFD700' },
          { label: '60日最高连板', value: stock.board_count_60d, icon: <BarChart2 className="w-4 h-4" />, color: 'up' as const, bar: '#FF4560' },
        ].map(({ label, value, icon, color, bar }) => (
          <div key={label} className={cn('card p-4 border-l-2', {
            'border-accent/30': color === 'accent',
            'border-down/30': color === 'down',
            'border-dragon/30': color === 'dragon',
            'border-up/30': color === 'up',
          })}>
            <div className="flex items-start justify-between">
              <div>
                <p className="label">{label}</p>
                <div className="text-xl font-mono font-semibold text-text-primary mt-1">{typeof value === 'number' ? value.toFixed(0) : value}</div>
              </div>
              <div className="text-text-muted">{icon}</div>
            </div>
            {typeof value === 'number' && value <= 100 && <Progress value={value} className="mt-2" color={bar} />}
          </div>
        ))}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 md:grid-cols-6 gap-3">
        {[
          { label: '60日涨停', value: stock.limit_up_days_60d },
          { label: '10日涨停', value: stock.limit_up_days_10d },
          { label: '昨日涨跌', value: latest ? pct(latest.pct_change) : '--', color: latest ? pctColor(latest.pct_change) : '' },
          { label: '换手率', value: latest?.turnover_rate != null ? `${latest.turnover_rate.toFixed(1)}%` : '--' },
          { label: '昨收', value: latest?.close_price != null ? latest.close_price.toFixed(2) : '--' },
          { label: '昨日板高', value: latest?.board_count ?? '--' },
        ].map(({ label, value, color }) => (
          <div key={label} className="card-elevated p-3">
            <p className="label">{label}</p>
            <div className={cn('font-mono font-semibold mt-1', color || 'text-text-primary')}>{value}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Price chart */}
        <Card title="价格走势（近30日）" className="h-64">
          {loadingSnaps ? (
            <LoadingSpinner />
          ) : snapshots?.length ? (
            <div className="h-48">
              <StockPriceChart data={snapshots} />
            </div>
          ) : (
            <div className="text-center text-text-muted py-12 text-sm">暂无数据</div>
          )}
        </Card>

        {/* Score trend */}
        <Card title="得分趋势" className="h-64">
          <div className="h-48">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={scoreData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1E2538" />
                <XAxis dataKey="date" tick={{ fill: '#505570', fontSize: 11 }} axisLine={false} tickLine={false} />
                <YAxis domain={[0, 100]} tick={{ fill: '#505570', fontSize: 11 }} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ background: '#0F1117', border: '1px solid #1E2538', borderRadius: 6, fontSize: 11 }}
                />
                <Line type="monotone" dataKey="leader" stroke="#4F9CF9" strokeWidth={1.5} dot={false} name="龙头" />
                <Line type="monotone" dataKey="risk" stroke="#26C281" strokeWidth={1.5} dot={false} name="风险" />
                <Line type="monotone" dataKey="emotion" stroke="#FFD700" strokeWidth={1.5} dot={false} name="情绪" strokeDasharray="4 2" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      {/* Snapshot table */}
      <Card title="历史快照" className="overflow-hidden p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-bg-border">
                {['日期', '收盘', '涨跌%', '换手率', '连板', '涨停', '炸板', '弱转强', '龙头分', '风险分'].map(h => (
                  <th key={h} className="text-left px-3 py-2 text-text-muted font-medium whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(snapshots ?? []).slice().reverse().map((s) => (
                <tr key={s.id} className="border-b border-bg-border/40 hover:bg-bg-elevated">
                  <td className="px-3 py-1.5 font-mono text-text-muted">{s.date}</td>
                  <td className="px-3 py-1.5 font-mono">{s.close_price?.toFixed(2) ?? '--'}</td>
                  <td className={cn('px-3 py-1.5 font-mono', pctColor(s.pct_change))}>{pct(s.pct_change)}</td>
                  <td className="px-3 py-1.5 font-mono text-text-secondary">{s.turnover_rate?.toFixed(1) ?? '--'}%</td>
                  <td className="px-3 py-1.5 font-mono text-center">{s.board_count}</td>
                  <td className="px-3 py-1.5 text-center">{s.is_limit_up ? <span className="text-up">▲</span> : <span className="text-text-muted">·</span>}</td>
                  <td className="px-3 py-1.5 text-center">{s.is_broken_board ? <span className="text-down">✕</span> : <span className="text-text-muted">·</span>}</td>
                  <td className="px-3 py-1.5 text-center">{s.is_weak_to_strong ? <span className="text-accent">↗</span> : <span className="text-text-muted">·</span>}</td>
                  <td className="px-3 py-1.5 font-mono text-accent">{s.leader_score.toFixed(0)}</td>
                  <td className="px-3 py-1.5 font-mono text-down">{s.risk_score.toFixed(0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  )
}
