/**
 * 市场效应 — 每日赚钱效应 / 亏钱效应（精简 MVP）
 * 核心逻辑：用前一交易日冻结的参与群体（昨日涨停/首板/连板/跌停/炸板/强势股近似）
 * 在当日的真实次日反馈，取代"今天涨得多=赚钱效应高"的粗糙统计，避免幸存者偏差。
 * 详见 docs/MARKET_EFFECT_SOLUTION.md。本页是精简版：固定权重评分，非滚动分位
 * 标准化；简化5态生命周期，非完整9态状态机。
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  fetchMarketEffectLatest, fetchMarketEffectByDate, fetchMarketEffectHistory, fetchCohortMembers,
} from '@/api/marketEffects'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from 'recharts'
import { AlertTriangle, ChevronDown } from 'lucide-react'
import type { CohortType, MarketEffectDailyResponse } from '@/types'

const C_UP   = '#FF4560'
const C_DOWN = '#26C281'

const QUADRANT_META: Record<string, { label: string; color: string; bg: string }> = {
  benign_spread:     { label: '良性扩散', color: C_UP,   bg: 'rgba(255,69,96,0.12)' },
  strong_divergence: { label: '强分歧',   color: '#F59E0B', bg: 'rgba(245,158,11,0.12)' },
  quiet_chaos:       { label: '缩量混沌', color: '#5EA6FF', bg: 'rgba(94,166,255,0.12)' },
  loss_spread:       { label: '负反馈扩散', color: C_DOWN, bg: 'rgba(38,194,129,0.12)' },
}

const LIFECYCLE_META: Record<string, string> = {
  loss_spreading:   '亏钱扩散',
  recovering:       '修复低迷',
  profit_confirmed: '赚钱确认',
  profit_spreading: '赚钱扩散',
  loss_warning:     '负反馈预警',
}

const COHORT_ORDER: CohortType[] = ['limit_up', 'first_board', 'multi_board', 'limit_down', 'broken_board', 'strong_proxy']

function Pct({ v }: { v: number | null }) {
  if (v == null) return <span className="text-text-muted">—</span>
  return (
    <span className={cn('font-mono', v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-muted')}>
      {v > 0 ? '+' : ''}{v.toFixed(2)}%
    </span>
  )
}

function RatioBar({ label, v, color }: { label: string; v: number | null; color: string }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="text-text-muted w-14 shrink-0">{label}</span>
      <div className="flex-1 h-1.5 rounded-full bg-bg-elevated overflow-hidden">
        {v != null && <div className="h-full rounded-full" style={{ width: `${v * 100}%`, backgroundColor: color }} />}
      </div>
      <span className="font-mono w-10 text-right shrink-0">{v != null ? `${Math.round(v * 100)}%` : '—'}</span>
    </div>
  )
}

export default function MarketEffects() {
  const [selDate, setSelDate] = useState<string>('')
  const [expandedCohort, setExpandedCohort] = useState<CohortType | null>(null)

  const { data: latest, isLoading: latestLoading } = useQuery({
    queryKey: ['market-effect-latest'],
    queryFn: fetchMarketEffectLatest,
    enabled: !selDate,
  })
  const { data: byDate, isLoading: byDateLoading } = useQuery({
    queryKey: ['market-effect-by-date', selDate],
    queryFn: () => fetchMarketEffectByDate(selDate),
    enabled: !!selDate,
  })
  const effect: MarketEffectDailyResponse | undefined = selDate ? byDate : latest
  const dataLoading = selDate ? byDateLoading : latestLoading

  const { data: history } = useQuery({
    queryKey: ['market-effect-history'],
    queryFn: () => fetchMarketEffectHistory(60),
  })

  const chartData = useMemo(
    () => (history ?? []).map((h) => ({ date: h.trade_date.slice(5), 赚钱效应: h.profit_strength, 亏钱效应: h.loss_strength })),
    [history],
  )

  const { data: members, isLoading: membersLoading } = useQuery({
    queryKey: ['market-effect-cohort-members', effect?.trade_date, expandedCohort],
    queryFn: () => fetchCohortMembers(effect!.trade_date, expandedCohort!),
    enabled: !!effect && !!expandedCohort,
  })

  return (
    <div className="space-y-4 animate-fade-in">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <h1 className="text-lg font-semibold text-text-primary">市场效应 · 赚钱效应与亏钱效应</h1>
        {history && history.length > 0 && (
          <select
            value={selDate}
            onChange={(e) => setSelDate(e.target.value)}
            className={cn('bg-bg-card border rounded-lg px-2.5 py-1.5 text-sm focus:outline-none cursor-pointer',
              selDate ? 'border-accent/50 text-accent' : 'border-bg-border text-text-primary')}
          >
            <option value="">最新（今日）</option>
            {[...history].reverse().map((h) => <option key={h.trade_date} value={h.trade_date}>{h.trade_date}</option>)}
          </select>
        )}
      </div>

      {dataLoading || !effect ? (
        <div className="card p-4"><LoadingRows /></div>
      ) : (
        <>
          {effect.breadth_source === 'tracked_pool' && (
            <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-warn/10 border border-warn/30 text-xs text-warn">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>
                全市场广度暂用「跟踪股票池」近似（曾进入涨跌停/强势池的股票），并非沪深京全部 A 股的实时数据；
                只有当日命中真实全市场同步数据时才会显示为「全市场」口径。
              </span>
            </div>
          )}

          {/* ── 顶部：双分数 + 象限 + 生命周期 ─────────────────────────────── */}
          <div className="flex items-stretch gap-3 flex-wrap">
            <div className="card flex-1 min-w-[160px] px-4 py-3 border bg-up/6 border-up/20">
              <div className="text-xs text-text-muted">赚钱效应强度</div>
              <div className="text-2xl font-bold font-mono text-up leading-none mt-0.5">{effect.profit_strength.toFixed(1)}</div>
            </div>
            <div className="card flex-1 min-w-[160px] px-4 py-3 border bg-down/6 border-down/20">
              <div className="text-xs text-text-muted">亏钱效应强度</div>
              <div className="text-2xl font-bold font-mono text-down leading-none mt-0.5">{effect.loss_strength.toFixed(1)}</div>
            </div>
            <div className="card flex-1 min-w-[160px] px-4 py-3 flex flex-col justify-center"
              style={{ backgroundColor: QUADRANT_META[effect.quadrant]?.bg }}>
              <div className="text-xs text-text-muted">四象限状态</div>
              <div className="text-lg font-bold mt-0.5" style={{ color: QUADRANT_META[effect.quadrant]?.color }}>
                {QUADRANT_META[effect.quadrant]?.label ?? effect.quadrant}
              </div>
            </div>
            <div className="card flex-1 min-w-[160px] px-4 py-3 flex flex-col justify-center">
              <div className="text-xs text-text-muted">生命周期（简化版）</div>
              <div className="text-lg font-bold text-text-primary mt-0.5">
                {LIFECYCLE_META[effect.lifecycle_state] ?? effect.lifecycle_state}
              </div>
            </div>
          </div>

          {/* ── 一句话结论 ─────────────────────────────────────────────────── */}
          <div className="card p-3 text-sm text-text-secondary">
            {effect.summary}
            <span className="text-xs text-text-muted ml-2">数据日期 {effect.trade_date}</span>
          </div>

          {/* ── 历史趋势 ───────────────────────────────────────────────────── */}
          <div className="card p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-text-primary">近期赚钱 / 亏钱效应走势</span>
              <span className="text-xs text-text-muted">近60个交易日</span>
            </div>
            <div className="h-48">
              {chartData.length === 0 ? (
                <div className="h-full flex items-center justify-center text-text-muted text-sm">暂无历史数据</div>
              ) : (
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={chartData} margin={{ top: 4, right: 8, left: -16, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
                    <XAxis dataKey="date" tick={{ fill: '#737A96', fontSize: 11 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
                    <YAxis tick={{ fill: '#737A96', fontSize: 11 }} axisLine={false} tickLine={false} width={32} domain={[0, 100]} />
                    <Tooltip contentStyle={{ backgroundColor: '#1A1F2E', border: '1px solid #262D40', fontSize: 12 }} />
                    <Legend wrapperStyle={{ fontSize: 12, color: '#A2A9C4', paddingTop: 4 }} />
                    <Line type="monotone" dataKey="赚钱效应" stroke={C_UP} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                    <Line type="monotone" dataKey="亏钱效应" stroke={C_DOWN} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </div>
          </div>

          {/* ── 冻结群体次日反馈 ───────────────────────────────────────────── */}
          <div className="space-y-2">
            <span className="text-sm font-semibold text-text-primary">冻结群体次日反馈</span>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {COHORT_ORDER.map((ct) => {
                const c = effect.cohorts[ct]
                if (!c) return null
                const isOpen = expandedCohort === ct
                return (
                  <div key={ct} className="card overflow-hidden p-0">
                    <button
                      onClick={() => setExpandedCohort(isOpen ? null : ct)}
                      className="w-full px-3 py-2.5 border-b border-bg-border/40 flex items-center gap-2 hover:bg-bg-elevated transition-colors"
                    >
                      <span className="font-semibold text-sm text-text-primary">{c.label}</span>
                      <span className="text-xs font-mono px-1.5 py-0.5 rounded bg-bg-elevated text-text-muted">
                        {c.member_count} 只
                      </span>
                      <ChevronDown className={cn('w-3.5 h-3.5 ml-auto text-text-muted transition-transform', isOpen && 'rotate-180')} />
                    </button>
                    <div className="p-3 space-y-2">
                      {c.valid_count === 0 ? (
                        <div className="text-xs text-text-muted py-2 text-center">
                          {c.member_count === 0 ? '当日无样本' : '次日数据暂缺'}
                        </div>
                      ) : (
                        <>
                          <div className="flex items-center justify-between text-xs">
                            <span className="text-text-muted">次日收益中位数</span>
                            <Pct v={c.median_pct_change} />
                          </div>
                          <RatioBar label="红盘率" v={c.red_ratio} color={C_UP} />
                          <RatioBar label="大亏率" v={c.large_loss_ratio} color={C_DOWN} />
                          {c.advance_ratio != null && <RatioBar label="晋级率" v={c.advance_ratio} color={C_UP} />}
                          {c.broken_ratio != null && <RatioBar label="断板率" v={c.broken_ratio} color={C_DOWN} />}
                          <div className="text-[10px] text-text-muted">样本 {c.valid_count}/{c.member_count} 只有次日数据</div>
                        </>
                      )}
                    </div>
                    {isOpen && (
                      <div className="border-t border-bg-border/40 max-h-64 overflow-y-auto">
                        {membersLoading ? (
                          <div className="p-3"><LoadingRows rows={3} /></div>
                        ) : !members?.length ? (
                          <div className="p-3 text-xs text-text-muted text-center">无成员明细</div>
                        ) : (
                          <table className="w-full text-xs">
                            <tbody>
                              {members.map((m, i) => (
                                <tr key={i} className="border-b border-bg-border/20 last:border-0">
                                  <td className="px-3 py-1.5 font-mono text-accent whitespace-nowrap">{m.code}</td>
                                  <td className="px-1 py-1.5 text-text-secondary truncate">{m.name}</td>
                                  <td className="px-3 py-1.5 text-right whitespace-nowrap">
                                    {m.has_outcome ? <Pct v={m.outcome_pct_change} /> : <span className="text-text-muted">—</span>}
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
