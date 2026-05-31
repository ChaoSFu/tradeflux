import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell,
} from 'recharts'
import type { StockSnapshot } from '@/types'
import { format } from 'date-fns'

interface StockPriceChartProps {
  data: StockSnapshot[]
}

const CustomTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null
  const d = payload[0]?.payload
  if (!d) return null
  return (
    <div className="card p-2 text-xs space-y-1 shadow-xl min-w-32">
      <div className="text-text-muted">{label}</div>
      <div className="flex justify-between gap-4">
        <span className="text-text-secondary">收盘</span>
        <span className="font-mono text-text-primary">{d.close_price?.toFixed(2) ?? '--'}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-text-secondary">涨跌</span>
        <span className={`font-mono ${(d.pct_change ?? 0) >= 0 ? 'text-up' : 'text-down'}`}>
          {d.pct_change != null ? `${d.pct_change >= 0 ? '+' : ''}${d.pct_change.toFixed(2)}%` : '--'}
        </span>
      </div>
      {d.is_limit_up && <div className="text-dragon font-medium">涨停 ▲</div>}
      {d.is_broken_board && <div className="text-down font-medium">炸板 ✕</div>}
    </div>
  )
}

export function StockPriceChart({ data }: StockPriceChartProps) {
  const chartData = data.map((d) => ({
    date: format(new Date(d.date), 'MM/dd'),
    price: d.close_price,
    pct: d.pct_change,
    is_limit_up: d.is_limit_up,
    is_broken_board: d.is_broken_board,
    close_price: d.close_price,
  }))

  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={chartData} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#1E2538" />
        <XAxis dataKey="date" tick={{ fill: '#505570', fontSize: 11 }} axisLine={false} tickLine={false} />
        <YAxis yAxisId="price" orientation="left" tick={{ fill: '#505570', fontSize: 11 }} axisLine={false} tickLine={false} domain={['auto', 'auto']} />
        <YAxis yAxisId="pct" orientation="right" tick={{ fill: '#505570', fontSize: 11 }} axisLine={false} tickLine={false} />
        <Tooltip content={<CustomTooltip />} />
        <Bar yAxisId="pct" dataKey="pct" barSize={4}>
          {chartData.map((entry, i) => (
            <Cell
              key={i}
              fill={
                entry.is_limit_up ? '#FFD700'
                  : entry.is_broken_board ? '#26C28166'
                  : (entry.pct ?? 0) >= 0 ? '#FF456066' : '#26C28166'
              }
            />
          ))}
        </Bar>
        <Line
          yAxisId="price"
          type="monotone"
          dataKey="price"
          stroke="#4F9CF9"
          strokeWidth={1.5}
          dot={false}
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
