import { useMemo, useState } from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ReferenceLine, ResponsiveContainer } from 'recharts'
import { format } from 'date-fns'
import { cn } from '@/utils/cn'
import type { UpDownSeriesPoint } from '@/api/marketTrend'

/**
 * 涨跌分档走势。样式参考「涨跌停概览 · 近期涨停/跌停走势」（实线当日值 + 虚线滚动
 * 均值），但粒度更细：六个分档，而不只是涨跌停两条。
 *
 * ## 为什么默认看「相对强度」而不是原始家数
 *
 * 六档的量级差得离谱——按 2026-07-20~08-28 的真实数据量过：
 *
 *     涨1~5% 中位 1555   涨>5% 236   涨停 78   跌1~5% 1163   跌>5% 96   跌停 4
 *     最大 / 最小 = 1555 / 4 = **389 倍**
 *
 * 六条线放同一个线性 Y 轴上，涨停和跌停会贴着底轴压成两条直线，而这两档恰恰是
 * 情绪最极端、最该看的。取对数轴能压回来，但对数轴上"涨了一倍"和"涨了 10 家"
 * 的视觉高度一样，读起来更容易错。
 *
 * 相对强度 = 当日值 ÷ 该档自己的滚动均值，把六条线全部归一到 1.0 附近，可以直接
 * 横向比较，而且正好回答用户要问的那个问题——**今天这个维度相对常态是强是弱**。
 *
 * 它还有个更重要的好处：**不需要任何拍脑袋的阈值**。「涨停 82 家」在热市里是常态、
 * 在冷市里是极端，同一个数字在不同环境下含义相反；除以它自己的近期均值，基准就
 * 自动跟着市场温度走了。（这跟涨停板块雷达拒绝加权打分是同一条原则：能从数据里
 * 长出来的基准，就不要人为拍一个。）
 *
 * 原始家数并没有被藏起来——切到「原始值」就是纯家数 + 虚线均值，tooltip 里也始终
 * 同时显示原始值和均值。
 *
 * ## 为什么不含 0~1% 和平盘
 *
 * 用户 2026-08-28 明确要求："除了振幅非常小 -1 到 1 的，其他都得关注。"
 * 那三档是中枢噪音，对"赚钱效应强不强"没有贡献，放进来只会用一堆一千多的数字
 * 把别的曲线压平。
 *
 * ## 窗口不满 MIN_WIN 天就不画
 *
 * 参考图那两条虚线在最左端是从高处急降下来的——那不是行情，是滚动窗口还没攒够
 * 数据的伪影（头几天的"30日均值"其实只有 1~5 天）。相对强度这边伪影更毒：第一天
 * 的值恒等于 v/v = 1.00，会画出一段"完全处于常态"的假象。
 * 所以窗口不足 MIN_WIN 天直接不出点，宁可线短一截。
 */

/** 与上方涨跌分布柱状图逐档同色——同一份数据换个看法，颜色必须能对上 */
const SERIES = [
  { key: '涨停',    field: 'limit_up'   as const, color: '#FF2D55', width: 2 },
  { key: '涨>5%',   field: 'up_gt5'     as const, color: '#FF4560', width: 1.5 },
  { key: '涨1~5%',  field: 'up_1_5'     as const, color: '#FF7A8A', width: 1.5 },
  { key: '跌1~5%',  field: 'down_1_5'   as const, color: '#4FD6A5', width: 1.5 },
  { key: '跌>5%',   field: 'down_gt5'   as const, color: '#26C281', width: 1.5 },
  { key: '跌停',    field: 'limit_down' as const, color: '#0E9F6E', width: 2 },
]

const WINDOW = 30   // 滚动窗口上限：近30个交易日
const MIN_WIN = 10  // 少于这么多天不出点，见上面注释

