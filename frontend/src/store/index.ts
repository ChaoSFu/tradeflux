import { create } from 'zustand'
import type { MarketState } from '@/types'

interface AppState {
  sidebarCollapsed: boolean
  setSidebarCollapsed: (v: boolean) => void
  toggleSidebar: () => void

  marketState: MarketState | null
  setMarketState: (s: MarketState) => void

  selectedSectorCode: string | null
  setSelectedSectorCode: (code: string | null) => void

  // 每日复盘（盘后研究）打开时注册：顶部状态条点开的板块按名字加进复盘的主线板块列表，返回提示语
  recapAddSector: ((name: string) => string) | null
  setRecapAddSector: (fn: ((name: string) => string) | null) => void
}

export const useAppStore = create<AppState>((set) => ({
  sidebarCollapsed: false,
  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

  marketState: null,
  setMarketState: (marketState) => set({ marketState }),

  selectedSectorCode: null,
  setSelectedSectorCode: (selectedSectorCode) => set({ selectedSectorCode }),

  recapAddSector: null,
  setRecapAddSector: (recapAddSector) => set({ recapAddSector }),
}))
