/**
 * 高标龙头生命周期 —— **事实层展示，不给状态标签**。
 *
 * 页面上刻意没有 RUNNING/RECLAIMING/CROSS_SUCCESS 这类词。状态机的六条转换全是人定
 * 阈值，而现在没有任何数据能验证它们合不合理——先攒几周快照，看清 days_since_break、
 * peak_drawdown、RS 的真实分布，**先有分布再定阈值**，否则就是又造一个拍脑袋的黑箱。
 *
 * 分组只按一个纯事实切：断板了没有。不做 D+1~3 / D+4~10 的分桶——桶边界本身就是阈值。
 *
 * 每个可信度标记都对应一件"我们不确定"的事，不是装饰：
 *   ⚠ 连板数   周期区间内有交易日没快照 → 可能偏低（计数循环分不清"那天没涨停"
 *              和"那天我们没记录"）
 *   RS 空白    锚点日没有收盘价 / 无对应基准 → 不用邻近日期近似顶替
 *   换手空白   没有流通股本观测，或观测超 45 天（除权、解禁会让它台阶式跳变）
 */
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, Info } from 'lucide-react'
import { fetchLeaderCycle, type LeaderCycleItem } from '@/api/stocks'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'

type Group = 'broken' | 'running'
const NUM = 'font-mono tabular-nums'

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
  const [group, setGroup] = useState<Group>('broken')
  const { data, isLoading } = useQuery({
    queryKey: ['leader-cycle'],
    queryFn: () => fetchLeaderCycle(),
    staleTime: 10 * 60 * 1000,
  })

  const rows = useMemo(
    () => (group === 'broken' ? data?.broken : data?.running) ?? [], [data, group])
  const cov = data?.coverage ?? {}
  const total = cov.total ?? 0

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
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        {([['broken', '已断板', data?.broken.length],
           ['running', '连板中', data?.running.length]] as const).map(([k, label, n]) => (
          <button key={k} onClick={() => setGroup(k as Group)}
            className={cn('text-xs px-3 py-1 rounded border transition-colors',
              group === k ? 'border-accent/50 text-accent bg-accent/10'
                          : 'border-bg-border text-text-secondary hover:text-text-primary')}>
            {label} <span className={NUM}>{n ?? 0}</span>
          </button>
        ))}
        <span className="text-[11px] text-text-muted">
          {group === 'broken' ? '按距断板交易日数升序——结构还没走完的排在前面'
                              : '按本轮连板高度降序'}
        </span>
      </div>

      {isLoading ? <LoadingRows /> : rows.length === 0 ? (
        <div className="card p-8 text-center text-text-muted text-sm">该分组暂无股票</div>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full text-xs" style={{ minWidth: 980 }}>
            <thead>
              <tr className="text-[10px] text-text-muted uppercase tracking-wider">
                {['股票', '主板块', '本轮', '60日', 'D+', '峰值回撤',
                  '现价/MA5', '现价/MA10', 'RS市场20', 'RS板块20', '换手'].map((h) => (
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
      <td className="px-3 py-2"><Val v={r.rs_market_20} signed /></td>
      <td className="px-3 py-2"><Val v={r.rs_sector_20} signed /></td>
      <td className="px-3 py-2"><Val v={r.turnover_rate} suffix="%" /></td>
    </tr>
  )
}
