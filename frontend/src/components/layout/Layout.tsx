import { Outlet, useLocation } from 'react-router-dom'
import { Sidebar } from './Sidebar'
import { Header } from './Header'

const TITLES: Record<string, string> = {
  '/': '强势股概览 Strong Overview',
  '/stocks': '活跃股池 Active Pool',
  '/sector-config': '板块展示配置 Sector Config',
  '/sector-trend': '趋势板块 Sector Trend',
  '/sector-emotion': '情绪板块 Sector Emotion',
  '/sector-ranking': '板块涨幅排名 Sector Ranking',
  '/signals': '弱转强信号 Weak-to-Strong',
  '/review': '日复盘 Daily Review',
  '/limit-moves': '涨跌停概览 Limit Moves',
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
