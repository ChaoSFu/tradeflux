import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { Layout } from '@/components/layout/Layout'
import Home from '@/pages/Home'
import MarketTrend from '@/pages/MarketTrend'
import MarketEffects from '@/pages/MarketEffects'
import Dashboard from '@/pages/Dashboard'
import StockPool from '@/pages/StockPool'
import Watchlist from '@/pages/Watchlist'
import StockDetail from '@/pages/StockDetail'
import SectorConfig from '@/pages/SectorConfig'
import PoolConfig from '@/pages/PoolConfig'
import { ProtectedRoute } from '@/components/auth/ProtectedRoute'
import SectorTrend from '@/pages/SectorTrend'
import SectorEmotion from '@/pages/SectorEmotion'
import DailyReview from '@/pages/DailyReview'
import LimitMovesDashboard from '@/pages/LimitMovesDashboard'
import TradeJournal from '@/pages/TradeJournal'
import TurnoverOverview from '@/pages/TurnoverOverview'
import WeakToStrongRadar from '@/pages/WeakToStrongRadar'
import SpeculationRadar from '@/pages/SpeculationRadar'
import LimitUpSectorRadar from '@/pages/LimitUpSectorRadar'
import WeakToStrongRadarGuide from '@/pages/WeakToStrongRadarGuide'

// ── V2：独立路由树，跟 V1 完全并行 ──────────────────────────────────────
// 共用同一个后端、同一份 React Query 缓存、同一个 auth store；
// V1 的路由、页面、组件一行没动
import V2Layout from '@/v2/layout/V2Layout'
import TradingConsole from '@/v2/pages/TradingConsole'
import V2Market from '@/v2/pages/Market'
import V2Mainlines from '@/v2/pages/Mainlines'
import V2CoreStocks from '@/v2/pages/CoreStocks'
import V2Regulatory from '@/v2/pages/Regulatory'
import V2ModelGuide from '@/v2/pages/ModelGuide'
import V2Settings from '@/v2/pages/Settings'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="market-trend" element={<MarketTrend />} />
          <Route path="market-effects" element={<MarketEffects />} />
          <Route path="strong" element={<Dashboard />} />
          <Route path="stocks" element={<StockPool />} />
          <Route path="watchlist" element={<Watchlist />} />
          <Route path="stocks/:code" element={<StockDetail />} />
          <Route path="sector-config" element={<ProtectedRoute><SectorConfig /></ProtectedRoute>} />
          <Route path="pool-config" element={<PoolConfig />} />
          <Route path="sector-trend" element={<SectorTrend />} />
          <Route path="sector-emotion" element={<SectorEmotion />} />
          <Route path="speculation-radar" element={<SpeculationRadar />} />
          <Route path="limit-up-radar" element={<LimitUpSectorRadar />} />
          <Route path="weak-to-strong-radar" element={<WeakToStrongRadar />} />
          <Route path="weak-to-strong-radar/guide" element={<WeakToStrongRadarGuide />} />
          <Route path="review" element={<DailyReview />} />
          <Route path="trade-journal" element={<TradeJournal />} />
          {/* 涨跌停分析 */}
          <Route path="limit-moves" element={<LimitMovesDashboard />} />
          <Route path="turnover" element={<TurnoverOverview />} />
        </Route>

        <Route path="/v2" element={<V2Layout />}>
          <Route index element={<TradingConsole />} />
          <Route path="market" element={<V2Market />} />
          <Route path="mainlines" element={<V2Mainlines />} />
          <Route path="core" element={<V2CoreStocks />} />
          <Route path="regulatory" element={<V2Regulatory />} />
          <Route path="model" element={<V2ModelGuide />} />
          <Route path="settings" element={<V2Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
