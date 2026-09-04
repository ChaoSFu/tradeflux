/**
 * 高标龙头生命周期 —— 事实层 + Price Lifecycle v1 状态。
 *
 * 2026-09-04 之前这里刻意不给状态标签，因为没有历史能验证阈值。现在上了 price_v1：
 * 它**只用价格结构**（close 与 MA5/10/20/30、断板后新高新低），不碰 RS / 量能 /
 * 换手 / 板块 / 大盘——那些字段的覆盖率还没收口，做成硬门槛就会变成
 * 「某字段有没有数据」决定「某股票处于什么状态」。
 *
 * 状态是后端从历史事实 **replay** 出来的，不落库：阈值以后一定会改，冻进历史表
 * 就再也回答不了「新口径下当时该是什么状态」。
 *
 * **CROSS_SUCCESS ≠ 可以买。** 它只说价格结构已完成二波突破且短趋势仍健康。
 * CROSS_WEAKENING 存在的全部意义就是：别让「曾经穿越成功」这个历史荣誉标签
 * 长期挂在核心买入池里。
 *
 * 事实列一列没删——状态是派生的，事实才是可核对的那层。
 *
 * 每个可信度标记都对应一件"我们不确定"的事，不是装饰：
 *   ⚠ 连板数   周期区间内有交易日没快照 → 可能偏低（计数循环分不清"那天没涨停"
 *              和"那天我们没记录"）
 *   RS 空白    锚点日没有收盘价 / 无对应基准 → 不用邻近日期近似顶替
 *   换手空白   没有流通股本观测，或观测超 45 天（除权、解禁会让它台阶式跳变）
 *   ΔRS 空白   没有昨天那行快照 → 算不出变化。**不写 0**：0 的意思是"没变化"
 *   量比空白   前 5 根里有 bar 缺量 → 不拿 4 根冒充 5 日均量
 *
 * 覆盖率的分母是**整个强势池**，不是"已识别出周期的那些"。用后者当分母是幸存者
 * 偏差：解析不出周期的股票直接从分母里消失，覆盖率看起来比实际好。所以底部专门
 * 列出「识别不出周期」的那几只——这轮排查里 14 只口径不符查出 11 只是我们自己
 * 算错的，静默消失就永远发现不了。
 */
import { Fragment, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Info, TrendingUp } from 'lucide-react'
import { fetchLeaderCycle, type LeaderCycleItem, type LifecycleState } from '@/api/stocks'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'

type Group = 'core' | 'waiting' | 'dropped' | 'all' | 'unbucketed'
/**
 * NO_CYCLE 是**前端专有**的展示状态：后端把这些股票放在 unresolved 里，它们
 * 根本没有快照，也就没有 lifecycle_state。单独给个标签而不是混进 UNKNOWN——
 * 「识别不出 ≥4 连板周期」和「今天的价格事实不足以判断」是两回事。
 */
type Bucketable = LifecycleState
const NUM = 'font-mono tabular-nums'

/**
 * 分组只有三个语义桶 + 一个「全部」。系统的职责是**剔除**，把注意力收敛到少数
 * 几只上；不是替人做买点判断——买点由人确认，这里一个 BUY/BUYABLE 都不给。
 *
 * **每一桶的并集必须等于整个强势池。** 一只股票因为归不了类而从界面上消失，
 * 是这个页面最不能容忍的失败：不可见比判断错更糟，判断错还能被看见并纠正。
 * 所以下面三个桶之外还有一个 unbucketed 兜底——它平时是 0，一旦非 0 就会亮出来。
 */
const BUCKET: Record<Group, Bucketable[]> = {
  core:    ['REPAIRING', 'CROSS_SUCCESS'],
  // UNKNOWN / NO_CYCLE 也归这里。它们是**合法状态**，不是"没预料到的状态"——
  // 放进 unbucketed 会让那个红色兜底 tab 长期亮着，警报天天响就等于没有警报。
  // 归到"待观察"也符合语义：既没被剔除，今天也不可行动，等事实补齐
  waiting: ['STREAKING', 'BROKEN', 'UNKNOWN', 'NO_CYCLE'],
  dropped: ['CROSS_WEAKENING', 'CROSS_FAILED', 'FADED'],
  all:     [],          // 不过滤
  unbucketed: [],       // 动态：不属于上面任何一桶的
}
const BUCKETED = new Set<string>([...BUCKET.core, ...BUCKET.waiting, ...BUCKET.dropped])

