import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Layout } from '@/components/layout/Layout'
import Dashboard from '@/pages/Dashboard'
import StockPool from '@/pages/StockPool'
import StockDetail from '@/pages/StockDetail'
import SectorPool from '@/pages/SectorPool'
import SectorConfig from '@/pages/SectorConfig'
import { ProtectedRoute } from '@/components/auth/ProtectedRoute'
import SectorAnalysis from '@/pages/SectorAnalysis'
import SectorTrend from '@/pages/SectorTrend'
import SectorEmotion from '@/pages/SectorEmotion'
import Signals from '@/pages/Signals'
import DailyReview from '@/pages/DailyReview'
import LimitMovesDashboard from '@/pages/LimitMovesDashboard'
import LimitMovesPool from '@/pages/LimitMovesPool'
import LimitMovesSectors from '@/pages/LimitMovesSectors'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="stocks" element={<StockPool />} />
          <Route path="stocks/:code" element={<StockDetail />} />
          <Route path="sector-pool" element={<SectorPool />} />
          <Route path="sector-config" element={<ProtectedRoute><SectorConfig /></ProtectedRoute>} />
          <Route path="sectors" element={<SectorAnalysis />} />
          <Route path="sector-trend" element={<SectorTrend />} />
          <Route path="sector-emotion" element={<SectorEmotion />} />
          <Route path="signals" element={<Signals />} />
          <Route path="review" element={<DailyReview />} />
          {/* 涨跌停分析 */}
          <Route path="limit-moves" element={<LimitMovesDashboard />} />
          <Route path="limit-moves/stocks" element={<LimitMovesPool />} />
          <Route path="limit-moves/sectors" element={<LimitMovesSectors />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
