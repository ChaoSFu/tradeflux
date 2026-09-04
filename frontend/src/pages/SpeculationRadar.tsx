/**
 * 破局雷达 / Speculation Regime Radar
 *
 * 回答的不是"今天哪只股票最强"，而是：**市场原本的高度天花板，是不是正在被打开？**
 *
 * 页面只有两张图，但它们必须一起看：
 *   上：高度前沿曲线 —— 天花板在哪，有没有被突破
 *   下：连板梯队热力图 —— 突破的时候，中位梯队有没有跟着变厚
 *
 * 一个 8 板 ≠ 市场进入 8 板周期，可能只是一只孤零零的妖股。**高度上移且梯队同时
 * 变厚，才叫高度扩张。** 所以两张图共用同一条时间轴，纵向对齐着读。
 *
 * 2026-08-06 是教科书样本：最高板 10 破 9 板上沿，同一天 3 板以上从 4 只跳到 13 只。
 * 四个突破日里只有它同时满足两个条件。
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceDot,
} from 'recharts'
import { AlertTriangle, Info, ChevronDown, ChevronUp } from 'lucide-react'
import { format } from 'date-fns'
import { fetchHeightSeries, type HeightPoint } from '@/api/marketTrend'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'

/** 热力图行标签宽度，同时也是折线图 Y 轴宽度——两张图靠它对齐 */
const LABEL_W = 46
const C_UP = '#FF2D55'

