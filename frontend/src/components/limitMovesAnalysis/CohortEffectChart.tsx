import { useCallback, useMemo, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { cn } from '@/utils/cn'
import type { CohortSeriesResponse, CohortType } from '@/types'

/**
 * 逐日赚钱效应 · 按**昨日群体**。
 *
 * 每条线读作：「前一交易日收盘时是这一类的票，这一天中位涨了多少」。
 *
 * 它就画在「昨日群体 · 今日反馈」那张表的正上方，两者的关系是**同一批数字的
 * 两个视角**：表是今天这一列，曲线是这一列沿日期铺开。所以曲线读的必须是
 * cohorts_json 里那个 median_pct_change 本身，中间不能再加一层换算——一旦加了，
 * 最后一点就会跟它下面那张表对不上。
 *
 * 三条纪律，全部写死在这里：
 *
 * 1. **中位数不是均值**。表头就叫「今日中位收益」，后端存的也是中位数。这跟
 *    强势股概览那张生命周期图（均值）不是一回事，图例上要说出来。
 * 2. **缺失就断开，不填 0**。有效样本不足、或者当天这个群体一只票都没有，
 *    后端给的是 null。填成 0 的话「这群票不涨不跌」和「没有这群票」在图上
 *    长得完全一样。
 * 3. **未收盘的最后一点要标出来**。盘中那一点是用现价、而且只覆盖了部分成员
 *    算出来的（今天 28/48），不标就是让盘中浮动冒充当日结果。
 */
const LINES: { key: CohortType; color: string; dash?: string }[] = [
  { key: 'limit_up',     color: '#FF4560' },
  { key: 'first_board',  color: '#FB923C', dash: '4 2' },
  { key: 'multi_board',  color: '#E879F9' },
  { key: 'limit_down',   color: '#26C281' },
  { key: 'broken_board', color: '#F59E0B', dash: '3 3' },
  { key: 'strong_proxy', color: '#5EA6FF', dash: '1 3' },
]

/**
 * 默认亮着的三条 —— 这一页问的是涨跌停。首板/炸板/强势股池点图例就能加回来，
 * **只是默认收起，不是没有**。六条全开会糊成一团。
 */
const DEFAULT_ON = new Set<CohortType>(['limit_up', 'multi_board', 'limit_down'])

interface Row {
  date: string
  /**
   * 这一点是不是**盘中价**算出来的。
   *
   * **只有最后一点可能是。** 后端每天都给 is_settled，但历史日期只要有一行快照
   * 没标结算（该字段 2026-05-28 才加，之前全是 NULL），整天就会判成 false——
   * 那说明的是「这天的数据可能不全」，跟「这是盘中浮动价」根本不是一回事。
   * 把它渲染成"盘中"，整张图会挂满一片假警报。
   *
   * 历史日期的不完整已经由 tooltip 里的 n/m（有效样本/群体总数）说清楚了。
   */
  __live: 0 | 1
  [k: string]: string | number | null
}

export function CohortEffectChart({ data }: { data: CohortSeriesResponse }) {
  const present = useMemo(
    () => LINES.filter((l) => data.cohorts.some((c) => c.cohort_type === l.key)),
    [data.cohorts])
  const labelOf = useCallback(
    (k: CohortType) => data.cohorts.find((c) => c.cohort_type === k)?.label ?? k,
    [data.cohorts])

  const [hidden, setHidden] = useState<Set<CohortType>>(
    () => new Set(LINES.filter((l) => !DEFAULT_ON.has(l.key)).map((l) => l.key)))
  const toggle = useCallback((k: CohortType) => setHidden((p) => {
    const n = new Set(p); n.has(k) ? n.delete(k) : n.add(k); return n
  }), [])

  const rows = useMemo<Row[]>(() => data.points.map((p, i) => {
    const live = i === data.points.length - 1 && p.is_settled === false
    const r: Row = { date: p.trade_date.slice(5).replace('-', '/'), __live: live ? 1 : 0 }
    for (const l of present) {
      const v = p.values[l.key]
      // **拿不到就是 null**，Line 的 connectNulls={false} 会在这里断开
      r[labelOf(l.key)] = v?.median_pct_change ?? null
      r[`${labelOf(l.key)}__n`] = v ? v.valid_count : null
      r[`${labelOf(l.key)}__m`] = v ? v.member_count : null
    }
    return r
  }), [data.points, present, labelOf])

  /**
   * 前后都断开的孤立点。
   *
   * 断开（connectNulls={false}）+ 不画点（dot={false}）的组合有个坑：**一个
   * 两侧都是 null 的点，折线画出来是一段零长度的路径，屏幕上什么都没有**。
   * 本地实测「昨日连板」整条线是 6 段一点的碎片，看上去等于这条线不存在。
   * 跌停股为 0 的日子会成片制造这种孤立点，生产上一样会遇到。
   *
   * 不能靠填 0 绕过去——那正是这张图拒绝做的事。给孤立点补一个圆点即可。
   */
  const isolated = useMemo(() => {
    const out: Record<string, Set<number>> = {}
    for (const l of present) {
      const key = labelOf(l.key)
      const set = new Set<number>()
      rows.forEach((r, i) => {
        if (r[key] === null || r[key] === undefined) return
        const prev = i > 0 ? rows[i - 1][key] : null
        const next = i < rows.length - 1 ? rows[i + 1][key] : null
        if ((prev === null || prev === undefined) && (next === null || next === undefined)) set.add(i)
      })
      out[key] = set
    }
    return out
  }, [rows, present, labelOf])

  // 未收盘的那些点从哪天开始。**只标最后一段**——中间某天没结算是数据问题，
  // 不是"盘中"，那种情况交给 tooltip 逐点说
  const liveFrom = rows.length && rows[rows.length - 1].__live === 1
    ? rows[rows.length - 1].date : null

  if (!data.points.length) {
    return <div className="h-56 flex items-center justify-center text-text-muted text-sm">
      暂无逐日数据
    </div>
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-baseline justify-between flex-wrap gap-2">
        <span className="text-xs font-semibold text-text-primary">
          逐日赚钱效应 · 按昨日群体
          <span className="ml-1.5 font-normal text-text-muted">
            近 {data.points.length} 个交易日
          </span>
          {liveFrom && (
            <span className="ml-1.5 font-normal text-warn">
              · {liveFrom} 未收盘，末点是盘中价
            </span>
          )}
        </span>
        <span className="text-[10px] text-text-muted max-w-[62%] text-right">
          每条线 = 前一交易日收盘时冻结的那批票，这一天的
          <span className="text-text-secondary">中位涨跌幅</span>
          （跟下面那张表同一个字段，<span className="text-text-secondary">不是均值</span>）。
          有效样本不足或当天没有成员时曲线断开，不画成 0%。
        </span>
      </div>

      <div className="h-56">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#262D40" vertical={false} />
            <XAxis dataKey="date" tick={{ fill: '#737A96', fontSize: 10 }}
                   axisLine={false} tickLine={false}
                   interval="preserveStartEnd" minTickGap={28} />
            <YAxis tick={{ fill: '#737A96', fontSize: 10 }} axisLine={false}
                   tickLine={false} width={44}
                   tickFormatter={(v: number) => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`} />
            <ReferenceLine y={0} stroke="#3A4258" />
            {/* 未收盘的最后一点：竖线把它跟已收盘的部分隔开，tooltip 再说一次 */}
            {/* **不给它挂 label。** 这条线永远落在绘图区最右边，recharts 的
                insideTopLeft / insideTopRight 都会把文字顶出去（实测溢出 24px）。
                字放到卡片标题旁边，那里不会被裁 */}
            {liveFrom && (
              <ReferenceLine x={liveFrom} stroke="#F59E0B" strokeDasharray="3 3" />
            )}
            {/* offset 拉开、允许溢出：贴着光标画会正好压在刚悬停的那几条线上 */}
            <Tooltip content={<Tip lines={present.filter((l) => !hidden.has(l.key))
                                            .map((l) => ({ label: labelOf(l.key), color: l.color }))} />}
                     offset={24}
                     allowEscapeViewBox={{ x: false, y: true }}
                     wrapperStyle={{ zIndex: 30 }} />
            {present.map((l) => (
              <Line key={l.key} type="monotone" dataKey={labelOf(l.key)} stroke={l.color}
                    strokeWidth={l.key === 'limit_up' || l.key === 'limit_down' ? 2 : 1.5}
                    strokeDasharray={l.dash} activeDot={{ r: 3 }}
                    dot={<IsolatedDot at={isolated[labelOf(l.key)]} color={l.color} />}
                    connectNulls={false} hide={hidden.has(l.key)}
                    // **必须关掉入场动画。** recharts 只在动画结束后才画 dot
                    // （Line.renderDots 头一行就是 `if (isAnimationActive &&
                    // !isAnimationFinished) return null`），而这张图上
                    // isAnimationFinished 一直停在 false，孤立点永远画不出来。
                    // 60 个点的曲线也不需要入场动画
                    isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="flex flex-wrap justify-center gap-x-4 gap-y-1">
        {present.map((l) => {
          const off = hidden.has(l.key)
          return (
            <button key={l.key} onClick={() => toggle(l.key)}
                    className={cn('flex items-center gap-1.5 text-xs select-none transition-opacity',
                      off ? 'opacity-25 hover:opacity-50' : 'hover:opacity-75')}
                    title={off ? `显示 ${labelOf(l.key)}` : `隐藏 ${labelOf(l.key)}`}>
              <svg width="20" height="10" className="shrink-0">
                <line x1="0" y1="5" x2="20" y2="5" stroke={l.color} strokeWidth={2}
                      strokeDasharray={l.dash} />
              </svg>
              <span className="text-text-secondary">{labelOf(l.key)}</span>
            </button>
          )
        })}
      </div>

      {data.notes.map((n, i) => (
        <p key={i} className="text-[10px] text-text-muted">{n.replace(/\*\*/g, '')}</p>
      ))}
    </div>
  )
}

/**
 * 只给孤立点画点。recharts 会把 cx/cy/index 注入进来（`dot` 元素的 props 由它补齐），
 * 所以这几个字段是可选的。**不能返回 null**——recharts 要一个合法元素。
 */
function IsolatedDot({ at, color, cx, cy, index }: {
  at?: Set<number>; color: string; cx?: number; cy?: number; index?: number
}) {
  if (cx == null || cy == null || index == null || !at?.has(index)) return <g />
  return <circle cx={cx} cy={cy} r={2} fill={color} />
}

interface TipProps {
  active?: boolean
  label?: string
  payload?: { payload: Row }[]
  /** 当前**可见**的线。不从 payload 取——recharts 会把值为 null 的线整条剔掉 */
  lines: { label: string; color: string }[]
}

function Tip({ active, label, payload, lines }: TipProps) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  // **每条可见的线都要出现**，包括这天没有值的。
  //
  // recharts 传进来的 payload 只含有值的线，照着渲染的话，断开那天的 tooltip 会
  // 少几行——读的人分不清是"这条线今天没数据"还是"我没悬停到它"。而"为什么断"
  // 恰恰是这张图最该回答的问题：0/46 说明这天这批票一个次日结果都没有。
  //
  // 排序按中位收益从大到小（图例顺序是"哪一档"，悬停要答的是"这天谁最强"），
  // 空值沉底——「没有有效样本」不是「最低」。
  const rows = lines
    .map((l) => ({
      ...l,
      value: (row[l.label] ?? null) as number | null,
      n: row[`${l.label}__n`] as number | null,
      m: row[`${l.label}__m`] as number | null,
    }))
    .sort((a, b) => {
      if (a.value === null && b.value === null) return 0
      if (a.value === null) return 1
      if (b.value === null) return -1
      return b.value - a.value
    })
  return (
    // 背景必须实心：这块浮在折线上面，透出来就看不清了（bg-bg-surface 不存在，
    // 别再写它——tailwind 配置里没有 surface 这个色阶，写了等于全透明）
    <div className="bg-bg-elevated border border-bg-border rounded px-2.5 py-1.5
                    text-[11px] space-y-0.5 shadow-xl shadow-black/50">
      <div className="text-text-primary font-medium">
        {label}
        {row.__live === 1 && (
          <span className="ml-1.5 text-warn font-normal">未收盘 · 盘中价</span>
        )}
      </div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-1.5 whitespace-nowrap">
          <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: r.color }} />
          <span className={cn(r.value === null ? 'text-text-muted' : 'text-text-secondary')}>
            {r.label}
          </span>
          <span className="font-mono tabular-nums ml-auto"
                style={{ color: r.value === null ? '#737A96'
                               : r.value >= 0 ? '#FF4560' : '#26C281' }}>
            {r.value === null ? '—' : `${r.value > 0 ? '+' : ''}${r.value.toFixed(2)}%`}
          </span>
          {/* **样本一起给。** n/m = 有次日结果的只数 / 群体总数。
              m0 是"当天这个群体一只票都没有"，跟"有票但一个次日结果都没拿到"
              （0/46）不是一回事，断开的原因就藏在这里 */}
          <span className={cn('font-mono',
            r.m && r.n !== null && r.n < r.m ? 'text-warn/70' : 'text-text-muted/70')}>
            {!r.m ? '无成员' : `${r.n}/${r.m}`}
          </span>
        </div>
      ))}
    </div>
  )
}