const COLS = ['股票', '状态', '主板块', '本轮', '60日', 'D+', '峰值回撤',
  '现价/MA5', '现价/MA10', '距阶段高', '距周期顶',
  'RS市场20', 'ΔRS 1日', 'ΔRS 3日', 'RS板块20', '量比5日', '换手']

/**
 * 表格内的排序 / 分段顺序：**按生命周期推进的方向**排。
 *
 * 一个 tab 里状态混着排，扫一眼看不出各有几只——「已剔除」里走弱、失败、结束
 * 交替出现时尤其明显。分段之后，每一段的规模一眼可见，而规模本身就是信息：
 * 46 只里有多少是刚走弱（还值得回头看）、多少已经周期结束（可以不看了）。
 */
const STATE_ORDER: Bucketable[] = [
  'STREAKING', 'BROKEN', 'REPAIRING', 'CROSS_SUCCESS',
  'CROSS_WEAKENING', 'CROSS_FAILED', 'FADED', 'UNKNOWN', 'NO_CYCLE',
]
const orderOf = (st: string | null) => {
  const i = STATE_ORDER.indexOf((st ?? 'UNKNOWN') as Bucketable)
  return i < 0 ? STATE_ORDER.length : i     // 认不出的排最后，但不丢
}

/** 每个状态的交易含义。写在这里而不是让人猜——状态名本身不自解释 */
const STATE_META: Record<string, { label: string; hint: string; tone: string }> = {
  REPAIRING:       { label: '修复中',   tone: 'text-accent',
                     hint: '二波修复中，核心观察' },
  CROSS_SUCCESS:   { label: '穿越成功', tone: 'text-up',
                     hint: '已完成二波价格结构确认，且趋势仍健康。不等于可以买' },
  CROSS_WEAKENING: { label: '成功后走弱', tone: 'text-warn',
                     hint: '曾经穿越成功，但趋势已恶化——明确退出核心机会池' },
  BROKEN:          { label: '刚断板',   tone: 'text-text-secondary',
                     hint: '第一段连板结束，结构还没演化' },
  STREAKING:       { label: '连板中',   tone: 'text-up',
                     hint: '尚未断板' },
  CROSS_FAILED:    { label: '修复失败', tone: 'text-down',
                     hint: '本次修复失败（创断板后新低，或连续收在 MA5 下）' },
  FADED:           { label: '周期结束', tone: 'text-text-muted',
                     hint: '当前这段周期生命周期结束，默认弱化' },
  UNKNOWN:         { label: '数据不足', tone: 'text-text-muted',
                     hint: '今天的价格事实不足以判断——不拿"破位/失败"顶替"不知道"' },
  NO_CYCLE:        { label: '无周期',   tone: 'text-warn',
                     hint: '仍在东财召回的强势池里，但本地重算不出 ≥4 连板周期。'
                         + '可能是对方口径不同，也可能是我们算错了——列在这里而不是'
                         + '从池子里删掉，因为静默消失就永远查不出来' },
}

const TAB_HINT: Record<Group, string> = {
  core: '第一次转强（修复中）+ 已完成二波结构（穿越成功）。这里只是观察名单，买点由人确认',
  waiting: '还没进场：仍在连板中、刚断板结构未演化，或今天判不出来',
  dropped: '已从核心池剔除：成功后走弱 / 修复失败 / 周期结束',
  all: '整个强势池，一只不落',
  unbucketed: '归不了类的股票。这一栏非 0 就是 bug——但宁可让它显眼，也不能让股票消失',
}

