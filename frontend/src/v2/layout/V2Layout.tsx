/**
 * V2 外壳。**跟 V1 完全并行**：独立路由、独立侧栏，共用同一个后端、同一份
 * React Query 缓存、同一个 auth store。V1 一行没动。
 */
import { Outlet, useLocation } from 'react-router-dom'
import { V2Sidebar } from './V2Sidebar'

const TITLES: Record<string, string> = {
  '/v2': '交易台 Trading Console',
  '/v2/market': '市场 Market',
  '/v2/mainlines': '主线 Mainlines',
  '/v2/core': '核心股 Core Stocks',
  '/v2/regulatory': '监管 Regulatory',
  '/v2/model': '模型说明 Model Guide',
  '/v2/settings': '设置 Settings',
}

export default function V2Layout() {
  const { pathname } = useLocation()
  return (
    <div className="flex h-screen overflow-hidden bg-bg-base">
      <V2Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <header className="h-12 shrink-0 border-b border-bg-border bg-bg-elevated
                           flex items-center px-5">
          <h1 className="text-sm text-text-primary">{TITLES[pathname] ?? 'TradeFlux V2'}</h1>
          <span className="ml-3 text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent">
            V2
          </span>
          <span className="ml-auto text-[11px] text-text-muted">
            仅供辅助分析，不构成投资建议
          </span>
        </header>
        <main className="flex-1 overflow-auto p-5">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
