import {
  RadarChart, Radar, PolarGrid, PolarAngleAxis, PolarRadiusAxis,
  ResponsiveContainer, Tooltip,
} from 'recharts'
import type { Sector } from '@/types'

interface SectorRadarChartProps {
  sector: Sector
}

export function SectorRadarChart({ sector }: SectorRadarChartProps) {
  const data = [
    { subject: '情绪', value: sector.emotion_score },
    { subject: '连续性', value: sector.continuity_score },
    { subject: '强股数', value: Math.min(100, sector.strong_stock_count * 15) },
    { subject: '板高', value: Math.min(100, sector.board_height * 18) },
    { subject: '安全性', value: Math.max(0, 100 - sector.risk_score) },
    { subject: '涨停数', value: Math.min(100, sector.limit_up_count * 25) },
  ]

  return (
    <ResponsiveContainer width="100%" height="100%">
      <RadarChart data={data} margin={{ top: 10, right: 20, bottom: 10, left: 20 }}>
        <PolarGrid stroke="#1E2538" />
        <PolarAngleAxis dataKey="subject" tick={{ fill: '#8A90A8', fontSize: 11 }} />
        <PolarRadiusAxis angle={30} domain={[0, 100]} tick={false} axisLine={false} />
        <Radar
          name={sector.name}
          dataKey="value"
          stroke="#4F9CF9"
          fill="#4F9CF9"
          fillOpacity={0.15}
          strokeWidth={1.5}
        />
        <Tooltip
          contentStyle={{ background: '#0F1117', border: '1px solid #1E2538', borderRadius: 6, fontSize: 11 }}
          labelStyle={{ color: '#8A90A8' }}
          itemStyle={{ color: '#4F9CF9' }}
        />
      </RadarChart>
    </ResponsiveContainer>
  )
}
