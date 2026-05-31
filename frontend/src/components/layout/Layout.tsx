import { Outlet, useLocation } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Header } from './Header'

const TITLES: Record<string, string> = {
  '/': '仪表盘 Dashboard',
  '/stocks': '强势股池 Strong Pool',
  '/sector-pool': '板块强势分布 Sector Pool',
  '/sector-config': '板块展示配置 Sector Config',
  '/sectors': '板块综合分析 Sector Analysis',
  '/sector-ranking': '板块涨幅排名 Sector Ranking',
  '/signals': '弱转强信号 Weak-to-Strong',
  '/review': '日复盘 Daily Review',
  '/limit-moves': '涨跌停概览 Limit Moves',
  '/limit-moves/stocks': '涨跌停池 Limit Pool',
  '/limit-moves/sectors': '涨跌停板块分布 Limit Sectors',
}

export function Layout() {
  const { pathname } = useLocation()
  const title = TITLES[pathname] ?? TITLES[pathname.split('/').slice(0, 2).join('/')] ?? 'TradeFlux'

  return (
    <div className="flex h-screen overflow-hidden bg-bg-base">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden">
        <Header title={title} />
        <main className="flex-1 overflow-auto p-5">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
