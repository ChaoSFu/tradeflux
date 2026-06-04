import SectorRanking from './SectorRanking'
import { PhaseLifecycleBar } from '@/components/common/PhaseLifecycleBar'

export default function SectorTrend() {
  return (
    <div className="space-y-3">
      <PhaseLifecycleBar />
      <SectorRanking fixedView="trend" />
    </div>
  )
}