function StateTag({ r }: { r: LeaderCycleItem }) {
  const st = r.lifecycle_state
  if (!st) return <span className="text-text-muted/50">—</span>
  const m = STATE_META[st] ?? { label: st, tone: 'text-text-secondary', hint: '' }
  const why = r.transition_reasons?.join('；')
  return (
    <span className={cn('inline-flex items-center gap-1', m.tone)}
          title={[m.hint,
                  r.transitioned_today ? '今日刚转入此状态' : null,
                  why && `判定依据：${why}`,
                  r.state_since_date && `${r.state_since_date} 起`,
                 ].filter(Boolean).join('\n')}>
      {/* 用文字不用符号：▲ 自带方向暗示，而它同时标记转强和转弱，
          「▲周期结束」读起来像"向上进入周期结束"，是反的。
          而且要靠问才知道含义的标记，等于没有标记 */}
      {r.transitioned_today && (
        <span className="text-[9px] px-1 rounded bg-current/15 leading-tight">今日</span>
      )}
      {m.label}
      {st === 'CROSS_WEAKENING' && r.ever_cross_success && (
        <span className="text-[9px] text-text-muted">曾成功</span>
      )}
    </span>
  )
}

/** 有值才渲染；null 一律显示 — ，绝不用 0 或"持平"顶替"不知道" */
function Val({ v, digits = 1, suffix = '', signed = false }: {
  v: number | null; digits?: number; suffix?: string; signed?: boolean
}) {
  if (v === null || v === undefined) return <span className="text-text-muted/50">—</span>
  const tone = !signed ? '' : v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-text-secondary'
  return (
    <span className={cn(NUM, tone)}>
      {signed && v > 0 ? '+' : ''}{v.toFixed(digits)}{suffix}
    </span>
  )
}

/**
 * 一行"只有代码和名字"的空壳。给识别不出周期的股票用——它们没有任何价格事实，
 * 所有事实列都得是 null，**绝不能填 0 冒充**。
 */
const EMPTY_ITEM = {
  code: '', name: null, sector_name: null, peak_board_count: null,
  board_count_60d: null, cycle_start_date: null, cycle_peak_date: null,
  break_date: null, days_since_break: null, peak_price: null,
  post_break_high: null, post_break_low: null, latest_close: null,
  peak_drawdown: null, ma5: null, ma10: null, ma20: null, ma30: null,
  ma_window_complete: null, rs_market_10: null, rs_market_20: null,
  rs_market_60: null, rs_sector_10: null, rs_sector_20: null, rs_sector_60: null,
  volume: null, amount: null, turnover_rate: null,
  rs_market_20_delta_1d: null, rs_market_20_delta_3d: null,
  rs_sector_20_delta_1d: null, dist_to_post_break_high: null,
  dist_to_cycle_peak: null, new_post_break_high_today: null,
  new_post_break_low_today: null, volume_ratio_5d: null, amount_ratio_5d: null,
  bar_count: null, data_fresh: null, bar_settled: null, latest_bar_date: null,
  lifecycle_state: null, previous_lifecycle_state: null, state_since_date: null,
  transitioned_today: false, lifecycle_formula_version: null,
  transition_reason_codes: [], transition_reasons: [], evaluation_status: null,
  ever_cross_success: false, first_cross_success_date: null,
  missing_days: null, peak_board_confident: null,
}

function MaPos({ close, ma }: { close: number | null; ma: number | null }) {
  if (!close || !ma || ma <= 0) return <span className="text-text-muted/50">—</span>
  const pct = (close / ma - 1) * 100
  return (
    <span className={cn(NUM, pct >= 0 ? 'text-up' : 'text-down')}>
      {pct >= 0 ? '↑' : '↓'}{Math.abs(pct).toFixed(1)}%
    </span>
  )
}

