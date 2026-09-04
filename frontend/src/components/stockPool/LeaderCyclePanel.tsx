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
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Info, TrendingUp } from 'lucide-react'
import { fetchLeaderCycle, type LeaderCycleItem } from '@/api/stocks'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'

type Group = 'core' | 'CROSS_WEAKENING' | 'BROKEN' | 'STREAKING'
           | 'CROSS_FAILED' | 'FADED' | 'UNKNOWN'
const NUM = 'font-mono tabular-nums'

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
                     hint: '今天的事实不足以判断——不拿"破位/失败"顶替"不知道"' },
}

const TABS: Array<[Group, string]> = [
  ['core', '核心机会'], ['CROSS_WEAKENING', '成功后走弱'], ['BROKEN', '刚断板'],
  ['STREAKING', '连板中'], ['CROSS_FAILED', '修复失败'], ['FADED', '周期结束'],
  ['UNKNOWN', '数据不足'],
]

function StateTag({ r }: { r: LeaderCycleItem }) {
  const st = r.lifecycle_state
  if (!st) return <span className="text-text-muted/50">—</span>
  const m = STATE_META[st] ?? { label: st, tone: 'text-text-secondary', hint: '' }
  const why = r.transition_reasons?.join('；')
  return (
    <span className={cn('inline-flex items-center gap-1', m.tone)}
          title={[m.hint, why && `今日判定：${why}`, r.state_since_date &&
                  `${r.state_since_date} 起`].filter(Boolean).join('\n')}>
      {r.transitioned_today && <span className="text-[9px] opacity-70">▲</span>}
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

  const all = useMemo(
    () => [...(data?.running ?? []), ...(data?.broken ?? [])], [data])
  // 「核心机会」= 修复中 + 穿越成功。这两个是唯一值得占用注意力的
  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    all.forEach((r) => { c[r.lifecycle_state ?? 'UNKNOWN'] =
      (c[r.lifecycle_state ?? 'UNKNOWN'] ?? 0) + 1 })
    c.core = (c.REPAIRING ?? 0) + (c.CROSS_SUCCESS ?? 0)
    return c
  }, [all])
  const rows = useMemo(() => {
    const picked = group === 'core'
      ? all.filter((r) => r.lifecycle_state === 'REPAIRING'
                       || r.lifecycle_state === 'CROSS_SUCCESS')
      : all.filter((r) => (r.lifecycle_state ?? 'UNKNOWN') === group)
    // 核心机会里穿越成功排前面；其余按距断板天数
    return picked.sort((a, b) =>
      (a.lifecycle_state === b.lifecycle_state ? 0
        : a.lifecycle_state === 'CROSS_SUCCESS' ? -1 : 1)
      || (a.days_since_break ?? 1e6) - (b.days_since_break ?? 1e6))
  }, [all, group])
  const cov = data?.coverage ?? {}
  const total = cov.total ?? 0          // = 整个强势池，不是已识别出周期的数量
  const unresolved = data?.unresolved ?? []

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
        {unresolved.length > 0 && (
          <div className="text-[11px] pt-1 border-t border-bg-border space-y-1">
            <div className="flex items-center gap-1 text-warn">
              <AlertTriangle className="w-3 h-3" />
              <span>本地识别不出 ≥4 连板周期（{unresolved.length} 只）</span>
            </div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {unresolved.map((u) => (
                <span key={u.code} className="text-text-secondary" title={u.reason}>
                  {u.name || u.code}
                  <span className={cn('ml-1 text-text-muted', NUM)}>
                    {u.code} · 60日{u.board_count_60d ?? '?'}板
                  </span>
                </span>
              ))}
            </div>
            <div className="text-text-muted/70">
              它们仍在东财召回的强势池里，但我们自己重算的连板数没到 4——
              可能是对方口径不同，也可能是我们算错了。<span className="text-text-primary">
              放在这里而不是从分母里删掉</span>，是因为静默消失就永远查不出来。
            </div>
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        {TABS.map(([k, label]) => (
          <button key={k} onClick={() => setGroup(k)}
            className={cn('text-xs px-3 py-1 rounded border transition-colors',
              k === 'FADED' && group !== k ? 'opacity-50' : '',
              group === k ? 'border-accent/50 text-accent bg-accent/10'
                          : 'border-bg-border text-text-secondary hover:text-text-primary')}>
            {label} <span className={NUM}>{counts[k] ?? 0}</span>
          </button>
        ))}
      </div>
      <div className="text-[11px] text-text-secondary">
        {group === 'core'
          ? <>修复中 + 穿越成功。<span className="text-text-primary">穿越成功不等于可以买</span>
              ——这一层只描述价格结构，不含领导力和交易许可（那是 Phase 2）。</>
          : STATE_META[group]?.hint}
      </div>

      {isLoading ? <LoadingRows /> : rows.length === 0 ? (
        <div className="card p-8 text-center text-text-muted text-sm">该分组暂无股票</div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-xs" style={{ minWidth: 1380 }}>
            <thead>
              <tr className="text-[10px] text-text-muted uppercase tracking-wider">
                {['股票', '状态', '主板块', '本轮', '60日', 'D+', '峰值回撤',
                  '现价/MA5', '现价/MA10', '距阶段高', '距周期顶',
                  'RS市场20', 'ΔRS 1日', 'ΔRS 3日', 'RS板块20',
                  '量比5日', '换手'].map((h) => (
                  <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap
                                         border-b border-bg-border">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>{rows.map((r) => <Row key={r.code} r={r} />)}</tbody>
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
