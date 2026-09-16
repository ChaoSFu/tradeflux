/**
 * MarketStateBar — 全局市场状态条（赚钱效应 / 涨跌停家数 / 板块）。
 * 复用到所有页面顶部。
 *
 * **2026-09-09 撤掉四项主观指标**：弱转强 Market Gate、市场阶段、情绪温度、
 * 建议仓位。前三个由自造的情绪分/板块生命周期加权而来，最后一个是在那之上又
 * 加一层映射——口径都经不起推敲，而摆在全局顶栏等于给它们最高的可信度。
 *
 * 这里只留能追到具体字段的市场事实：涨跌停家数、强势池当日涨跌幅、T-1 冻结群体
 * 的次日反馈、成交额分布、板块排名。它们各自的来源在每个 Cell 上都说得出来。
 *
 * **2026-09-15 换掉「涨停龙头赚钱 / 震荡龙头赚钱」**：那两组按 `Stock.phase` 分，
 * 而 phase 只是「收盘价在哪条均线下面」的单日快照。强势股概览下面的卡片 09-07
 * 就换成了生命周期分组，顶栏一直没跟上。现在是「昨日刚断板 / 昨日修复中」：
 * 昨天处于该状态的票，今天的平均涨幅——跟概览页「逐日赚钱效应」曲线同一个数。
 * 原来那个「×」（今日 / 30日均值）不带过来：拿涨跌幅做除数，均值贴着 0 时
 * 就是 -61.14× 这种数，看不出任何东西。
 */
import { useState, useMemo, useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchProfitEffect } from '@/api/marketState'
import { fetchTurnoverOverview } from '@/api/turnover'
import { fetchLimitMoves, fetchLimitMovesTrend, fetchStrongPool, fetchLifecycleEffect } from '@/api/stocks'
import { LIFECYCLE_ZH } from '@/lib/lifecycle'
import { fetchMarketEffectLatest } from '@/api/marketEffects'
import { useSectorTags } from '@/hooks/useSectorTags'
import { SectorTag } from '@/components/common/SectorTags'
import { SectorSection, buildSectorGroups } from '@/components/common/SectorSection'
import type { Stock } from '@/types'
import { cn } from '@/utils/cn'

