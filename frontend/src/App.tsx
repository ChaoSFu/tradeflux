import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
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
import LimitMovesAnalysis from '@/pages/LimitMovesAnalysis'
import TradeJournal from '@/pages/TradeJournal'
import TurnoverOverview from '@/pages/TurnoverOverview'
import WeakToStrongRadar from '@/pages/WeakToStrongRadar'
import SpeculationRadar from '@/pages/SpeculationRadar'
import LimitUpSectorRadar from '@/pages/LimitUpSectorRadar'
import WeakToStrongRadarGuide from '@/pages/WeakToStrongRadarGuide'
import PreTradeCheck from '@/pages/PreTradeCheck'
import DataHealth from '@/pages/DataHealth'

// ── V2：独立路由树，跟 V1 完全并行 ──────────────────────────────────────
// 2026-09-12 起 V2 冻结：不再维护，只维护 V1 界面。代码和 /v2 路由暂时保留（删不删另说），V1 不要 import @/v2。
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
          <Route path="strong" element={<Dashboard />} />
          <Route path="stocks" element={<StockPool />} />
          <Route path="watchlist" element={<Watchlist />} />
          <Route path="stocks/:code" element={<StockDetail />} />
          <Route path="sector-config" element={<ProtectedRoute><SectorConfig /></ProtectedRoute>} />
          <Route path="pool-config" element={<PoolConfig />} />
          <Route path="sector-trend" element={<SectorTrend />} />
          <Route path="sector-emotion" element={<SectorEmotion />} />
          <Route path="weak-to-strong-radar" element={<WeakToStrongRadar />} />
          <Route path="weak-to-strong-radar/guide" element={<WeakToStrongRadarGuide />} />
          <Route path="review" element={<DailyReview />} />
          <Route path="trade-journal" element={<TradeJournal />} />
          <Route path="pre-trade-check" element={<PreTradeCheck />} />
          {/* 数据体检：管理功能，没登录看不到（后端接口也全部要登录） */}
          <Route path="data-health" element={<ProtectedRoute><DataHealth /></ProtectedRoute>} />
          {/* ── 涨跌停分析：四合一整合页 ─────────────────────────────
              旧的四个页面**没有删**，挪到 /legacy/* 继续可访问。整合页有没有
              漏掉东西，只有对着原页面才看得出来；redirect 如果同时让原页面
              失联，就等于把这个校验能力一起丢了。 */}
          <Route path="limit-moves-analysis" element={<LimitMovesAnalysis />} />
          <Route path="legacy/limit-moves" element={<LimitMovesDashboard />} />
          <Route path="legacy/speculation-radar" element={<SpeculationRadar />} />
          <Route path="legacy/limit-up-radar" element={<LimitUpSectorRadar />} />
          <Route path="legacy/market-effects" element={<MarketEffects />} />
          {/* 老书签 / 老链接落到整合页。replace 不留历史，避免返回键在
              重定向和目标页之间来回弹 */}
          {['limit-moves', 'speculation-radar', 'limit-up-radar', 'market-effects']
            .map((p) => (
              <Route key={p} path={p}
                     element={<Navigate to="/limit-moves-analysis" replace />} />
            ))}
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
