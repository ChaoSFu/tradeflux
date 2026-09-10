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
 * 2. **缺口在图上按 0 接起来，但只在展示层**。有效样本不足、或者当天这个群体
 *    一只票都没有，后端给的是 null；接口载荷里它一直是 null，别去动。
 *
 *    图上填 0 是产品决定（跟强势股概览那张生命周期图一致）：那一组当天没有
 *    贡献赚钱效应。断开的版本试过——「昨日连板」整条线成了 6 段一点的碎片，
 *    等于这条线不存在。
 *
 *    代价是 0 在图上跟「真的中位涨 0%」长得一样，所以**这笔账由 tooltip 还**：
 *    悬停时缺失的那行显示「—」外加样本数（`0/46` = 这批票一个次日结果都没有，
 *    `无成员` = 当天压根没有这批票），永远不会把 0 说成 0.00%。
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
      const key = labelOf(l.key)
      // 画线用的值：缺失填 0，曲线才不会碎成一段一段
      r[key] = v?.median_pct_change ?? 0
      // **真值另存一份给 tooltip。** 填了 0 之后，光看 r[key] 已经分不出
      // 「这天中位就是 0.00%」和「这天没算出来」，而这正是悬停要回答的
      r[`${key}__raw`] = v?.median_pct_change ?? null
      r[`${key}__n`] = v ? v.valid_count : null
      r[`${key}__m`] = v ? v.member_count : null
    }
    return r
  }), [data.points, present, labelOf])

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
          有效样本不足或当天没有成员时，曲线按 0 接过去，
          <span className="text-text-secondary">悬停显示「—」和样本数</span>。
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
                    strokeDasharray={l.dash} activeDot={{ r: 3 }} dot={false}
                    hide={hidden.has(l.key)}
                    // 60 个点的曲线不需要入场动画，关掉渲染也更确定
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

interface TipProps {
  active?: boolean
  label?: string
  payload?: { payload: Row }[]
  /**
   * 当前**可见**的线。不从 payload 取——payload 里的值已经是填过 0 的展示值，
   * 而且 recharts 的排序/剔除规则跟这里要的不是一回事
   */
  lines: { label: string; color: string }[]
}

function Tip({ active, label, payload, lines }: TipProps) {
  if (!active || !payload?.length) return null
  const row = payload[0].payload
  // **读 __raw，不读画线用的那个值。** 曲线上缺失的点被填成了 0，照着渲染的话
  // tooltip 会写「0.00%」——那就成了拿"没数据"冒充"不涨不跌"。图上填 0 是为了
  // 线不碎，这笔账在这里还：缺失显示「—」，再把样本数摆出来说明为什么缺
  // （0/46 = 这批票一个次日结果都没有，无成员 = 当天压根没有这批票）。
  //
  // 排序按中位收益从大到小（图例顺序是"哪一档"，悬停要答的是"这天谁最强"），
  // 空值沉底——「没有有效样本」不是「最低」。
  const rows = lines
    .map((l) => ({
      ...l,
      value: row[`${l.label}__raw`] as number | null,
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