export default function ThrustTrendChart({ data, loading }: {
  data?: UpDownSeriesPoint[]
  loading?: boolean
}) {
  const [mode, setMode] = useState<'relative' | 'raw'>('relative')
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const toggle = (k: string) => setHidden((prev) => {
    const next = new Set(prev)
    next.has(k) ? next.delete(k) : next.add(k)
    return next
  })

  const rows = useMemo(() => {
    const raw = data ?? []
    const mapped = raw.map((p, i) => {
      const win = raw.slice(Math.max(0, i - WINDOW + 1), i + 1)
      const enough = win.length >= MIN_WIN
      const out: any = { date: format(new Date(p.date), 'MM/dd'), _n: win.length, _ok: enough }
      for (const s of SERIES) {
        const v = p[s.field] ?? 0
        const avg = win.reduce((a, q) => a + (q[s.field] ?? 0), 0) / win.length
        out[`${s.key}_原值`] = v
        out[`${s.key}_均值`] = enough ? Math.round(avg * 10) / 10 : null
        out[s.key] = mode === 'raw'
          ? v
          : (enough && avg > 0 ? Math.round((v / avg) * 100) / 100 : null)
      }
      return out
    })
    // 相对强度模式下把预热期那几天整行丢掉：它们一个点都画不出来，留着只会在左边
    // 空出一大片（27天数据时白白浪费三分之一的画布）。原始值模式保留全部——原始
    // 家数每天都有，只有那条均值虚线要等窗口攒够。
    return mode === 'relative' ? mapped.filter((r) => r._ok) : mapped
  }, [data, mode])

  // 相对强度轴自己排刻度：交给 recharts 自动分会排出 0.65x / 1.95x 这种读不出来的数。
  // 固定 0.5 一档（超过 4x 改 1 一档），保证 1.0 那条基准线一定落在刻度上。
  const relTicks = useMemo(() => {
    if (mode !== 'relative') return undefined
    let max = 0
    for (const r of rows) for (const s of SERIES) if (r[s.key] != null) max = Math.max(max, r[s.key])
    const step = max > 4 ? 1 : 0.5
    const top = Math.max(step * 2, Math.ceil(max / step) * step)
    const out: number[] = []
    for (let v = 0; v <= top + 1e-9; v += step) out.push(Math.round(v * 10) / 10)
    return out
  }, [rows, mode])

  const Tip = ({ active, payload, label }: any) => {
    if (!active || !payload?.length) return null
    const d = payload[0].payload
    return (
      <div className="bg-bg-elevated border border-bg-border rounded-lg px-3 py-2 shadow-xl">
        <div className="text-xs text-text-primary font-medium mb-1.5">{label}</div>
        <div className="space-y-0.5">
          {SERIES.filter((s) => !hidden.has(s.key)).map((s) => (
            <div key={s.key} className="flex items-center gap-2 text-[11px] whitespace-nowrap">
              <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: s.color }} />
              <span className="text-text-secondary w-14">{s.key}</span>
              <span className="font-mono text-text-primary w-11 text-right">{d[`${s.key}_原值`]}</span>
              {mode === 'relative' && (
                <span className="font-mono text-text-muted">
                  ÷ {d[`${s.key}_均值`] ?? '—'} ={' '}
                  <b className="text-text-primary">{d[s.key] != null ? `${d[s.key]}x` : '—'}</b>
                </span>
              )}
            </div>
          ))}
        </div>
        <div className="text-[10px] text-text-muted/70 mt-1.5">
          {d._n >= MIN_WIN ? `均值取近 ${d._n} 个交易日` : `仅 ${d._n} 天，不足 ${MIN_WIN} 天不计均值`}
        </div>
      </div>
    )
  }

  const btn = (m: 'relative' | 'raw', label: string, title: string) => (
    <button key={m} onClick={() => setMode(m)} title={title}
      className={cn('text-xs px-2 py-0.5 rounded border transition-colors',
        mode === m
          ? 'border-accent/50 text-accent bg-accent/10'
          : 'border-bg-border text-text-secondary hover:text-text-primary')}>
      {label}
    </button>
  )

  return (
    <div className="space-y-1.5 pt-1">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-semibold text-text-primary">分档走势</span>
          <span className="text-[10px] text-text-muted">
            {mode === 'relative' ? '当日 ÷ 近30日均值，1.0=常态' : '原始家数 · 虚线为近30日均值'}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {btn('relative', '相对强度', '当日值 ÷ 该档自己的滚动均值。六档量级差 389 倍（涨1~5%中位1555 vs 跌停中位4），原始家数放同一个轴上涨停跌停会贴底压成直线；归一之后 1.0 就是各自的常态水平，可以直接横向比强弱')}
          {btn('raw', '原始值', '各档原始家数 + 虚线滚动均值。量级差很大，小的几档会贴着底轴——想比较强弱切到「相对强度」')}
        </div>
      </div>

      <div className="h-48">
        {loading ? (
          <div className="h-full flex items-center justify-center text-text-muted text-xs">加载中…</div>
        ) : rows.length === 0 ? (
          /* 区分"一天数据都没有"和"有数据但不够算相对强度"——后者不是没数据，
             是这个口径还不成立，说清楚差多少天，别让人以为链路断了 */
          <div className="h-full flex items-center justify-center text-text-muted text-xs">
            {(data?.length ?? 0) > 0
              ? `已有 ${data!.length} 个交易日，不足 ${MIN_WIN} 天算不出相对强度——可切「原始值」先看家数`
              : '暂无历史数据'}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            {/* 原始值是四位数（涨1~5% 能到 3800+），轴要留够宽；相对强度只有 "1.5x"
                这种短标签，可以往左收一点省画布 */}
            <LineChart data={rows} margin={{ top: 4, right: 6, left: mode === 'raw' ? 0 : -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: '#737A96', fontSize: 10 }}
                     axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={24} />
              <YAxis tick={{ fill: '#737A96', fontSize: 10 }} axisLine={false} tickLine={false}
                     width={mode === 'raw' ? 46 : 40}
                     ticks={relTicks}
                     domain={relTicks ? [0, relTicks[relTicks.length - 1]] : undefined}
                     tickFormatter={(v: number) => (mode === 'relative' ? `${v}x` : `${v}`)} />
              <Tooltip content={<Tip />} />
              {/* 1.0 基准线：没有它就不知道"高"和"低"是相对什么说的 */}
              {mode === 'relative' && (
                <ReferenceLine y={1} stroke="#737A96" strokeDasharray="4 3" strokeOpacity={0.65} />
              )}
              <Legend wrapperStyle={{ fontSize: 11, color: '#A2A9C4', paddingTop: 2, cursor: 'pointer' }}
                      iconSize={8}
                      onClick={(e: any) => toggle(e?.dataKey ?? e?.value)}
                      formatter={(value: string) => (
                        <span style={{ opacity: hidden.has(value) ? 0.35 : 1 }}>{value}</span>
                      )} />
              {SERIES.map((s) => (
                <Line key={s.key} type="monotone" dataKey={s.key} stroke={s.color}
                      strokeWidth={s.width} dot={false} activeDot={{ r: 3 }}
                      connectNulls={false} hide={hidden.has(s.key)} />
              ))}
              {/* 原始值模式才画均值虚线；相对强度模式下均值恒等于 1.0，就是那条基准线 */}
              {mode === 'raw' && SERIES.map((s) => (
                <Line key={`${s.key}_均值`} type="monotone" dataKey={`${s.key}_均值`} stroke={s.color}
                      strokeWidth={1.2} strokeDasharray="5 4" strokeOpacity={0.45}
                      dot={false} activeDot={false} connectNulls={false}
                      hide={hidden.has(s.key)} legendType="none" />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  )
}
