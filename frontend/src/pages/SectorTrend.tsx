import { useState } from 'react'
import SectorRanking from './SectorRanking'
import { PhaseLifecycleBar } from '@/components/common/PhaseLifecycleBar'

export default function SectorTrend() {
  // 选中的生命周期阶段(0-6)：点击分布条某阶段 → 下方板块列表只显示该阶段板块
  const [phase, setPhase] = useState<number | null>(null)
  return (
    <div className="space-y-3">
      <PhaseLifecycleBar selected={phase} onSelect={setPhase} />
      <SectorRanking fixedView="trend" phaseFilter={phase} />
    </div>
  )
}
