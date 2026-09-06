/**
 * 市场 —— 指数趋势 + 市场广度 + 赚钱效应。
 *
 * **不显示情绪温度**：那是自造的复合分，说不清由什么构成。这里全部是可核对的
 * 原始事实：收盘、均线、排列、斜率、涨跌家数、涨跌停数、cohort 次日反馈。
 */
import { useQuery } from '@tanstack/react-query'
import { fetchMarketTrend } from '@/api/marketTrend'
import { fetchMarketEffectLatest } from '@/api/marketEffects'
import { LoadingRows } from '@/components/common/LoadingSpinner'
import { cn } from '@/utils/cn'
import { GateCard } from '../components/GateCard'
import { Empty } from '../components/DataGap'
import { marketGate } from '../domain/marketGate'

const Q = { staleTime: 10 * 60 * 1000 }
const NUM = 'font-mono tabular-nums'
const pct = (v: number | null | undefined, d = 1) =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(d)}%`

export default function Market() {
  const trend = useQuery({ queryKey: ['market-trend'], queryFn: () => fetchMarketTrend(), ...Q })
  const effect = useQuery({ queryKey: ['market-effect-latest'],
                            queryFn: fetchMarketEffectLatest, ...Q })

  return (
    <div className="space-y-4">
      <GateCard name="市场" gate={marketGate(trend.data)} />

      <section className="card p-4">
        <h2 className="text-sm text-text-primary mb-3">指数趋势</h2>
        {trend.isLoading ? <LoadingRows /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs" style={{ minWidth: 860 }}>
              <thead>
                <tr className="text-[10px] text-text-muted">
                  {['指数', '状态', '收盘', '今日', '5日', '20日', '均线排列',
                    '>MA5', '>MA10', '>MA20', '>MA60', 'MA20斜率', 'MA60斜率'].map((h) => (
                    <th key={h} className="px-2 py-1.5 text-left font-medium
                                           border-b border-bg-border whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(trend.data?.indices ?? []).map((i) => (
                  <tr key={i.code} className="border-b border-bg-border/50 last:border-0">
                    <td className="px-2 py-1.5 text-text-primary whitespace-nowrap">{i.name}</td>
                    <td className={cn('px-2 py-1.5',
                      ['strong', 'bullish'].includes(i.state) ? 'text-up'
                        : ['bearish', 'weak'].includes(i.state) ? 'text-down'
                        : 'text-text-secondary')}>{i.state_label}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{i.close.toFixed(2)}</td>
                    <td className={cn('px-2 py-1.5', NUM,
                      i.pct_change >= 0 ? 'text-up' : 'text-down')}>{pct(i.pct_change, 2)}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{pct(i.pct_5d)}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{pct(i.pct_20d)}</td>
                    <td className="px-2 py-1.5">
                      {i.alignment === 'bull' ? <span className="text-up">多头</span>
                        : i.alignment === 'bear' ? <span className="text-down">空头</span>
                        : <span className="text-text-secondary">交织</span>}
                    </td>
                    {[i.above_ma5, i.above_ma10, i.above_ma20, i.above_ma60].map((b, k) => (
                      <td key={k} className="px-2 py-1.5">
                        {b ? <span className="text-up">是</span>
                           : <span className="text-text-muted">否</span>}
                      </td>
                    ))}
                    <td className={cn('px-2 py-1.5', NUM)}>{pct(i.ma20_slope_pct, 2)}</td>
                    <td className={cn('px-2 py-1.5', NUM)}>{pct(i.ma60_slope_pct, 2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-[11px] text-text-muted mt-2">
          全部来自后端 market-trend 已算好的字段，前端不重算均线——同一个事实只能有
          一套判定。
        </p>
      </section>

      <section className="card p-4">
        <h2 className="text-sm text-text-primary">赚钱效应 · 昨日群体今日反馈</h2>
        <p className="text-[11px] text-text-muted mt-1">
          真正的赚钱效应是「昨天最强的那批今天还赚不赚钱」，不是一个 0~100 的强度分。
          所以这里只给 cohort 的原始反馈；V1 的 profit_strength / loss_strength
          保留在 V1，不在 V2 占据视觉中心。
        </p>
        {effect.isLoading ? <LoadingRows />
          : !effect.data ? <Empty text="暂无市场效应数据" /> : (
          <div className="mt-3 text-xs text-text-secondary">
            <MarketEffectRaw data={effect.data} />
          </div>
        )}
      </section>
    </div>
  )
}

/**
 * cohort 的次日反馈 —— **这才是真正的赚钱效应**。
 *
 * 首版这里用了通用的 key-value 倾倒，结果把 `profit_strength 61.1`、
 * `loss_strength 26.8` 直接摆到了页面上——正是 V2 说好不放在视觉中心的两个
 * 自造复合分。偷懒的通用渲染会绕过设计原则，所以这里改成显式的列。
 *
 * 每一列都是可核对的原始比率，`profit_strength / loss_strength / quadrant`
 * 这些加工过的字段留在 V1，V2 不显示。
 */
function MarketEffectRaw({ data }: { data: any }) {
  const cohorts = data?.cohorts ?? {}
  const order = ['limit_up', 'first_board', 'multi_board', 'broken_board',
                 'strong_proxy', 'limit_down']
  const rows = order.map((k) => cohorts[k]).filter(Boolean)
  if (!rows.length) return <Empty text="该日无 cohort 数据" />
  const rate = (v: number | null | undefined) =>
    v === null || v === undefined ? '—' : `${(v * 100).toFixed(0)}%`
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs" style={{ minWidth: 720 }}>
        <thead>
          <tr className="text-[10px] text-text-muted">
            {['昨日群体', '样本', '有效', '今日中位收益', '红盘率', '晋级率',
              '断板率', '大亏率'].map((h) => (
              <th key={h} className="px-2 py-1.5 text-left font-medium
                                     border-b border-bg-border whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((c: any) => (
            <tr key={c.cohort_type} className="border-b border-bg-border/50 last:border-0">
              <td className="px-2 py-1.5 text-text-primary whitespace-nowrap">{c.label}</td>
              <td className={cn('px-2 py-1.5', NUM)}>{c.member_count}</td>
              <td className={cn('px-2 py-1.5', NUM, 'text-text-muted')}>{c.valid_count}</td>
              <td className={cn('px-2 py-1.5', NUM,
                c.median_pct_change === null ? ''
                  : c.median_pct_change >= 0 ? 'text-up' : 'text-down')}>
                {pct(c.median_pct_change, 2)}
              </td>
              <td className={cn('px-2 py-1.5', NUM)}>{rate(c.red_ratio)}</td>
              <td className={cn('px-2 py-1.5', NUM)}>{rate(c.advance_ratio)}</td>
              <td className={cn('px-2 py-1.5', NUM)}>{rate(c.broken_ratio)}</td>
              <td className={cn('px-2 py-1.5', NUM)}>{rate(c.large_loss_ratio)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] text-text-muted mt-2">
        「有效」小于「样本」是正常的：停牌、退市、次日无数据的成员不计入比率，
        不用 0 顶替。
      </p>
    </div>
  )
}
