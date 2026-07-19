/**
 * 大盘趋势分析 — 核心指数（上证/深成/创业板/科创50/北证50）
 * 均线体系（多空排列 / 关键均线 / 斜率 / 金叉死叉 / 乖离率）→ 趋势强度分 + 状态判定
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchMarketTrend } from '@/api/marketTrend'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import { format } from 'date-fns'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts'
import { TrendingUp, TrendingDown, AlertTriangle, BookOpen, ChevronDown, ChevronUp } from 'lucide-react'
import type { IndexTrendAnalysis, IndexSignal } from '@/types'

// ─── 状态配色（A股惯例：红强绿弱）────────────────────────────────────────────
const STATE_META: Record<string, { color: string; bg: string }> = {
  strong:  { color: '#FF4560', bg: 'rgba(255,69,96,0.12)' },
  bullish: { color: '#F59E0B', bg: 'rgba(245,158,11,0.12)' },
  range:   { color: '#5EA6FF', bg: 'rgba(94,166,255,0.12)' },
  bearish: { color: '#2FBF9F', bg: 'rgba(47,191,159,0.12)' },
  weak:    { color: '#26C281', bg: 'rgba(38,194,129,0.12)' },
}

const MA_COLORS: Record<string, string> = {
  '收盘': '#EDF0F5',
  'MA5': '#FFB020',
  'MA10': '#B47CFF',
  'MA20': '#5EA6FF',
  'MA60': '#FF4560',
  'MA120': '#737A96',
  'MA250': '#4A5068',
}

const ALIGN_LABEL: Record<string, { text: string; cls: string }> = {
  bull:  { text: '多头排列', cls: 'text-up bg-up/10 border-up/30' },
  bear:  { text: '空头排列', cls: 'text-down bg-down/10 border-down/30' },
  mixed: { text: '均线纠缠', cls: 'text-text-secondary bg-bg-elevated border-bg-border' },
}

function Pct({ v, className }: { v: number; className?: string }) {
  return (
    <span className={cn('font-mono', v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-muted', className)}>
      {v > 0 ? '+' : ''}{v.toFixed(2)}%
    </span>
  )
}

// ─── Chart tooltip ────────────────────────────────────────────────────────────
function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="card p-2.5 text-xs space-y-0.5 shadow-xl border border-bg-border/60 min-w-[150px]">
      <div className="text-text-muted mb-1">{label}</div>
      {payload.map((p: any) => (
        <div key={p.name} className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: p.color }} />
          <span className="text-text-secondary">{p.name}</span>
          <span className="font-mono ml-auto" style={{ color: p.color }}>{p.value?.toFixed?.(2) ?? '-'}</span>
        </div>
      ))}
    </div>
  )
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function MarketTrend() {
  const { data, isLoading } = useQuery({
    queryKey: ['market-trend'],
    queryFn: () => fetchMarketTrend(),
    staleTime: 5 * 60 * 1000,
  })

  const indices: IndexTrendAnalysis[] = data?.indices ?? []
  const [selCode, setSelCode] = useState<string | null>(null)
  const selected = useMemo(
    () => indices.find(x => x.code === selCode) ?? indices[0] ?? null,
    [indices, selCode],
  )

  // 图例显隐（默认隐藏长期均线，图面干净）
  const [hiddenLines, setHiddenLines] = useState<Set<string>>(new Set(['MA120', 'MA250']))
  const toggleLine = (key?: string) => {
    if (!key) return
    setHiddenLines(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
  }

  const chartData = useMemo(() => (
    (selected?.series ?? []).map(p => ({
      date: format(new Date(p.date), 'MM/dd'),
      '收盘': p.close, 'MA5': p.ma5, 'MA10': p.ma10, 'MA20': p.ma20,
      'MA60': p.ma60, 'MA120': p.ma120, 'MA250': p.ma250,
    }))
  ), [selected])

  const [showMethod, setShowMethod] = useState(false)

  return (
    <div className="space-y-4 animate-fade-in">

      {/* ── 顶部说明 ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <p className="text-xs text-text-muted">
          基于均线体系的经典趋势判定（多空排列 / 月线季线位置 / 斜率 / 金叉死叉 / 乖离率），
          <span className="text-text-secondary">趋势强度分构成透明可解释</span>，点击下方「判定方法说明」查看口径
        </p>
        {data?.updated_at && (
          <span className="text-xs text-text-muted shrink-0">
            数据时间 {format(new Date(data.updated_at), 'MM/dd HH:mm')}
          </span>
        )}
      </div>

      {/* 拉取失败提示（部分指数源暂不可用时仍展示其余） */}
      {(data?.errors?.length ?? 0) > 0 && (
        <div className="flex items-center gap-2 text-xs text-warn bg-warn/10 border border-warn/25 rounded-lg px-3 py-2">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          部分指数数据源暂不可用：{data!.errors.join('；')}
        </div>
      )}

      {/* ── 指数卡片 ─────────────────────────────────────────────────── */}
      {isLoading ? (
        <div className="card p-6"><LoadingRows /></div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-3">
          {indices.map(idx => {
            const meta = STATE_META[idx.state]
            const active = selected?.code === idx.code
            return (
              <div
                key={idx.code}
                onClick={() => setSelCode(idx.code)}
                className={cn(
                  'card p-4 cursor-pointer transition-all border',
                  active ? 'ring-1 ring-accent/50 border-accent/40' : 'border-bg-border hover:border-bg-border/80 hover:bg-bg-elevated/40',
                )}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <span className="text-sm font-semibold text-text-primary">{idx.name}</span>
                  <span
                    className="text-[10px] font-bold px-1.5 py-0.5 rounded border"
                    style={{ color: meta.color, backgroundColor: meta.bg, borderColor: `${meta.color}55` }}
                  >
                    {idx.state_label}
                  </span>
                </div>
                <div className="flex items-baseline gap-2">
                  <span className={cn('text-xl font-bold font-mono leading-none',
                    idx.pct_change > 0 ? 'text-up' : idx.pct_change < 0 ? 'text-down' : 'text-text-primary')}>
                    {idx.close.toFixed(2)}
                  </span>
                  <Pct v={idx.pct_change} className="text-xs" />
                </div>
                <div className="flex items-center gap-2 mt-1 text-[10px] text-text-muted">
                  <span>5日 <Pct v={idx.pct_5d} /></span>
                  <span>20日 <Pct v={idx.pct_20d} /></span>
                </div>
                {/* 趋势强度分条 */}
                <div className="mt-2.5">
                  <div className="flex items-center justify-between text-[10px] text-text-muted mb-1">
                    <span>趋势强度</span>
                    <span className="font-mono font-bold" style={{ color: meta.color }}>{idx.score}</span>
                  </div>
                  <div className="h-1.5 rounded-full bg-bg-border/40 overflow-hidden">
                    <div className="h-full rounded-full transition-all" style={{ width: `${idx.score}%`, backgroundColor: meta.color }} />
                  </div>
                </div>
                {/* 关键均线位置 */}
                <div className="flex flex-wrap gap-1 mt-2.5">
                  <span className={cn('text-[9px] font-bold px-1 py-px rounded border', ALIGN_LABEL[idx.alignment].cls)}>
                    {ALIGN_LABEL[idx.alignment].text}
                  </span>
                  {(['ma20', 'ma60'] as const).map(m => {
                    const above = m === 'ma20' ? idx.above_ma20 : idx.above_ma60
                    const name = m === 'ma20' ? '月线' : '季线'
                    return (
                      <span
                        key={m}
                        className={cn('text-[9px] font-bold px-1 py-px rounded border inline-flex items-center gap-0.5',
                          above ? 'text-up bg-up/10 border-up/30' : 'text-down bg-down/10 border-down/30')}
                      >
                        {above ? <TrendingUp className="w-2.5 h-2.5" /> : <TrendingDown className="w-2.5 h-2.5" />}
                        {above ? `站上${name}` : `跌破${name}`}
                      </span>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* ── 选中指数：均线图 + 指标 + 信号 ───────────────────────────── */}
      {selected && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* 均线图 */}
          <div className="card p-4 lg:col-span-2 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-text-primary">
                {selected.name} · 收盘与均线系统
              </span>
              <span className="text-xs text-text-muted">近120个交易日 · 点图例切换均线</span>
            </div>
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
                  <XAxis dataKey="date" tick={{ fill: '#737A96', fontSize: 11 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
                  <YAxis
                    tick={{ fill: '#737A96', fontSize: 11 }} axisLine={false} tickLine={false} width={52}
                    domain={[(min: number) => Math.floor(min * 0.995), (max: number) => Math.ceil(max * 1.005)]}
                    tickFormatter={(v: number) => v.toFixed(0)}
                  />
                  <Tooltip content={<ChartTooltip />} />
                  <Legend
                    wrapperStyle={{ fontSize: 12, color: '#A2A9C4', paddingTop: 4, cursor: 'pointer' }}
                    iconSize={8}
                    onClick={(e: any) => toggleLine(e?.dataKey ?? e?.value)}
                    formatter={(value: string, entry: any) => (
                      <span style={{ opacity: hiddenLines.has(entry?.dataKey ?? value) ? 0.35 : 1 }}>{value}</span>
                    )}
                  />
                  {Object.entries(MA_COLORS).map(([key, color]) => (
                    <Line
                      key={key}
                      type="monotone"
                      dataKey={key}
                      stroke={color}
                      strokeWidth={key === '收盘' ? 2.2 : 1.4}
                      strokeDasharray={key === 'MA120' || key === 'MA250' ? '5 4' : undefined}
                      dot={false}
                      activeDot={key === '收盘' ? { r: 3 } : false}
                      connectNulls
                      hide={hiddenLines.has(key)}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* 指标明细 + 近期信号 */}
          <div className="space-y-4">
            <div className="card p-4">
              <div className="text-sm font-semibold text-text-primary mb-2.5">{selected.name} · 趋势指标</div>
              <div className="space-y-2 text-xs">
                <Row label="均线排列">
                  <span className={cn('text-[10px] font-bold px-1.5 py-0.5 rounded border', ALIGN_LABEL[selected.alignment].cls)}>
                    {ALIGN_LABEL[selected.alignment].text}
                  </span>
                </Row>
                <Row label="收盘价位置">
                  <span className="font-mono text-text-secondary">
                    {(['ma5', 'ma10', 'ma20', 'ma60'] as const).map((m, i) => {
                      const above = selected[`above_${m}` as keyof IndexTrendAnalysis] as boolean
                      return (
                        <span key={m} className={cn(above ? 'text-up' : 'text-down')}>
                          {i > 0 && <span className="text-text-muted/50"> · </span>}
                          {m.toUpperCase()}{above ? '上' : '下'}
                        </span>
                      )
                    })}
                  </span>
                </Row>
                <Row label="MA20 斜率（5日）"><Pct v={selected.ma20_slope_pct} /></Row>
                <Row label="MA60 斜率（10日）"><Pct v={selected.ma60_slope_pct} /></Row>
                <Row label="乖离率 BIAS20">
                  <span className={cn('font-mono', Math.abs(selected.bias20) >= 5 ? 'text-warn font-bold' : 'text-text-secondary')}>
                    {selected.bias20 > 0 ? '+' : ''}{selected.bias20.toFixed(2)}%
                    {Math.abs(selected.bias20) >= 5 && (selected.bias20 > 0 ? '（超买）' : '（超跌）')}
                  </span>
                </Row>
              </div>
            </div>

            <div className="card p-4">
              <div className="text-sm font-semibold text-text-primary mb-2">近期信号（10个交易日内）</div>
              {selected.signals.length === 0 ? (
                <p className="text-xs text-text-muted py-2">近期无均线信号，趋势延续中</p>
              ) : (
                <div className="space-y-1.5">
                  {selected.signals.map((s: IndexSignal, i: number) => (
                    <div key={i} className="flex items-start gap-2 text-xs">
                      <span className={cn(
                        'w-1.5 h-1.5 rounded-full mt-1.5 shrink-0',
                        s.side === 'bull' ? 'bg-up' : s.side === 'bear' ? 'bg-down' : 'bg-warn',
                      )} />
                      <div className="min-w-0">
                        <span className="text-text-muted font-mono mr-1.5">{s.date.slice(5)}</span>
                        <span className={cn(
                          s.side === 'bull' ? 'text-up' : s.side === 'bear' ? 'text-down' : 'text-warn',
                        )}>{s.label}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ── 判定方法说明 ─────────────────────────────────────────────── */}
      <div className="card overflow-hidden">
        <button
          onClick={() => setShowMethod(v => !v)}
          className="w-full px-4 py-3 flex items-center gap-2 text-sm font-semibold text-text-primary hover:bg-bg-elevated/40 transition-colors"
        >
          <BookOpen className="w-4 h-4 text-accent" />
          判定方法说明（均线体系口径）
          {showMethod ? <ChevronUp className="w-4 h-4 ml-auto text-text-muted" /> : <ChevronDown className="w-4 h-4 ml-auto text-text-muted" />}
        </button>
        {showMethod && (
          <div className="px-4 pb-4 grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3 text-xs text-text-secondary leading-relaxed">
            <div>
              <div className="text-text-primary font-medium mb-1">① 均线多空排列（道氏趋势理论）</div>
              多头排列：收盘 &gt; MA5 &gt; MA10 &gt; MA20 &gt; MA60，各周期持仓成本递增，趋势向上且各级买盘获利，回调有支撑；空头排列反之；均线相互缠绕为震荡市，趋势信号可靠度下降。
            </div>
            <div>
              <div className="text-text-primary font-medium mb-1">② 关键均线：MA20 月线 / MA60 季线</div>
              MA20 约一个月持仓成本，是中期趋势线；MA60 约一个季度成本，被广泛视为中长期牛熊分界（生命线）。收盘价对这两条线的突破/跌破是中期趋势转换的主信号。
            </div>
            <div>
              <div className="text-text-primary font-medium mb-1">③ 均线斜率（趋势方向确认）</div>
              MA20 五日斜率、MA60 十日斜率为正表示趋势仍在推进。价格站上均线但均线本身走平或向下，大概率是反弹而非反转——位置与斜率需相互印证。
            </div>
            <div>
              <div className="text-text-primary font-medium mb-1">④ 金叉 / 死叉（动能变化）</div>
              MA5 上穿 MA20 为金叉（短期动能确认中期转强）、下穿为死叉。作为辅助确认信号使用，震荡市中频繁交叉时参考价值降低。
            </div>
            <div>
              <div className="text-text-primary font-medium mb-1">⑤ 乖离率 BIAS20（均值回归）</div>
              BIAS20 = (收盘 − MA20) / MA20。指数口径 |BIAS| ≥ 5% 视为短期超买/超跌：价格偏离成本线过远时，向均线回归（回踩或反抽）的概率显著加大，追涨杀跌风险高。
            </div>
            <div>
              <div className="text-text-primary font-medium mb-1">⑥ 趋势强度分（0-100，构成透明）</div>
              位置分40（站上 MA5/10/20/60 各+10）＋ 排列分20（MA5&gt;MA10 +7、MA10&gt;MA20 +7、MA20&gt;MA60 +6）＋ 斜率分20（MA20/MA60 斜率为正各+10）＋ 动能分20（5日涨幅为正+10、近3日金叉+10）。
              ≥75 强势 / 55-74 偏强 / 40-54 震荡 / 20-39 偏弱 / &lt;20 弱势。
            </div>
            <p className="md:col-span-2 text-text-muted/80 pt-1 border-t border-bg-border/40">
              以上为经典技术分析口径，反映的是趋势状态的客观描述而非未来预测；均线体系在单边趋势中有效性较高、震荡市中信号质量下降。仅供复盘参考，不构成投资建议。
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-text-muted">{label}</span>
      {children}
    </div>
  )
}