export default function SpeculationRadar() {
  const { data, isLoading } = useQuery({
    queryKey: ['speculation-height'],
    queryFn: () => fetchHeightSeries(66),
    staleTime: 30 * 60 * 1000,
  })

  const [showWarn, setShowWarn] = useState(false)
  const points = data?.points ?? []
  const chart = useMemo(() => points.map((p) => ({
    date: format(new Date(p.date), 'MM/dd'),
    _p: p,
    '最高连板': p.height,
    '20日上沿': p.frontier,
  })), [points])

  /** 梯队里出现过的板级，从高到低。上限跟着实际最高板走，不写死 */
  const levels = useMemo(() => {
    const s = new Set<string>()
    for (const p of points) for (const k of Object.keys(p.ladder)) s.add(k)
    // "8+" 要排在 "8" 之上。直接 parseInt 两者都得 8，排序结果不稳定，
    // 实测就把封顶那一档排到了 8 板下面——梯队图最高一格跑到中间去了
    const rank = (k: string) => parseInt(k) + (k.endsWith('+') ? 0.5 : 0)
    return [...s].sort((a, b) => rank(b) - rank(a))
  }, [points])

  /**
   * 每一行按**自己**的最大值归一，不做全局归一。
   * 首板动辄五六十只、8板常年只有 1 只，全局归一之后除了首板那行全是黑的——
   * 而"2板这两天比平时厚"恰恰是这张图要回答的问题。跟涨跌分档走势里用相对强度
   * 解决 389 倍量级差是同一个道理。
   */
  const rowMax = useMemo(() => {
    const m: Record<string, number> = {}
    for (const lv of levels) m[lv] = Math.max(1, ...points.map((p) => p.ladder[lv] ?? 0))
    return m
  }, [levels, points])

  const last = points[points.length - 1]
  // 只画**确认**的突破。is_breakout === null 是"不知道"（上沿窗口里有天缺数据，
  // 上沿可能被低估），把它画成突破点就是把不确定当成结论
  const breakouts = points.filter((p) => p.is_breakout === true)
  const unknownBreakout = points.filter((p) => p.is_breakout === null && p.has_data).length

  return (
    <div className="space-y-4">
      {/* ── 口径声明：不写在页面上，这张图就会被当成全市场高度 ── */}
      <div className="card p-3 flex items-start gap-2 text-[11px] text-text-secondary leading-relaxed">
        <Info className="w-3.5 h-3.5 shrink-0 mt-0.5 text-text-muted" />
        <div>
          <span className="text-text-primary font-medium">口径：</span>
          {data?.scope_note ?? '不含 ST 股'}；
          <span className="text-text-primary">不含退市整理期股票</span>（0.4 元的「退」字股连 5 板不是投机高度）；
          <span className="text-text-primary">不含北交所</span>（30% 涨跌幅，3 连板 = +120%，与主板 +33% 不可比）。
          上沿取近 {data?.frontier_window ?? 20} 个交易日，<span className="text-text-primary">窗口不满时不给上沿、也不判突破</span>。
        </div>
      </div>

      {/* 默认折叠：实测 18 条里绝大多数是"保留"类的信息条目，全摊开会把两张图整个
          挤到折叠线以下。但**不能不给**——剔了什么、缺了哪几天，看图的人有权知道 */}
      {data?.warnings?.length ? (
        <div className="card overflow-hidden border-warn/30">
          <button onClick={() => setShowWarn((v) => !v)}
                  className="w-full flex items-center justify-between gap-2 px-3 py-2 hover:bg-bg-elevated/40 transition-colors">
            <span className="flex items-center gap-1.5 text-xs text-warn font-medium">
              <AlertTriangle className="w-3.5 h-3.5" />
              数据说明 {data.warnings.length} 条
              <span className="text-text-muted font-normal">
                （{data.warnings.filter((w) => w.includes('已剔除')).length} 条已剔除，
                其余为无法验证但保留）
              </span>
            </span>
            {showWarn ? <ChevronUp className="w-3.5 h-3.5 text-text-muted" />
                      : <ChevronDown className="w-3.5 h-3.5 text-text-muted" />}
          </button>
          {showWarn && (
            <div className="px-3 pb-2 space-y-1">
              {data.warnings.map((w, i) => (
                <div key={i} className={cn('text-[11px] leading-relaxed',
                  w.includes('已剔除') ? 'text-warn/90' : 'text-text-secondary')}>· {w}</div>
              ))}
            </div>
          )}
        </div>
      ) : null}

      {/* ── 今日快照 ── */}
      {last && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat label="当日最高连板" value={last.height} suffix="板"
                hint={last.is_breakout === true ? `突破 ${last.frontier} 板上沿`
                  : last.is_breakout === null
                    ? `上沿窗口只覆盖 ${last.frontier_covered}/${data?.frontier_window ?? 20} 天，无法判定突破`
                    : `上沿 ${last.frontier ?? '—'} 板`}
                accent={last.is_breakout === true} />
          <Stat label="天花板附近" value={last.near_top_count} suffix="只"
                hint={`板数 ≥ ${Math.max(1, last.height - 1)}，看最高板是不是孤票`} />
          <Stat label="3 板以上" value={last.multi_board_count} suffix="只"
                hint="中高位赚钱效应的扩散程度" />
          <Stat label="涨停 / 梯队覆盖" value={last.limit_up_count} suffix={`/ ${last.ladder_count}`}
                hint={last.limit_up_count === last.ladder_count
                  ? '两个口径一致'
                  : `差 ${last.limit_up_count - last.ladder_count} 只无连板数据`} />
        </div>
      )}

      {/* ── 高度前沿曲线 ── */}
      <div className="card p-4 space-y-2">
        <div className="flex items-baseline justify-between flex-wrap gap-2">
          <span className="text-sm font-semibold text-text-primary">市场高度前沿</span>
          <span className="text-xs text-text-muted">
            实线=当日最高连板，虚线=近{data?.frontier_window ?? 20}日上沿，圈=突破
            {unknownBreakout > 0 && (
              <span className="text-warn ml-1">
                · {unknownBreakout} 天因上沿窗口缺数据无法判定
              </span>
            )}
          </span>
        </div>
        <div className="h-56">
          {isLoading ? <LoadingRows /> : points.length === 0 ? (
            <div className="h-full flex items-center justify-center text-text-muted text-sm">暂无数据</div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={chart} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
                <XAxis dataKey="date" tick={{ fill: '#737A96', fontSize: 10 }}
                       axisLine={false} tickLine={false} interval="preserveStartEnd" minTickGap={28} />
                <YAxis width={LABEL_W} tick={{ fill: '#737A96', fontSize: 10 }}
                       axisLine={false} tickLine={false} allowDecimals={false}
                       tickFormatter={(v: number) => `${v}板`} />
                <Tooltip content={<HeightTip />} />
                {/* 上沿在窗口不满时是 null —— connectNulls 必须关掉，
                    否则会画出一段并不存在的基线，正是这张图最不能骗人的地方 */}
                <Line type="stepAfter" dataKey="20日上沿" stroke="#737A96" strokeWidth={1.5}
                      strokeDasharray="5 4" dot={false} connectNulls={false} />
                <Line type="monotone" dataKey="最高连板" stroke={C_UP} strokeWidth={2}
                      dot={false} activeDot={{ r: 4 }} />
                {breakouts.map((p) => (
                  <ReferenceDot key={p.date} x={format(new Date(p.date), 'MM/dd')} y={p.height}
                                r={5} fill="none" stroke={C_UP} strokeWidth={2} />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </div>
        {breakouts.length > 0 && (
          <div className="text-[11px] text-text-secondary leading-relaxed pt-1 border-t border-bg-border">
            <span className="text-text-primary font-medium">突破日 {breakouts.length} 个：</span>
            {breakouts.map((p) => (
              <span key={p.date} className="ml-2 whitespace-nowrap">
                {p.date.slice(5)} <b className="text-up">{p.height}板</b>
                <span className="text-text-muted">破{p.frontier}板 · 3板以上{p.multi_board_count}只</span>
              </span>
            ))}
            <div className="text-text-muted mt-1">
              高度创新高<b className="text-text-secondary">且</b>梯队同时变厚才叫扩张——
              只看最高板会把孤零零一只妖股当成周期启动。
            </div>
          </div>
        )}
      </div>

      {/* ── 连板梯队热力图 ── */}
      <div className="card p-4 space-y-2">
        <div className="flex items-baseline justify-between flex-wrap gap-2">
          <span className="text-sm font-semibold text-text-primary">连板梯队</span>
          <span className="text-xs text-text-muted">每行按自己的峰值着色（各板级只数量级差几十倍）</span>
        </div>
        {points.length === 0 ? (
          <div className="text-text-muted text-sm py-6 text-center">暂无数据</div>
        ) : (
          <div className="overflow-x-auto">
            <div style={{ minWidth: LABEL_W + points.length * 11 }}>
              {levels.map((lv) => (
                <div key={lv} className="flex items-center" style={{ height: 18 }}>
                  <div className="text-[10px] text-text-muted text-right pr-1.5 shrink-0"
                       style={{ width: LABEL_W }}>{lv}板</div>
                  <div className="flex gap-px flex-1">
                    {points.map((p) => {
                      const n = p.ladder[lv] ?? 0
                      const ratio = n / rowMax[lv]
                      return (
                        <div key={p.date} className="flex-1 rounded-[1px]"
                             style={{
                               height: 15,
                               background: n === 0 ? '#1A1F30'
                                 : `rgba(255,45,85,${0.15 + ratio * 0.85})`,
                             }}
                             title={`${p.date}  ${lv}板 ${n} 只`} />
                      )
                    })}
                  </div>
                </div>
              ))}
              <div className="flex items-center pt-1">
                <div className="shrink-0" style={{ width: LABEL_W }} />
                <div className="flex-1 flex justify-between text-[10px] text-text-muted">
                  <span>{points[0]?.date.slice(5)}</span>
                  <span>{points[Math.floor(points.length / 2)]?.date.slice(5)}</span>
                  <span>{points[points.length - 1]?.date.slice(5)}</span>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value, suffix, hint, accent }: {
  label: string; value: number; suffix?: string; hint?: string; accent?: boolean
}) {
  return (
    <div className={cn('card p-3', accent && 'border-up/50 bg-up/5')}>
      <div className="text-[11px] text-text-muted">{label}</div>
      <div className={cn('text-xl font-bold font-mono', accent ? 'text-up' : 'text-text-primary')}>
        {value}<span className="text-xs font-normal text-text-muted ml-0.5">{suffix}</span>
      </div>
      {hint && <div className="text-[10px] text-text-muted/80 mt-0.5 leading-tight">{hint}</div>}
    </div>
  )
}

function HeightTip({ active, payload }: any) {
  if (!active || !payload?.length) return null
  const p: HeightPoint = payload[0].payload._p
  const lv = Object.keys(p.ladder).sort((a, b) => parseInt(b) - parseInt(a))
  return (
    <div className="bg-bg-elevated border border-bg-border rounded-lg px-3 py-2 shadow-xl text-xs">
      <div className="text-text-primary font-medium mb-1">
        {p.date}
        {p.is_breakout === true && <span className="ml-2 text-up">突破 {p.frontier} 板上沿</span>}
        {p.is_breakout === null && p.has_data && (
          <span className="ml-2 text-warn">
            上沿窗口覆盖 {p.frontier_covered} 天，不足以判定突破
          </span>
        )}
        {!p.has_data && <span className="ml-2 text-text-muted">当日无连板数据</span>}
      </div>
      <div className="text-text-secondary space-y-0.5">
        <div>最高 <b className="text-text-primary font-mono">{p.height}</b> 板
          {p.frontier != null && <span className="text-text-muted">（上沿 {p.frontier}）</span>}</div>
        <div>天花板附近 <b className="text-text-primary font-mono">{p.near_top_count}</b> 只 ·
          3板以上 <b className="text-text-primary font-mono">{p.multi_board_count}</b> 只</div>
        <div className="text-text-muted pt-0.5">
          梯队 {lv.map((k) => `${k}板×${p.ladder[k]}`).join('  ')}
        </div>
      </div>
    </div>
  )
}
