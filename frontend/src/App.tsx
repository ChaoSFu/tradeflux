import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Layout } from '@/components/layout/Layout'
import Home from '@/pages/Home'
import MarketTrend from '@/pages/MarketTrend'
import Dashboard from '@/pages/Dashboard'
import StockPool from '@/pages/StockPool'
import Watchlist from '@/pages/Watchlist'
import StockDetail from '@/pages/StockDetail'
import SectorConfig from '@/pages/SectorConfig'
import PoolConfig from '@/pages/PoolConfig'
import { ProtectedRoute } from '@/components/auth/ProtectedRoute'
import SectorTrend from '@/pages/SectorTrend'
import SectorEmotion from '@/pages/SectorEmotion'
import Signals from '@/pages/Signals'
import DailyReview from '@/pages/DailyReview'
import LimitMovesDashboard from '@/pages/LimitMovesDashboard'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="market-trend" element={<MarketTrend />} />
          <Route path="strong" element={<Dashboard />} />
          <Route path="stocks" element={<StockPool />} />
          <Route path="watchlist" element={<Watchlist />} />
          <Route path="stocks/:code" element={<StockDetail />} />
          <Route path="sector-config" element={<ProtectedRoute><SectorConfig /></ProtectedRoute>} />
          <Route path="pool-config" element={<PoolConfig />} />
          <Route path="sector-trend" element={<SectorTrend />} />
          <Route path="sector-emotion" element={<SectorEmotion />} />
          <Route path="signals" element={<Signals />} />
          <Route path="review" element={<DailyReview />} />
          {/* 涨跌停分析 */}
          <Route path="limit-moves" element={<LimitMovesDashboard />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
