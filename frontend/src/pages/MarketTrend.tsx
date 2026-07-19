/**
 * 大盘趋势分析 — 核心指数（上证/深成/创业板/科创50/北证50）
 * 均线体系（多空排列 / 关键均线 / 斜率 / 金叉死叉 / 乖离率）→ 趋势强度分 + 状态判定
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchMarketTrend, fetchWindvane } from '@/api/marketTrend'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import { format } from 'date-fns'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, ComposedChart, Bar, Cell, ReferenceLine,
} from 'recharts'
import { TrendingUp, TrendingDown, AlertTriangle, BookOpen, ChevronDown, ChevronUp } from 'lucide-react'
import type { IndexTrendAnalysis, IndexSignal, WindvaneResponse } from '@/types'

// ── 金额格式化 ───────────────────────────────────────────────────────────────
const wanyi = (v: number) => `${(v / 1e12).toFixed(2)}万亿`
const yi = (v: number) => `${(v / 1e8).toFixed(1)}亿`

// ─── 状态配色（A股惯例：红强绿弱）────────────────────────────────────────────
const STATE_META: Record<string, { color: string; bg: string }> = {
  strong:  { color: '#FF4560', bg: 'rgba(255,69,96,0.12)' },
  bullish: { color: '#F59E0B', bg: 'rgba(245,158,11,0.12)' },
  range:   { color: '#5EA6FF', bg: 'rgba(94,166,255,0.12)' },
  bearish: { color: '#2FBF9F', bg: 'rgba(47,191,159,0.12)' },
  weak:    { color: '#26C281', bg: 'rgba(38,194,129,0.12)' },
}

const MA_COLORS: Record<string, string> = {
  'MA5': '#FFB020',
  'MA10': '#B47CFF',
  'MA20': '#5EA6FF',
  'MA60': '#FF4560',
  'MA120': '#737A96',
  'MA250': '#4A5068',
}

// ─── 蜡烛图自定义 shape（recharts range Bar [low, high] 上绘制影线+实体）──────
function CandleShape(props: any) {
  const { x, width, y, height, payload } = props
  const open = payload._open, close = payload._close
  const high = payload._high, low = payload._low
  if (open == null || close == null || high == null || low == null || height <= 0) return null
  const span = high - low
  const k = span > 0 ? height / span : 0
  const up = close >= open
  const color = up ? '#FF4560' : '#26C281'
  const cx = x + width / 2
  const openY = y + (high - open) * k
  const closeY = y + (high - close) * k
  const bodyTop = Math.min(openY, closeY)
  const bodyH = Math.max(Math.abs(closeY - openY), 1)
  const bw = Math.max(width * 0.6, 2)
  return (
    <g>
      <line x1={cx} x2={cx} y1={y} y2={y + height} stroke={color} strokeWidth={1} />
      <rect x={cx - bw / 2} y={bodyTop} width={bw} height={bodyH} fill={color} />
    </g>
  )
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
  const row = payload[0]?.payload ?? {}
  const hasOHLC = row._open != null && row._close != null
  const candleUp = hasOHLC && row._close >= row._open
  const candleColor = candleUp ? '#FF4560' : '#26C281'
  return (
    <div className="card p-2.5 text-xs space-y-0.5 shadow-xl border border-bg-border/60 min-w-[150px]">
      <div className="text-text-muted mb-1">{label}</div>
      {payload.map((p: any) => {
        if (p.dataKey === 'K线') {
          if (!hasOHLC) return null
          return (
            <div key="kline" className="space-y-0.5">
              {[['开盘', row._open], ['最高', row._high], ['最低', row._low], ['收盘', row._close]].map(([n, v]) => (
                <div key={n as string} className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: candleColor }} />
                  <span className="text-text-secondary">{n}</span>
                  <span className="font-mono ml-auto" style={{ color: candleColor }}>{(v as number)?.toFixed(2)}</span>
                </div>
              ))}
            </div>
          )
        }
        return (
          <div key={p.name} className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-sm shrink-0" style={{ backgroundColor: p.color }} />
            <span className="text-text-secondary">{p.name}</span>
            <span className="font-mono ml-auto" style={{ color: p.color }}>{p.value?.toFixed?.(2) ?? '-'}</span>
          </div>
        )
      })}
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
      // 蜡烛：range Bar 数据域 [low, high]，OHLC 原值供 shape/tooltip 使用
      'K线': p.low != null && p.high != null ? [p.low, p.high] : undefined,
      _open: p.open, _close: p.close, _high: p.high, _low: p.low,
      'MA5': p.ma5, 'MA10': p.ma10, 'MA20': p.ma20,
      'MA60': p.ma60, 'MA120': p.ma120, 'MA250': p.ma250,
    }))
  ), [selected])

  const [showMethod, setShowMethod] = useState(false)

  // 市场风向标：融资融券 / 涨跌统计 / 成交分析
  const { data: wv } = useQuery({
    queryKey: ['market-windvane'],
    queryFn: () => fetchWindvane(),
    staleTime: 5 * 60 * 1000,
  })

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
                <ComposedChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
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
                  {/* 蜡烛：range Bar [low, high] + 自定义 shape（红涨绿跌） */}
                  <Bar dataKey="K线" isAnimationActive={false} shape={<CandleShape />} hide={hiddenLines.has('K线')} legendType="rect" fill="#FF4560" />
                  {Object.entries(MA_COLORS).map(([key, color]) => (
                    <Line
                      key={key}
                      type="monotone"
                      dataKey={key}
                      stroke={color}
                      strokeWidth={1.4}
                      strokeDasharray={key === 'MA120' || key === 'MA250' ? '5 4' : undefined}
                      dot={false}
                      activeDot={false}
                      connectNulls
                      hide={hiddenLines.has(key)}
                    />
                  ))}
                </ComposedChart>
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

      {/* ── 市场资金与盘面（融资融券 / 涨跌统计 / 成交分析）─────────── */}
      {wv && <WindvaneCards wv={wv} />}

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

