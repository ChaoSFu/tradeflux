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
}

export const useAppStore = create<AppState>((set) => ({
  sidebarCollapsed: false,
  setSidebarCollapsed: (v) => set({ sidebarCollapsed: v }),
  toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

  marketState: null,
  setMarketState: (marketState) => set({ marketState }),

  selectedSectorCode: null,
  setSelectedSectorCode: (selectedSectorCode) => set({ selectedSectorCode }),
}))