const pctColor = (v: number) => (v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-secondary')
const pctSign = (v: number) => (v >= 0 ? `+${v.toFixed(2)}%` : `${v.toFixed(2)}%`)

function ClickSector({ name, pct, active, onClick }: { name: string; pct?: number | null; active: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className={cn('inline-flex items-center gap-0.5 rounded transition-shadow', active && 'ring-1 ring-accent')} title="查看该板块强势股">
      <SectorTag name={name} />
      {pct != null && (
        <span className={cn('text-[10px] font-mono font-medium', pctColor(pct))}>{pctSign(pct)}</span>
      )}
    </button>
  )
}

/**
 * 跌停水平的四档 —— **全部由「今日只数 / 跌停30日均值」这一个比值定**，没有别的
 * 输入，阈值写在那一格的悬停说明里。这不是评分，是把一个比值切成四段。
 *
 * 颜色沿用本仓库的约定：跌停一律走 down 色（绿），轻重靠边框浓淡表达，不靠换色。
 * 「退潮」那档例外走 accent（蓝）——它要的是「看一眼」，不是「警告」。
 */
const DOWN_LEVEL = {
  ebb:     { text: '退潮',     box: 'border-accent/60 bg-accent/10' },
  normal:  { text: '均值附近', box: 'border-bg-border bg-bg-elevated/40' },
  high:    { text: '高于均值', box: 'border-down/50 bg-down/10' },
  extreme: { text: '极端',     box: 'border-down bg-down/20' },
} as const
type DownLevel = keyof typeof DOWN_LEVEL

/**
 * 近 N 个交易日的跌停只数缩略图。
 *
 * **柱状不是折线**：只数是离散计数，柱子高度一眼能比大小；折线会在 0 附近画出
 * 根本不存在的斜坡。最后一根是今天，单独着色。
 * 那天真是 0 只就画 1px 的底座——**0 是事实，不是没数据**，逐根悬停给具体只数。
 */
function MiniBars({ data }: { data: { date: string; n: number }[] }) {
  const max = Math.max(...data.map((d) => d.n), 1)
  return (
    <div className="flex items-end gap-[2px] h-4">
      {data.map((d, i) => (
        <div key={d.date} title={`${d.date.slice(5)} ${d.n} 只`}
             className={cn('w-[3px] rounded-sm', i === data.length - 1 ? 'bg-down' : 'bg-down/35')}
             style={{ height: d.n === 0 ? 1 : Math.max(3, (d.n / max) * 16) }} />
      ))}
    </div>
  )
}

function Cell({ label, title, children }: { label: string; title?: string; children: React.ReactNode }) {
  return (
    <div className="shrink-0" title={title}>
      <p className="text-[10px] text-text-muted leading-none mb-1">{label}</p>
      <div className="flex items-center gap-1.5">{children}</div>
    </div>
  )
}

export function MarketStateBar() {
  const { data: pe } = useQuery({ queryKey: ['profit-effect'], queryFn: fetchProfitEffect })
  // 市场效应页同源缓存 key（MarketEffects.tsx 里也是 ['market-effect-latest']）
  const { data: effect } = useQuery({ queryKey: ['market-effect-latest'], queryFn: fetchMarketEffectLatest })
  // 大成交额赚钱效应（成交额概览页同源缓存 key，两处共享同一次请求）
  const { data: turnover } = useQuery({ queryKey: ['turnover-overview'], queryFn: () => fetchTurnoverOverview() })
  const turnoverUpCount = turnover?.stocks.filter((s) => s.pct_change > 0).length ?? 0
  const turnoverDownCount = turnover?.stocks.filter((s) => s.pct_change < 0).length ?? 0
  // 点击板块 → 展开该板块强势股列表（与板块赚钱效应点击一致）
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [expandedSector, setExpandedSector] = useState<string | null>(null)
  // MarketStateBar 挂载在 Layout 里、跨路由常驻不卸载，切换页面时默认收起，
  // 避免在别的页面下面拖着一个上个页面展开的板块股票列表
  useEffect(() => setExpandedSector(null), [pathname])

  // ── 涨跌停家数：**只要一个数字，就别把 500 行拉回来数长度** ───────────────
  // 这个组件挂在 Layout 上跨路由常驻，下面每一路查询都是"进任何页面都要付一遍"。
  // 原来用 page_size=500 再取 items.length，实测 88KB / 0.40s，而接口本身就返回
  // total —— page_size=1 之后 1KB / 0.12s。
  //
  // 顺带修掉一个正确性问题：**涨停超过 500 只时 items.length 会停在 500**，
  // 而 total 才是真数。今天 93 只没暴露，那是运气不是设计。
  const { data: upCount } = useQuery({
    queryKey: ['limit-moves-count', 'limit_up'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 1, move_type: 'limit_up' }),
  })
  const { data: downCount } = useQuery({
    queryKey: ['limit-moves-count', 'limit_down'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 1, move_type: 'limit_down' }),
  })
  const limitUpCount = upCount?.total ?? null
  const limitDownCount = downCount?.total ?? null

  // ── 板块展开用的三份名单：**点开才取** ───────────────────────────────────
  // 它们只喂 sectorGroupMap，而那个只在 expandedSector 非空时才用得到（见文件
  // 末尾的展开区）。默认是收起的，所以原来每进一个页面都白拉 146KB。
  //
  // queryKey 跟「涨跌停分析」页对齐，两边共享同一次请求——同一份涨停名单原来
  // 有三个不同的 key，各取各的。
  const sectorListEnabled = expandedSector !== null
  const { data: up } = useQuery({
    queryKey: ['limit-moves', 'limit_up'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_up' }),
    enabled: sectorListEnabled,
  })
  const { data: down } = useQuery({
    queryKey: ['limit-moves', 'limit_down'],
    queryFn: () => fetchLimitMoves({ page: 1, page_size: 500, move_type: 'limit_down' }),
    enabled: sectorListEnabled,
  })
  const { data: strongPool } = useQuery({
    queryKey: ['strong-pool-sector-analysis'],
    queryFn: () => fetchStrongPool({ page: 1, page_size: 500 }),
    // 这个 key 另有 4 个 hook 和情绪板块页在用；那些页面照常取，
    // 顶栏只是不再无条件替它们发起
    enabled: sectorListEnabled,
  } as any)
  const sectorGroupMap = useMemo(() => {
    const seen = new Set<number>(); const merged: Stock[] = []
    for (const s of [
      ...((strongPool as any)?.items ?? []),
      ...((up as any)?.items ?? []),
      ...((down as any)?.items ?? []),
    ] as Stock[]) { if (!seen.has(s.id)) { seen.add(s.id); merged.push(s) } }
    return new Map(buildSectorGroups(merged).map((g) => [g.name, g]))
  }, [strongPool, up, down])
  const toggleSector = (name: string) => setExpandedSector((p) => (p === name ? null : name))

  // 30日均值（与走势图同源）→ 比值 = 当日 / 30日均值
  const { data: trend } = useQuery({
    queryKey: ['limit-moves-trend', 30],
    queryFn: () => fetchLimitMovesTrend(30),
  } as any)
  const last30: any[] = ((trend as any) ?? []).slice(-30)
  const avgUp30 = last30.length ? last30.reduce((s, p) => s + p.limit_up_count, 0) / last30.length : null
  const avgDown30 = last30.length ? last30.reduce((s, p) => s + p.limit_down_count, 0) / last30.length : null
  const upRatio = limitUpCount != null && avgUp30 ? limitUpCount / avgUp30 : null
  const downRatio = limitDownCount != null && avgDown30 ? limitDownCount / avgDown30 : null

  // ── 跌停 · 极端做空：**整条里最该先看的一格，所以它排在最前、单独成框** ──
  // 一个数字说不出它在什么水平、往哪个方向走，而方向恰恰是能用的那部分：
  // 贴近 0 或急剧变少 = 做空力量退潮；开始变多 = 风险抬头。所以这格给三样，
  // 每样都追得到字段：今日只数 + 相对30日均值的倍数、相对上一交易日的增减、
  // 近 12 个交易日的柱状缩略图。
  //
  // **增减和大数字必须是同一天的事实**：大数字来自 limit-moves 的 total，
  // 缩略图和昨日只数来自 trend 序列，两个查询两个观测时点。日期对不上就不显示
  // 增减——拿昨天的数冒充今天，正好会在方向上骗人（2026-09-16 实测两边都是 4）
  const downSeries: { date: string; n: number }[] = last30
    .slice(-12)
    .map((p) => ({ date: p.date, n: p.limit_down_count }))
  const downPrev = downSeries.length >= 2 ? downSeries[downSeries.length - 2] : null
  const downSameDay = downSeries.length > 0
    && downSeries[downSeries.length - 1].date === (downCount as any)?.trade_date
  const downDelta = downPrev && downSameDay && limitDownCount != null
    ? limitDownCount - downPrev.n : null
  const downLevel: DownLevel | null =
    downRatio == null ? null
      : downRatio >= 2 ? 'extreme'
      : downRatio >= 1 ? 'high'
      : downRatio <= 0.5 ? 'ebb'
      : 'normal'
  const downTip = limitDownCount == null ? '' : [
    `今日跌停 ${limitDownCount} 只`
      + (avgDown30 && downRatio != null
        ? ` · 30日均值 ${avgDown30.toFixed(1)} → ${downRatio.toFixed(2)}×` : ''),
    downPrev && downDelta != null
      ? `上一交易日（${downPrev.date}）${downPrev.n} 只，${
          downDelta > 0 ? `多了 ${downDelta} 只` : downDelta < 0 ? `少了 ${-downDelta} 只` : '持平'}`
      : '上一交易日的只数和今天不是同一天的观测，不显示增减',
    '柱状 = 近 12 个交易日的跌停只数，最后一根是今天（未收盘时是盘中数）',
    '读法：贴近 0 或急剧变少 = 做空力量退潮；开始变多 = 风险抬头',
    '四档阈值（今日/30日均值）：≤0.5× 退潮 · 1× 以下均值附近 · ≥1× 高于均值 · ≥2× 极端',
  ].join('\n')

  // 行情强弱标注：涨停数>30日均值=强势(>2倍=极端强势)；跌停数>30日均值=弱势(>2倍=极端弱势)
  const strongLv = upRatio == null ? 0 : upRatio > 2 ? 2 : upRatio > 1 ? 1 : 0
  const weakLv = downRatio == null ? 0 : downRatio > 2 ? 2 : downRatio > 1 ? 1 : 0
  // 卡片左边框随最严重信号联动（极端弱势优先提示风险）
  const regimeBorder =
    weakLv === 2 ? '#26C281' : strongLv === 2 ? '#FF4560'
    : weakLv === 1 ? '#26C28199' : strongLv === 1 ? '#FF456099' : '#4F9CF9'

  // 进攻板块：5日 / 10日涨幅排名前5（5日龙1~5 / 10日龙1~5）
  const { byName: sectorTags } = useSectorTags()
  const rankTop5 = (key: 'rank_5d' | 'rank_10d' | 'rank_20d') => {
    const arr: { name: string; rank: number }[] = []
    sectorTags.forEach((t, name) => {
      const r = t[key]
      if (r != null && r >= 1 && r <= 5) arr.push({ name, rank: r })
    })
    return arr.sort((a, b) => a.rank - b.rank)
  }
  const attack5 = rankTop5('rank_5d')
  const attack10 = rankTop5('rank_10d')
  const attack20 = rankTop5('rank_20d')
  // 归并：跨 5/10/20 日强出现次数（越多越有持续力），只取出现 ≥2 次的
  const sustained = (() => {
    const cnt = new Map<string, number>()
    for (const a of [attack5, attack10, attack20]) for (const s of a) cnt.set(s.name, (cnt.get(s.name) ?? 0) + 1)
    return [...cnt.entries()].filter(([, c]) => c >= 2).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
  })()
  // 今日最强前 5：当日涨幅最高的 5 个板块
  const todayTop5 = [...sectorTags.entries()]
    .filter(([, t]) => t.pct_today != null)
    .sort((a, b) => (b[1].pct_today ?? -Infinity) - (a[1].pct_today ?? -Infinity))
    .slice(0, 5)
    .map(([name]) => name)

  // 今日涨停最多前 5：当日涨停股所属板块出现次数最高的 5 个（与「涨跌停概览」板块统计同口径）
  const todayLimitUpTop5: [string, number][] = useMemo(() => {
    const cnt = new Map<string, number>()
    for (const s of ((up as any)?.items ?? []) as Stock[]) {
      for (const sec of s.sectors ?? []) cnt.set(sec, (cnt.get(sec) ?? 0) + 1)
    }
    return [...cnt.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 5)
  }, [up])

  // 成交额板块效应前 5：成交额概览页「成交额板块效应」默认排序（赚钱效应 avg_pct_change）前5
  // 保留完整 group（而非只取 name），行内括号展示赚钱效应/大成交额只数，跟板块自身涨幅区分
  const turnoverEffectTop5 = useMemo(
    () => [...(turnover?.sector_groups ?? [])].sort((a, b) => b.avg_pct_change - a.avg_pct_change).slice(0, 5),
    [turnover],
  )

  // 昨日刚断板 / 昨日修复中：跟强势股概览共用 ['lifecycle-effect'] 这一次请求。
  // 收盘后取 series 里 as_of 那一点；未收盘取 today_estimate（盘中估算，标出来）。
  // **按 as_of 找，不取 series 的最后一点**——那可能是前一天的，会冒充今天
  const { data: lce } = useQuery({
    queryKey: ['lifecycle-effect'], queryFn: fetchLifecycleEffect,
    staleTime: 10 * 60 * 1000,
  })
  const lcPoint = lce?.today_estimate
    ?? lce?.series.find((p) => p.trade_date === lce.as_of) ?? null
  const lcIsEstimate = !!lce?.today_estimate
  const lcBasedOn = lce?.today_estimate?.based_on ?? lce?.prev ?? null

  return (
    <>
    <div className="card px-4 py-2.5 border-l-4 mb-4" style={{ borderLeftColor: regimeBorder }}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        {/* 行情强弱标注（显眼位）：涨/跌停数 vs 30日均值 */}
        {(strongLv > 0 || weakLv > 0) && (
          <div className="flex items-center gap-1.5">
            {strongLv > 0 && (
              <span
                title={`今日涨停 ${limitUpCount} 只，为30日均值(${avgUp30!.toFixed(1)})的 ${upRatio!.toFixed(2)} 倍`}
                className={cn('text-xs font-bold px-2 py-1 rounded-md border whitespace-nowrap',
                  strongLv === 2
                    ? 'bg-up text-white border-up animate-pulse-slow shadow-[0_0_18px_-2px_#FF4560]'
                    : 'bg-up/15 text-up border-up/40')}
              >
                {strongLv === 2 ? '⚡ 极端强势' : '强势行情'}
              </span>
            )}
            {weakLv > 0 && (
              <span
                title={`今日跌停 ${limitDownCount} 只，为30日均值(${avgDown30!.toFixed(1)})的 ${downRatio!.toFixed(2)} 倍，注意风险`}
                className={cn('text-xs font-bold px-2 py-1 rounded-md border whitespace-nowrap',
                  weakLv === 2
                    ? 'bg-down text-white border-down animate-pulse-slow shadow-[0_0_18px_-2px_#26C281]'
                    : 'bg-down/15 text-down border-down/40')}
              >
                {weakLv === 2 ? '⚠ 极端弱势' : '弱势行情'}
              </span>
            )}
          </div>
        )}
        {/* 跌停 · 极端做空（2026-09-16 提到队首并单独成框）：这一格是做多做空
            环境最直接的那个事实，原来跟其他格一样是一行小字，看不出方向 */}
        {limitDownCount != null && (
          <div className={cn('shrink-0 rounded-md border px-2.5 py-1',
                             DOWN_LEVEL[downLevel ?? 'normal'].box)}
               title={downTip}>
            <p className="text-[10px] text-text-secondary leading-none mb-1">跌停 · 极端做空</p>
            <div className="flex items-center gap-2">
              <span className="font-mono text-xl font-bold text-down leading-none">
                {limitDownCount}
              </span>
              <div className="flex flex-col gap-1">
                <div className="flex items-center gap-1.5 leading-none">
                  {downRatio != null && (
                    <span className="text-[11px] font-mono font-bold text-down/90">
                      {downRatio.toFixed(2)}×
                    </span>
                  )}
                  {downLevel && (
                    <span className={cn('text-[10px]',
                      downLevel === 'ebb' ? 'text-accent'
                        : downLevel === 'normal' ? 'text-text-muted' : 'text-down')}>
                      {DOWN_LEVEL[downLevel].text}
                    </span>
                  )}
                  {/* 跌停变多是风险抬头，走 down 色；变少只是退潮，不抢注意力 */}
                  {downDelta != null && (
                    <span className={cn('text-[10px] font-mono',
                      downDelta > 0 ? 'text-down font-bold' : 'text-text-muted')}>
                      {downDelta > 0 ? `↑${downDelta}` : downDelta < 0 ? `↓${-downDelta}` : '持平'}
                    </span>
                  )}
                </div>
                {downSeries.length >= 2 && <MiniBars data={downSeries} />}
              </div>
            </div>
          </div>
        )}

        {/* 此前叫"赚钱效应"，但统计口径其实是当前 in_strong_pool 股票的当天涨跌幅，
            跟下面"短线赚亏效应"（market_effect_service 的T-1冻结群体反馈）是两套
            完全不同方法论的独立指标，共用一个名字会互相误导——改名成"强势股池
            表现"匹配它真实的数据来源，数值/逻辑不变（2026-08-24按用户要求修复）。 */}
        <Cell label="强势股池表现">
          {pe?.has_data ? (
            <>
              <span className={cn('font-mono text-base font-bold', pctColor(pe.overall_avg_pct))}>
                {pctSign(pe.overall_avg_pct)}
              </span>
              <span className="text-xs text-text-muted">
                <span className="text-up">↑{pe.overall_up_count}</span>
                {' / '}
                <span className="text-down">↓{pe.overall_down_count}</span>
              </span>
            </>
          ) : <span className="text-text-muted text-xs">—</span>}
        </Cell>

        {/* 短线赚亏效应（2026-08-24新增）：market_effect_service 的真实语义——T-1是
            成员资格的冻结时点，不是这个赚亏效应本身的数据日期；service先在T-1冻结
            涨停/连板/炸板/跌停/强势股这批群体的名单，再用trade_date当天的表现评价
            这批固定名单，避免"今天涨的都在池子里、今天跌的都被剔除"这种当天重新
            选样导致的幸存者偏差。不能只显示两个好看的分数——breadth_source降级成
            tracked_pool时必须显眼标出来，不能悄悄用近似值冒充全市场结果。 */}
        <Cell label="短线赚亏效应">
          {effect ? (
            <>
              <span className="font-mono text-base font-bold">
                <span className="text-up">赚{effect.profit_strength.toFixed(0)}</span>
                {' / '}
                <span className="text-down">亏{effect.loss_strength.toFixed(0)}</span>
              </span>
              <span className={cn(
                'text-[10px] font-mono',
                effect.breadth_source === 'tracked_pool' ? 'text-warn' : 'text-text-muted',
              )}>
                {effect.trade_date} · {effect.breadth_source === 'tracked_pool' ? 'LOW·跟踪池近似' : 'NORMAL'}
              </span>
            </>
          ) : <span className="text-text-muted text-xs">—</span>}
        </Cell>

        {/* 昨天处于该状态的票，今天的平均涨幅。**均值**——跟概览页那条逐日曲线
            同一个数；那里卡片上的是去极值均值，不足 3 只算不出，而这两组平时
            每天就一两只，照搬那个口径这两格多数日子是空的。只数一起摆出来：
            1 只的时候它就是一只票的涨幅 */}
        {(['BROKEN', 'REPAIRING'] as const).map((st) => {
          const zh = LIFECYCLE_ZH[st]
          // 当天没有这个 key = 昨天没有处于该状态的票，**不是涨了 0%**
          const v = lcPoint?.values[st]
          const tip = !lcPoint ? '暂无数据'
            : !v ? `昨天（${lcBasedOn}）没有处于「${zh}」的票`
            : `昨天（${lcBasedOn}）处于「${zh}」的 ${v.n} 只票，`
              + `${lcIsEstimate ? '今天按现价的盘中估算' : `${lcPoint.trade_date} 收盘`}平均 ${pctSign(v.avg)}。\n`
              + '均值，跟强势股概览「逐日赚钱效应」曲线同一个数；'
              + '下面卡片上的是去极值均值（不足 3 只算不出）。'
          return (
            <Cell key={st} label={`昨日${zh}`} title={tip}>
              {v ? (
                <>
                  <span className={cn('font-mono text-base font-bold', pctColor(v.avg))}>{pctSign(v.avg)}</span>
                  <span className="text-xs text-text-muted">{v.n}只</span>
                  {lcIsEstimate && <span className="text-[10px] text-warn">盘中估算</span>}
                </>
              ) : <span className="text-text-muted text-xs">—</span>}
            </Cell>
          )
        })}

        {limitUpCount != null && (
          <Cell label="涨停 · 极端做多">
            <span className="font-mono text-base font-bold text-up">{limitUpCount}</span>
            {upRatio != null && (
              <span
                title={`当日涨停 ${limitUpCount} / 涨停30日均值 ${avgUp30!.toFixed(1)} = ${upRatio.toFixed(2)}（>1 做多意愿强）`}
                className={cn('text-xs font-mono font-medium', upRatio >= 1 ? 'text-up' : 'text-text-muted')}
              >
                {upRatio.toFixed(2)}×
              </span>
            )}
          </Cell>
        )}
        {turnover?.date && (
          <Cell label="大成交额赚钱效应">
            <span className={cn('font-mono text-base font-bold', pctColor(turnover.overall_avg_pct))}>
              {pctSign(turnover.overall_avg_pct)}
            </span>
            <span className="text-xs text-text-muted">
              <span className="text-up">↑{turnoverUpCount}</span>
              {' / '}
              <span className="text-down">↓{turnoverDownCount}</span>
            </span>
          </Cell>
        )}

        <div className="ml-auto text-[10px] text-text-muted/70 self-center">⚠️ 仅供辅助分析，不构成投资建议</div>
      </div>

      {/* 进攻板块：5日 / 10日 / 20日强 + 持续板块（5/10/20 日强归并出现≥2次） */}
      {(attack5.length > 0 || attack10.length > 0 || attack20.length > 0 || sustained.length > 0) && (
        <div className="mt-2 pt-2 border-t border-bg-border/40 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs">
          <span className="text-[10px] text-text-muted shrink-0">进攻板块</span>
          {attack5.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[10px] text-up shrink-0">5日强</span>
              {attack5.map((s) => <ClickSector key={`5-${s.name}`} name={s.name} pct={sectorTags.get(s.name)?.pct_today} active={expandedSector === s.name} onClick={() => toggleSector(s.name)} />)}
            </div>
          )}
          {attack10.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[10px] text-accent shrink-0">10日强</span>
              {attack10.map((s) => <ClickSector key={`10-${s.name}`} name={s.name} pct={sectorTags.get(s.name)?.pct_today} active={expandedSector === s.name} onClick={() => toggleSector(s.name)} />)}
            </div>
          )}
          {attack20.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[10px] text-warn shrink-0">20日强</span>
              {attack20.map((s) => <ClickSector key={`20-${s.name}`} name={s.name} pct={sectorTags.get(s.name)?.pct_today} active={expandedSector === s.name} onClick={() => toggleSector(s.name)} />)}
            </div>
          )}
          {sustained.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap">
              <span className="text-[10px] text-text-muted shrink-0" title="在 5/10/20 日强势板块中出现 ≥2 次，体现持续力">持续板块</span>
              {sustained.map(([name, c]) => (
                <span key={`sus-${name}`} className="inline-flex items-center gap-1">
                  <ClickSector name={name} pct={sectorTags.get(name)?.pct_today} active={expandedSector === name} onClick={() => toggleSector(name)} />
                  <span className={cn('text-[10px] font-mono font-bold', c >= 3 ? 'text-up' : 'text-text-secondary')}>×{c}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* 今日最强前5（当日涨幅最高）——单独一行 */}
      {todayTop5.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-[10px] text-text-muted shrink-0" title="当日涨幅最高的前 5 个板块">今日最强</span>
          {todayTop5.map((name) => (
            <ClickSector key={`today-${name}`} name={name} pct={sectorTags.get(name)?.pct_today} active={expandedSector === name} onClick={() => toggleSector(name)} />
          ))}
        </div>
      )}

      {/* 今日涨停最多——单独一行 */}
      {todayLimitUpTop5.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-[10px] text-up shrink-0" title="当日涨停股所属板块出现次数最高的 5 个板块">今日涨停最多</span>
          {todayLimitUpTop5.map(([name, c]) => (
            <span key={`lu-${name}`} className="inline-flex items-center gap-1">
              <ClickSector name={name} pct={sectorTags.get(name)?.pct_today} active={expandedSector === name} onClick={() => toggleSector(name)} />
              <span className="text-[10px] font-mono font-bold text-up">×{c}</span>
            </span>
          ))}
        </div>
      )}

      {/* 成交额板块效应前5——单独一行。板块 tag 上的 % 是板块自身今日涨幅（跟其他行同一
          口径）；括号内是成交额概览页的赚钱效应（该板块高成交额股均涨幅）+ 大成交额只数，
          用括号跟前面的板块涨幅区分开，体现"高成交额资金在这个板块的强度"，而非板块本身涨跌 */}
      {turnoverEffectTop5.length > 0 && (
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-[10px] text-text-muted shrink-0" title="成交额概览页「成交额板块效应」排名前 5 的板块；括号内为该板块的赚钱效应（高成交额股均涨幅）与大成交额只数">成交额板块效应</span>
          {turnoverEffectTop5.map((g) => (
            <span key={`to-${g.name}`} className="inline-flex items-center gap-0.5">
              <ClickSector name={g.name} pct={sectorTags.get(g.name)?.pct_today} active={expandedSector === g.name} onClick={() => toggleSector(g.name)} />
              <span className="text-[10px] font-mono text-text-muted/70">
                (<span className={pctColor(g.avg_pct_change)}>{pctSign(g.avg_pct_change)}</span>
                <span className="text-text-muted/50">·{g.count}只</span>)
              </span>
            </span>
          ))}
        </div>
      )}
      </div>

      {/* 点击板块展开：该板块强势股列表（沿用 SectorSection） */}
      {expandedSector && sectorGroupMap.get(expandedSector) && (
        <SectorSection
          group={sectorGroupMap.get(expandedSector)!}
          collapsed={false}
          onToggle={() => setExpandedSector(null)}
          onClickStock={(code) => navigate(`/stocks/${code}`)}
        />
      )}
    </>
  )
}
