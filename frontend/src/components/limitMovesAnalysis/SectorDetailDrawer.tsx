import { useQuery } from '@tanstack/react-query'
import { cn } from '@/utils/cn'
import { fetchLimitUpRadarSector } from '@/api/limitUpRadar'
import { QueryState } from './QueryState'
import type { SectorRow } from './sectorRows'

const NUM = 'font-mono tabular-nums'
const pct = (v: number | null | undefined, d = 1) =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`
const tone = (v: number | null | undefined) =>
  v === null || v === undefined ? 'text-text-muted' : v > 0 ? 'text-up' : v < 0 ? 'text-down' : ''
/** 封单额（元）→ 亿。null = 东财没给，**不是 0** */
const yi = (v: number | null | undefined) =>
  v === null || v === undefined ? '—' : `${(v / 1e8).toFixed(2)}亿`

function Block({ title, count, children }: {
  title: string; count: number; children: React.ReactNode
}) {
  return (
    <div className="space-y-1">
      <div className="text-[11px] text-text-secondary font-medium">
        {title} <span className="text-text-muted">{count}</span>
      </div>
      {count === 0
        ? <div className="text-[11px] text-text-muted/60 py-1">无</div>
        : <div className="overflow-x-auto">{children}</div>}
    </div>
  )
}

const TH = 'px-2 py-1 text-left font-medium text-text-muted whitespace-nowrap border-b border-bg-border'
const TD = 'px-2 py-1 whitespace-nowrap'

/**
 * 板块展开详情：今日涨停 / 炸板 / 今日跌停 / 历史核心锚。
 *
 * **刻意不显示龙头分、风险分、情绪分。** 那三个是自造的加权分，口径经不起推敲；
 * 后端还留着它们，但这个页面一个都不用。
 */
export function SectorDetailDrawer({ row }: { row: SectorRow }) {
  // **明细在这里才拉，一次一个板块。** 列表接口带 include_stock_lists=false
  // 只要汇总——原来是为了一次点开，把 40 个板块的明细全拉过来（670KB / 1.3s）。
  // 只有跌停侧的板块没有 sector_id，那就不发这个请求
  const secId = row.up?.sector_id ?? null
  const q = useQuery({
    queryKey: ['lur-sector', secId],
    queryFn: () => fetchLimitUpRadarSector(secId!),
    enabled: secId !== null,
    staleTime: 10 * 60 * 1000,
  })
  const up = q.data ?? null

  return (
    <div className="px-3 py-2.5 bg-bg-base/40 space-y-3 text-xs">
      {secId !== null && (q.isPending || q.error) && (
        <QueryState qs={[q]} rows={3}><span /></QueryState>
      )}
      {secId !== null && (
      <Block title="今日涨停" count={up?.today_limit_up_stocks.length ?? 0}>
        <table className="w-full text-[11px]" style={{ minWidth: 860 }}>
          <thead><tr>
            {['股票', '板数', '首封', '最终封板', '炸板次数', '换手', '封单额',
              '10/20/60日涨停', '60日最高板', '涨停原因'].map((h) => (
              <th key={h} className={TH}>{h}</th>))}
          </tr></thead>
          <tbody>
            {(up?.today_limit_up_stocks ?? []).map((s) => (
              <tr key={s.code} className="border-b border-bg-border/40 last:border-0">
                <td className={TD}>
                  <span className="text-text-primary">{s.name}</span>
                  <span className={cn('ml-1 text-text-muted', NUM)}>{s.code}</span>
                </td>
                <td className={cn(TD, NUM, 'text-up')}>{s.board_count ?? '—'}</td>
                <td className={cn(TD, NUM)}>{s.first_limit_time ?? '—'}</td>
                <td className={cn(TD, NUM)}>{s.last_limit_time ?? '—'}</td>
                <td className={cn(TD, NUM)}>{s.broken_times ?? '—'}</td>
                <td className={cn(TD, NUM)}>
                  {s.turnover_rate === null ? '—' : `${s.turnover_rate.toFixed(1)}%`}
                </td>
                <td className={cn(TD, NUM)}>{yi(s.seal_amount)}</td>
                <td className={cn(TD, NUM, 'text-text-secondary')}>
                  {s.limit_up_days_10d ?? '—'}/{s.limit_up_days_20d ?? '—'}/{s.limit_up_days_60d ?? '—'}
                </td>
                <td className={cn(TD, NUM)}>{s.board_count_60d ?? '—'}</td>
                <td className={cn(TD, 'max-w-[16rem] truncate text-text-muted')}
                    title={s.limit_reason || ''}>{s.limit_reason || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Block>)}

      {secId !== null && (
      <Block title="炸板" count={up?.broken_stocks.length ?? 0}>
        <table className="w-full text-[11px]" style={{ minWidth: 640 }}>
          <thead><tr>
            {['股票', '当前涨幅', '距涨停价', '历史板高', '炸板次数', '换手', '成交额']
              .map((h) => <th key={h} className={TH}>{h}</th>)}
          </tr></thead>
          <tbody>
            {(up?.broken_stocks ?? []).map((s) => (
              <tr key={s.code} className="border-b border-bg-border/40 last:border-0">
                <td className={TD}>
                  <span className="text-text-primary">{s.name}</span>
                  <span className={cn('ml-1 text-text-muted', NUM)}>{s.code}</span>
                </td>
                <td className={cn(TD, NUM, tone(s.pct_change))}>{pct(s.pct_change, 2)}</td>
                <td className={cn(TD, NUM, 'text-text-secondary')}>{pct(s.gap_to_limit_pct, 2)}</td>
                <td className={cn(TD, NUM)}>{s.board_count_60d ?? '—'}</td>
                <td className={cn(TD, NUM)}>{s.broken_times ?? '—'}</td>
                <td className={cn(TD, NUM)}>
                  {s.turnover_rate === null ? '—' : `${s.turnover_rate.toFixed(1)}%`}
                </td>
                <td className={cn(TD, NUM)}>{yi(s.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Block>)}

      <Block title="今日跌停" count={row.downStocks.length}>
        <table className="w-full text-[11px]" style={{ minWidth: 480 }}>
          <thead><tr>
            {['股票', '连续跌停', '今日跌幅', '一字跌停', '所属板块']
              .map((h) => <th key={h} className={TH}>{h}</th>)}
          </tr></thead>
          <tbody>
            {row.downStocks.map((s) => (
              <tr key={s.code} className="border-b border-bg-border/40 last:border-0">
                <td className={TD}>
                  <span className="text-text-primary">{s.name}</span>
                  <span className={cn('ml-1 text-text-muted', NUM)}>{s.code}</span>
                </td>
                <td className={cn(TD, NUM, 'text-down')}>{s.today_limit_down_count ?? '—'}</td>
                <td className={cn(TD, NUM, tone(s.today_pct_change))}>{pct(s.today_pct_change, 2)}</td>
                <td className={cn(TD, NUM)}>{s.today_is_one_word_limit_down ? '是' : '—'}</td>
                <td className={cn(TD, 'max-w-[16rem] truncate text-text-muted')}
                    title={(s.sectors ?? []).join('、')}>{(s.sectors ?? []).join('、') || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Block>

      {secId !== null && (
      <Block title="历史核心锚" count={up?.core_stocks.length ?? 0}>
        <table className="w-full text-[11px]" style={{ minWidth: 520 }}>
          <thead><tr>
            {['股票', '今日涨幅', '近10日涨停', '近20日涨停', '近60日涨停', '60日最高板']
              .map((h) => <th key={h} className={TH}>{h}</th>)}
          </tr></thead>
          <tbody>
            {(up?.core_stocks ?? []).map((s) => (
              <tr key={s.code} className="border-b border-bg-border/40 last:border-0">
                <td className={TD}>
                  <span className="text-text-primary">{s.name}</span>
                  <span className={cn('ml-1 text-text-muted', NUM)}>{s.code}</span>
                </td>
                {/* pct_change 为 null = 当日数据还没更新，不是 0% */}
                <td className={cn(TD, NUM, tone(s.pct_change))}>{pct(s.pct_change, 2)}</td>
                <td className={cn(TD, NUM)}>{s.limit_up_days_10d ?? '—'}</td>
                <td className={cn(TD, NUM)}>{s.limit_up_days_20d ?? '—'}</td>
                <td className={cn(TD, NUM)}>{s.limit_up_days_60d ?? '—'}</td>
                <td className={cn(TD, NUM)}>{s.board_count_60d ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Block>)}
    </div>
  )
}
