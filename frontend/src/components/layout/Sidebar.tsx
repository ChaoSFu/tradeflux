import { NavLink, useLocation } from 'react-router-dom'
import { useState, useEffect } from 'react'
import { cn } from '@/utils/cn'
import { useAppStore } from '@/store'
import {
  LayoutDashboard,
  TrendingUp,
  BarChart2,
  Zap,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  Activity,
  Flame,
  ShieldAlert,
} from 'lucide-react'

// ─── Nav structure ────────────────────────────────────────────────────────────

type NavLinkItem  = { type: 'link';  to: string; label: string; icon: React.ElementType }
type NavGroupItem = { type: 'group'; label: string; icon: React.ElementType; children: NavLinkItem[] }
type NavEntry     = NavLinkItem | NavGroupItem

const NAV: NavEntry[] = [
  {
    type: 'group',
    label: '活跃股分析',
    icon: Activity,
    children: [
      { type: 'link', to: '/',            label: '强势股概览', icon: LayoutDashboard },
      { type: 'link', to: '/limit-moves', label: '涨跌停概览', icon: Flame },
      { type: 'link', to: '/stocks',      label: '活跃股池',   icon: TrendingUp },
      { type: 'link', to: '/watchlist',   label: '重点监控',   icon: ShieldAlert },
    ],
  },
  {
    type: 'group',
    label: '板块分析',
    icon: BarChart2,
    children: [
      { type: 'link', to: '/sector-trend',   label: '趋势板块', icon: Activity  },
      { type: 'link', to: '/sector-emotion', label: '情绪板块', icon: Activity  },
    ],
  },
  { type: 'link', to: '/signals', label: '弱转强信号', icon: Zap },
  { type: 'link', to: '/review',  label: '日复盘',     icon: BookOpen },
]

// All link items flattened (used in collapsed mode)
const FLAT_LINKS: NavLinkItem[] = NAV.flatMap((e) =>
  e.type === 'group' ? e.children : [e],
)

// ─── Component ────────────────────────────────────────────────────────────────

export function Sidebar() {
  const { sidebarCollapsed, toggleSidebar } = useAppStore()
  const location = useLocation()

  // Track which groups are open; default all groups open
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => {
    const open = new Set<string>()
    NAV.forEach((e) => { if (e.type === 'group') open.add(e.label) })
    return open
  })

  // Auto-expand a group when user navigates to one of its children
  useEffect(() => {
    NAV.forEach((e) => {
      if (e.type !== 'group') return
      const childActive = e.children.some((c) => location.pathname === c.to)
      if (childActive) {
        setOpenGroups((prev) => {
          if (prev.has(e.label)) return prev
          const next = new Set(prev)
          next.add(e.label)
          return next
        })
      }
    })
  }, [location.pathname])

  const toggleGroup = (label: string) =>
    setOpenGroups((prev) => {
      const next = new Set(prev)
      next.has(label) ? next.delete(label) : next.add(label)
      return next
    })

  return (
    <aside
      className={cn(
        'flex flex-col h-screen bg-bg-card border-r border-bg-border transition-all duration-200 shrink-0',
        sidebarCollapsed ? 'w-14' : 'w-52',
      )}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-3 h-14 border-b border-bg-border">
        <div className="w-7 h-7 rounded bg-accent/20 border border-accent/30 flex items-center justify-center shrink-0">
          <Activity className="w-4 h-4 text-accent" />
        </div>
        {!sidebarCollapsed && (
          <div className="overflow-hidden">
            <div className="text-sm font-bold text-text-primary leading-none">TradeFlux</div>
            <div className="text-xs text-text-muted mt-0.5">短线晴雨表</div>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 px-2 py-3 overflow-y-auto">
        {sidebarCollapsed ? (
          // ── Collapsed: flat icon list ──────────────────────────────────────
          <div className="space-y-0.5">
            {FLAT_LINKS.map(({ to, label, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                end
                title={label}
                className={({ isActive }) =>
                  cn(
                    'flex items-center justify-center px-2 py-2 rounded transition-colors',
                    isActive
                      ? 'bg-accent-dim text-accent'
                      : 'text-text-secondary hover:bg-bg-elevated hover:text-text-primary',
                  )
                }
              >
                <Icon className="w-4 h-4 shrink-0" />
              </NavLink>
            ))}
          </div>
        ) : (
          // ── Expanded: grouped list ─────────────────────────────────────────
          <div className="space-y-0.5">
            {NAV.map((entry) => {
              if (entry.type === 'link') {
                const { to, label, icon: Icon } = entry
                return (
                  <NavLink
                    key={to}
                    to={to}
                    end
                    className={({ isActive }) =>
                      cn(
                        'flex items-center gap-3 px-2 py-2 rounded text-sm transition-colors',
                        isActive
                          ? 'bg-accent-dim text-accent'
                          : 'text-text-secondary hover:bg-bg-elevated hover:text-text-primary',
                      )
                    }
                  >
                    <Icon className="w-4 h-4 shrink-0" />
                    <span className="truncate">{label}</span>
                  </NavLink>
                )
              }

              // Group entry
              const isOpen = openGroups.has(entry.label)
              const hasActiveChild = entry.children.some((c) =>
                location.pathname === c.to,
              )
              const GroupIcon = entry.icon

              return (
                <div key={entry.label}>
                  {/* Group header */}
                  <button
                    onClick={() => toggleGroup(entry.label)}
                    className={cn(
                      'w-full flex items-center gap-3 px-2 py-2 rounded text-sm transition-colors',
                      hasActiveChild
                        ? 'text-accent'
                        : 'text-text-secondary hover:bg-bg-elevated hover:text-text-primary',
                    )}
                  >
                    <GroupIcon className="w-4 h-4 shrink-0" />
                    <span className="truncate flex-1 text-left font-medium">{entry.label}</span>
                    {isOpen
                      ? <ChevronUp   className="w-3.5 h-3.5 shrink-0 text-text-muted/60" />
                      : <ChevronDown className="w-3.5 h-3.5 shrink-0 text-text-muted/60" />
                    }
                  </button>

                  {/* Children */}
                  {isOpen && (
                    <div className="mt-0.5 mb-1 ml-3 pl-2.5 border-l border-bg-border/50 space-y-0.5">
                      {entry.children.map(({ to, label, icon: ChildIcon }) => (
                        <NavLink
                          key={to}
                          to={to}
                          end
                          className={({ isActive }) =>
                            cn(
                              'flex items-center gap-2.5 px-2 py-1.5 rounded text-sm transition-colors',
                              isActive
                                ? 'bg-accent-dim text-accent'
                                : 'text-text-secondary hover:bg-bg-elevated hover:text-text-primary',
                            )
                          }
                        >
                          <ChildIcon className="w-3.5 h-3.5 shrink-0" />
                          <span className="truncate">{label}</span>
                        </NavLink>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </nav>

      {/* Collapse toggle */}
      <div className="px-2 pb-3">
        <button
          onClick={toggleSidebar}
          className="flex items-center justify-center w-full h-8 rounded text-text-muted hover:bg-bg-elevated hover:text-text-primary transition-colors"
        >
          {sidebarCollapsed ? (
            <ChevronRight className="w-4 h-4" />
          ) : (
            <ChevronLeft className="w-4 h-4" />
          )}
        </button>
      </div>
    </aside>
  )
}
