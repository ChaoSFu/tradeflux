/**
 * V2 一级导航：**只有六项 + 设置**，对应一条决策链上的每一环。
 *
 * V1 的「弱转强雷达 / 破局雷达 / 涨停板块雷达 / 市场效应 / 强势股概览 / 活跃股池 /
 * 成交额概览 / 趋势板块 / 情绪板块」都不在这里——它们是 V2 的**数据来源或
 * 子能力**，不是新的一级产品概念。把它们摆成一级导航，等于让人每天自己在
 * 九个入口里拼决策，而那正是 V2 要解决的问题。
 */
import { NavLink } from 'react-router-dom'
import {
  Gauge, LineChart, Layers, Crosshair, ShieldAlert, BookOpen, Settings, ArrowLeft,
} from 'lucide-react'
import { cn } from '@/utils/cn'

const NAV = [
  { to: '/v2', end: true, icon: Gauge, zh: '交易台', en: 'Trading Console' },
  { to: '/v2/market', icon: LineChart, zh: '市场', en: 'Market' },
  { to: '/v2/mainlines', icon: Layers, zh: '主线', en: 'Mainlines' },
  { to: '/v2/core', icon: Crosshair, zh: '核心股', en: 'Core Stocks' },
  { to: '/v2/regulatory', icon: ShieldAlert, zh: '监管', en: 'Regulatory' },
  { to: '/v2/model', icon: BookOpen, zh: '模型说明', en: 'Model Guide' },
  { to: '/v2/settings', icon: Settings, zh: '设置', en: 'Settings' },
]

export function V2Sidebar() {
  return (
    <aside className="w-48 shrink-0 border-r border-bg-border bg-bg-surface flex flex-col">
      <div className="px-4 py-4 border-b border-bg-border">
        <div className="text-accent font-semibold">TradeFlux</div>
        <div className="text-[11px] text-text-muted">决策链 V2</div>
      </div>
      <nav className="flex-1 py-2">
        {NAV.map(({ to, end, icon: Icon, zh, en }) => (
          <NavLink key={to} to={to} end={end}
            className={({ isActive }) => cn(
              'flex items-center gap-2.5 px-4 py-2.5 text-sm transition-colors',
              isActive ? 'text-accent bg-accent/10 border-r-2 border-accent'
                       : 'text-text-secondary hover:text-text-primary hover:bg-bg-elevated')}>
            <Icon className="w-4 h-4 shrink-0" />
            <span>{zh}</span>
            <span className="ml-auto text-[10px] text-text-muted/60">{en}</span>
          </NavLink>
        ))}
      </nav>
      <NavLink to="/"
        className="flex items-center gap-2 px-4 py-3 text-[11px] text-text-muted
                   border-t border-bg-border hover:text-text-secondary">
        <ArrowLeft className="w-3.5 h-3.5" /> 回到 V1
      </NavLink>
    </aside>
  )
}