// ─── 市场风向标三卡片（融资融券 / 涨跌统计 / 成交分析）───────────────────────

function WindvaneCards({ wv }: { wv: WindvaneResponse }) {
  const m = wv.margin
  const u = wv.updown
  const t = wv.turnover

  // 两融图数据（余额万亿 + 上证 + 净买入亿）
  const marginChart = useMemo(() => (
    (m?.series ?? []).map(p => ({
      date: format(new Date(p.date), 'MM/dd'),
      '两融余额': +(p.balance / 1e12).toFixed(3),
      '上证指数': p.szzs_close,
      '融资净买入': +(p.net_buy / 1e8).toFixed(1),
    }))
  ), [m])

  // 涨跌分布 9 档（与东财口径一致）
  const updownChart = useMemo(() => {
    if (!u) return []
    const su = (a: number, b: number) => u.up_buckets.slice(a, b).reduce((x, y) => x + y, 0)
    const sd = (a: number, b: number) => u.down_buckets.slice(a, b).reduce((x, y) => x + y, 0)
    return [
      { name: '涨停',   v: u.limit_up,   fill: '#FF2D55' },
      { name: '涨>5%',  v: su(5, 10),    fill: '#FF4560' },
      { name: '1~5%',   v: su(1, 5),     fill: '#FF7A8A' },
      { name: '0~1%',   v: su(0, 1),     fill: '#FFB3BC' },
      { name: '平盘',   v: u.flat,       fill: '#737A96' },
      { name: '-0~1%',  v: sd(0, 1),     fill: '#9FE8CB' },
      { name: '-1~5%',  v: sd(1, 5),     fill: '#4FD6A5' },
      { name: '跌>5%',  v: sd(5, 10),    fill: '#26C281' },
      { name: '跌停',   v: u.limit_down, fill: '#0E9F6E' },
    ]
  }, [u])

  // 成交额（万亿）
  const turnoverChart = useMemo(() => (
    (t?.series ?? []).map(p => ({
      date: format(new Date(p.date), 'MM/dd'),
      '成交额': +(p.amount / 1e12).toFixed(3),
    }))
  ), [t])
  const avg60WanYi = t ? +(t.avg60 / 1e12).toFixed(3) : 0
  const shrink = t ? t.today < t.prev : false

  const axis = { tick: { fill: '#737A96', fontSize: 10 }, axisLine: false, tickLine: false } as any

  return (
    <div className="space-y-3">
      <div className="flex items-baseline gap-2">
        <span className="text-sm font-semibold text-text-primary">市场资金与盘面</span>
        <span className="text-xs text-text-muted">融资融券 · 涨跌统计 · 成交分析（东财公开数据，收盘后口径）</span>
      </div>
      {wv.errors.length > 0 && (
        <div className="flex items-center gap-2 text-xs text-warn bg-warn/10 border border-warn/25 rounded-lg px-3 py-2">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          部分数据暂不可用：{wv.errors.join('；')}
        </div>
      )}
      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">

        {/* ── 融资融券 ─────────────────────────────────────────────── */}
        {m && (
          <div className="card p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-text-primary">融资融券</span>
              <span className="text-xs text-text-muted">截至 {m.latest_date.slice(5)}</span>
            </div>
            <div className="flex items-center gap-4">
              <div>
                <div className="text-[10px] text-text-muted">两融余额</div>
                <div className="text-lg font-bold font-mono text-accent leading-tight">{wanyi(m.balance)}</div>
              </div>
              <div>
                <div className="text-[10px] text-text-muted">融资净买入</div>
                <div className={cn('text-lg font-bold font-mono leading-tight', m.net_buy >= 0 ? 'text-up' : 'text-down')}>
                  {m.net_buy >= 0 ? '+' : ''}{yi(m.net_buy)}
                </div>
              </div>
            </div>
            {/* 上下两图 syncId 同步：光标滑动时数据联动；左右轴宽一致保证日期对齐 */}
            <div className="h-36">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={marginChart} syncId="margin-sync" margin={{ top: 2, right: 0, left: -6, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
                  <XAxis dataKey="date" {...axis} interval="preserveStartEnd" />
                  <YAxis yAxisId="l" {...axis} width={38} domain={['auto', 'auto']} tickFormatter={(v: number) => v.toFixed(2)} />
                  <YAxis yAxisId="r" {...axis} width={36} orientation="right" domain={['auto', 'auto']} tickFormatter={(v: number) => v.toFixed(0)} />
                  <Tooltip content={<ChartTooltip />} />
                  <Line yAxisId="l" type="monotone" dataKey="两融余额" stroke="#5EA6FF" strokeWidth={1.8} dot={false} />
                  <Line yAxisId="r" type="monotone" dataKey="上证指数" stroke="#FFB020" strokeWidth={1.4} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            {/* 净买入柱状条 */}
            <div className="h-14">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={marginChart} syncId="margin-sync" margin={{ top: 0, right: 0, left: -6, bottom: 0 }}>
                  <XAxis dataKey="date" hide />
                  <YAxis yAxisId="l" {...axis} width={38} tickFormatter={(v: number) => v.toFixed(0)} />
                  {/* 占位右轴：与上图右轴同宽，保证横向坐标对齐 */}
                  <YAxis yAxisId="r" orientation="right" width={36} tick={false} axisLine={false} tickLine={false} />
                  <Tooltip content={<ChartTooltip />} />
                  <ReferenceLine yAxisId="l" y={0} stroke="#262D40" />
                  <Bar yAxisId="l" dataKey="融资净买入" maxBarSize={4}>
                    {marginChart.map((p, i) => (
                      <Cell key={i} fill={p['融资净买入'] >= 0 ? '#FF4560' : '#26C281'} />
                    ))}
                  </Bar>
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <p className="text-[10px] text-text-muted/80 leading-relaxed">
              两融余额（蓝）通常与大盘同向：余额持续上升=杠杆资金看多；净买入（下方柱）连续为负代表融资盘撤退
            </p>
          </div>
        )}

        {/* ── 涨跌统计 ─────────────────────────────────────────────── */}
        {u && (
          <div className="card p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-text-primary">涨跌统计</span>
              <span className="text-xs text-text-muted">沪深京</span>
            </div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
              <div className="flex justify-between"><span className="text-text-muted">上涨</span><span className="font-mono font-bold text-up">{u.up}</span></div>
              <div className="flex justify-between"><span className="text-text-muted">下跌</span><span className="font-mono font-bold text-down">{u.down}</span></div>
              <div className="flex justify-between"><span className="text-text-muted">涨停 / 自然</span><span className="font-mono text-up">{u.limit_up} / {u.natural_limit_up}</span></div>
              <div className="flex justify-between"><span className="text-text-muted">跌停 / 自然</span><span className="font-mono text-down">{u.limit_down} / {u.natural_limit_down}</span></div>
            </div>
            <div className="h-44">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={updownChart} margin={{ top: 14, right: 0, left: -18, bottom: 0 }}>
                  <XAxis dataKey="name" {...axis} interval={0} tick={{ fill: '#737A96', fontSize: 9 }} />
                  <YAxis {...axis} />
                  <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                  <Bar dataKey="v" name="家数" maxBarSize={26} label={{ position: 'top', fill: '#A2A9C4', fontSize: 9 }}>
                    {updownChart.map((p, i) => <Cell key={i} fill={p.fill} />)}
                  </Bar>
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <p className="text-[10px] text-text-muted/80 leading-relaxed">
              自然涨停/跌停 = 剔除一字板后的数量，更能反映盘中真实做多/做空力量
            </p>
          </div>
        )}

        {/* ── 成交分析 ─────────────────────────────────────────────── */}
        {t && (
          <div className="card p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold text-text-primary">成交分析</span>
              <span className="text-xs text-text-muted">沪深两市 · 近60日</span>
            </div>
            <div className="flex items-center gap-4 text-xs">
              <div>
                <div className="text-[10px] text-text-muted">最新成交</div>
                <div className={cn('text-lg font-bold font-mono leading-tight', shrink ? 'text-down' : 'text-up')}>
                  {wanyi(t.today)}
                </div>
              </div>
              <div>
                <div className="text-[10px] text-text-muted">前一日</div>
                <div className="text-sm font-mono text-text-secondary leading-tight mt-1">{wanyi(t.prev)}</div>
              </div>
              <div>
                <div className="text-[10px] text-text-muted">60日均值</div>
                <div className="text-sm font-mono text-text-secondary leading-tight mt-1">{wanyi(t.avg60)}</div>
              </div>
            </div>
            <div className="h-[13.5rem]">
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart data={turnoverChart} margin={{ top: 4, right: 0, left: -6, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
                  <XAxis dataKey="date" {...axis} interval="preserveStartEnd" />
                  <YAxis {...axis} width={38} domain={['auto', 'auto']} tickFormatter={(v: number) => v.toFixed(1)} />
                  <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
                  <ReferenceLine y={avg60WanYi} stroke="#F59E0B" strokeDasharray="5 4" strokeOpacity={0.7} />
                  <Bar dataKey="成交额" maxBarSize={8}>
                    {turnoverChart.map((p, i) => (
                      <Cell key={i} fill={p['成交额'] >= avg60WanYi ? '#5EA6FF' : '#33415E'} />
                    ))}
                  </Bar>
                </ComposedChart>
              </ResponsiveContainer>
            </div>
            <p className="text-[10px] text-text-muted/80 leading-relaxed">
              虚线为60日均量：持续高于均量=市场活跃承接充分；缩量至均量下方=谨慎观望情绪升温
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