export default function LeaderCyclePanel() {
  const [group, setGroup] = useState<Group>('core')
  const { data, isLoading } = useQuery({
    queryKey: ['leader-cycle'],
    queryFn: () => fetchLeaderCycle(),
    staleTime: 10 * 60 * 1000,
  })

  const cov = data?.coverage ?? {}
  const total = cov.total ?? 0          // = 整个强势池，不是已识别出周期的数量

  // **每一只都必须是表格里的一行。** 2026-09-04 起后端给识别不出周期的股票也
  // 落行（周期字段整组 NULL、lifecycle_state=NO_CYCLE），所以这里不再需要造假行。
  // unresolved 现在只剩一种：连 K 线都没有、压根建不出行——那种仍要补成空壳，
  // 否则它会从界面上消失
  const all = useMemo(() => {
    const pseudo = (data?.unresolved ?? []).map((u) => ({
      ...EMPTY_ITEM, code: u.code, name: u.name,
      board_count_60d: u.board_count_60d,
      lifecycle_state: 'NO_CYCLE' as LifecycleState,
      transition_reasons: [u.reason],
      transition_reason_codes: ['NO_CYCLE'],
      evaluation_status: 'INSUFFICIENT',
    })) as LeaderCycleItem[]
    return [...(data?.running ?? []), ...(data?.broken ?? []), ...pseudo]
  }, [data])

  const counts = useMemo(() => {
    const c: Record<string, number> = { core: 0, waiting: 0, dropped: 0,
                                        unbucketed: 0, all: all.length }
    all.forEach((r) => {
      const st = r.lifecycle_state ?? 'UNKNOWN'
      const g = (['core', 'waiting', 'dropped'] as Group[])
        .find((k) => (BUCKET[k] as string[]).includes(st))
      // 落到 unbucketed 只有一种可能：出现了这里没列的新状态。那是 bug，
      // 而不是"数据不足"——后者是合法状态，已经归进待观察了
      c[g ?? 'unbucketed'] += 1
    })
    return c
  }, [all])

  const rows = useMemo(() => {
    const picked =
      group === 'all' ? all
      : group === 'unbucketed'
        ? all.filter((r) => !BUCKETED.has(r.lifecycle_state ?? 'UNKNOWN'))
        : all.filter((r) => (BUCKET[group] as string[])
            .includes(r.lifecycle_state ?? 'UNKNOWN'))
    // 先按状态分段（段内顺序见 STATE_ORDER），段内今天刚变的在前——
    // 转强和转弱都是当天才需要动脑子的事
    return [...picked].sort((a, b) =>
      orderOf(a.lifecycle_state) - orderOf(b.lifecycle_state)
      || Number(b.transitioned_today) - Number(a.transitioned_today)
      || (a.days_since_break ?? 1e6) - (b.days_since_break ?? 1e6))
  }, [all, group])

  // 今天的两个动作信号：谁第一次转强、谁从强转弱
  const turned = useMemo(() => ({
    up: all.filter((r) => r.transitioned_today
      && (BUCKET.core as string[]).includes(r.lifecycle_state ?? '')),
    down: all.filter((r) => r.transitioned_today
      && r.lifecycle_state === 'CROSS_WEAKENING'),
  }), [all])

  // 池子里有几只、界面上显示了几只，必须对得上
  const missing = total > 0 ? total - all.length : 0

  return (
    <div className="space-y-3">
      <div className="card p-3 space-y-2">
        <div className="flex items-start gap-2 text-[11px] text-text-secondary leading-relaxed">
          <Info className="w-3.5 h-3.5 shrink-0 mt-0.5 text-text-muted" />
          <div>
            <span className="text-text-primary font-medium">口径：</span>
            {data?.scope_note ?? '高标池 = 近60个交易日最高连板 ≥ 4'}
            <span className="text-text-muted">
              。本页只展示事实，<span className="text-text-primary">不给生命周期状态标签</span>
              ——状态机的阈值要先攒够历史分布才能定，否则又是一个拍脑袋的黑箱。
            </span>
          </div>
        </div>
        {total > 0 && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] pt-1 border-t border-bg-border">
            <span className="text-text-muted">数据覆盖</span>
            {([['连板数可信', cov.peak_board_confident], ['均线完整', cov.ma_window_complete],
               ['成交量', cov.volume], ['相对强度', cov.rs_market],
               ['RS变化', cov.rs_delta], ['当日已结算', cov.settled],
               ['状态判得出', cov.lifecycle_resolved],
               ['换手率', cov.turnover_rate]] as const).map(([label, n]) => {
              const pct = total ? Math.round(((n ?? 0) / total) * 100) : 0
              return (
                <span key={label} className="flex items-center gap-1">
                  <span className="text-text-secondary">{label}</span>
                  <span className={cn(NUM, pct >= 90 ? 'text-up'
                    : pct >= 60 ? 'text-text-primary' : 'text-warn')}>{n ?? 0}/{total}</span>
                </span>
              )
            })}
            <span className="text-text-muted/70">
              分母 = 强势池 {cov.pool_total ?? total} 只（含识别不出周期的
              <span className={NUM}> {cov.cycle_unresolved ?? 0} </span>只）
            </span>
          </div>
        )}
        {missing !== 0 && (
          <div className="text-[11px] pt-1 border-t border-bg-border flex items-start
                          gap-1 text-down">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>
              <span className="font-medium">对不上：</span>强势池 {total} 只，
              界面上 {all.length} 只，差 <span className={NUM}>{Math.abs(missing)}</span> 只。
              {missing > 0
                ? '有股票没出现在任何分组里——这是 bug。一只股票因为归不了类而从界面消失，比判断错更糟，判断错还能被看见并纠正。'
                : '界面上比池子还多，多半是已移出强势池但当日快照还在（口径变化的正常残留），不影响可见性。'}
            </span>
          </div>
        )}
      </div>

      {(turned.up.length > 0 || turned.down.length > 0) && (
        <div className="card p-2.5 text-[11px] flex flex-wrap items-center gap-x-5 gap-y-1">
          <span className="text-text-muted">今日变化</span>
          <span className="flex items-center gap-1">
            <span className="text-up">↑ 转强</span>
            <span className={cn(NUM, 'text-text-primary')}>{turned.up.length}</span>
            <span className="text-text-secondary">
              {turned.up.map((r) => r.name || r.code).join('、') || '—'}</span>
          </span>
          <span className="flex items-center gap-1">
            <span className="text-warn">↓ 转弱</span>
            <span className={cn(NUM, 'text-text-primary')}>{turned.down.length}</span>
            <span className="text-text-secondary">
              {turned.down.map((r) => r.name || r.code).join('、') || '—'}</span>
          </span>
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap">
        {(['core', 'waiting', 'dropped', 'all'] as Group[]).map((k) => (
          <button key={k} onClick={() => setGroup(k)}
            className={cn('text-xs px-3 py-1 rounded border transition-colors',
              k === 'dropped' && group !== k ? 'opacity-60' : '',
              group === k ? 'border-accent/50 text-accent bg-accent/10'
                          : 'border-bg-border text-text-secondary hover:text-text-primary')}>
            {({ core: '核心观察', waiting: '待观察', dropped: '已剔除',
                all: '全部' } as Record<string, string>)[k]}
            {' '}<span className={NUM}>{counts[k] ?? 0}</span>
          </button>
        ))}
        {counts.unbucketed > 0 && (
          <button onClick={() => setGroup('unbucketed')}
            className={cn('text-xs px-3 py-1 rounded border transition-colors',
              'border-down/60 text-down bg-down/10')}>
            未归类 <span className={NUM}>{counts.unbucketed}</span>
          </button>
        )}
      </div>
      <div className="text-[11px] text-text-secondary">
        <span className="mr-2 text-text-muted">
          <span className="text-[9px] px-1 rounded bg-text-muted/20">今日</span>
          {' '}= 今天刚转入该状态
        </span>
        {TAB_HINT[group]}
        {group === 'core' && (
          <span className="text-text-muted">
            {' '}这一层只描述<span className="text-text-primary">价格结构</span>，
            不含领导力和交易许可（Phase 2）。
          </span>
        )}
      </div>

      {isLoading ? <LoadingRows /> : rows.length === 0 ? (
        <div className="card p-8 text-center text-text-muted text-sm">该分组暂无股票</div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-xs" style={{ minWidth: 1380 }}>
            <thead>
              <tr className="text-[10px] text-text-muted uppercase tracking-wider">
                {COLS.map((h) => (
                  <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap
                                         border-b border-bg-border">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const st = r.lifecycle_state ?? 'UNKNOWN'
                const head = i === 0 || st !== (rows[i - 1].lifecycle_state ?? 'UNKNOWN')
                const m = STATE_META[st]
                const n = rows.filter(
                  (x) => (x.lifecycle_state ?? 'UNKNOWN') === st).length
                return (
                  <Fragment key={r.code}>
                    {head && (
                      <tr className="bg-bg-elevated/50">
                        <td colSpan={COLS.length}
                            className="px-3 py-1.5 border-y border-bg-border">
                          <span className={cn('text-[11px] font-medium', m?.tone)}>
                            {m?.label ?? st}
                          </span>
                          <span className={cn('ml-1.5 text-[11px] text-text-muted', NUM)}>
                            {n}
                          </span>
                          {m?.hint && (
                            <span className="ml-2 text-[10px] text-text-muted/80">
                              {m.hint}
                            </span>
                          )}
                        </td>
                      </tr>
                    )}
                    <Row r={r} />
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function Row({ r }: { r: LeaderCycleItem }) {
  const suspect = r.peak_board_confident === false
  return (
    <tr className="border-b border-bg-border/60 last:border-0 hover:bg-bg-elevated/40">
      <td className="px-3 py-2 whitespace-nowrap">
        <span className="text-text-primary font-medium">{r.name || r.code}</span>
        <span className={cn('ml-1.5 text-[10px] text-text-muted', NUM)}>{r.code}</span>
      </td>
      <td className="px-3 py-2 whitespace-nowrap text-xs"><StateTag r={r} /></td>
      <td className="px-3 py-2 text-text-secondary whitespace-nowrap max-w-[8rem] truncate"
          title={r.sector_name || ''}>{r.sector_name || '—'}</td>
      <td className="px-3 py-2 whitespace-nowrap">
        <span className={cn(NUM, 'text-up font-semibold')}>{r.peak_board_count ?? '—'}</span>
        <span className="text-text-muted">板</span>
        {suspect && (
          <span title={`周期区间内有 ${r.missing_days} 个交易日没有快照，连板数可能偏低`}
                className="ml-1 inline-flex align-middle text-warn">
            <AlertTriangle className="w-3 h-3" />
          </span>
        )}
      </td>
      <td className={cn('px-3 py-2', NUM, 'text-text-secondary')}>{r.board_count_60d ?? '—'}</td>
      <td className={cn('px-3 py-2', NUM)}>
        {r.days_since_break === null || r.days_since_break === undefined
          ? <span className="text-text-muted/50">—</span>
          : <span className="text-text-primary">D+{r.days_since_break}</span>}
      </td>
      <td className="px-3 py-2"><Val v={r.peak_drawdown} suffix="%" signed /></td>
      <td className="px-3 py-2"><MaPos close={r.latest_close} ma={r.ma5} /></td>
      <td className="px-3 py-2"><MaPos close={r.latest_close} ma={r.ma10} /></td>
      <td className="px-3 py-2">
        <Val v={r.dist_to_post_break_high} suffix="%" />
        {r.new_post_break_high_today && (
          <span title="今日收盘创断板后新高" className="ml-1 inline-flex align-middle text-up">
            <TrendingUp className="w-3 h-3" />
          </span>
        )}
      </td>
      <td className="px-3 py-2"><Val v={r.dist_to_cycle_peak} suffix="%" /></td>
      <td className="px-3 py-2"><Val v={r.rs_market_20} signed /></td>
      <td className="px-3 py-2"><Val v={r.rs_market_20_delta_1d} signed /></td>
      <td className="px-3 py-2"><Val v={r.rs_market_20_delta_3d} signed /></td>
      <td className="px-3 py-2"><Val v={r.rs_sector_20} signed /></td>
      <td className="px-3 py-2"><Val v={r.volume_ratio_5d} digits={2} /></td>
      <td className="px-3 py-2"><Val v={r.turnover_rate} suffix="%" /></td>
    </tr>
  )
}
