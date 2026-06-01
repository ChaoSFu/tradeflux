import { create } from 'zustand'
import { persist } from 'zustand/middleware'

const TOKEN_KEY = 'tradeflux:token'

interface AuthState {
  token: string | null
  username: string | null
  isLoggedIn: boolean
  login: (token: string, username: string) => void
  logout: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      username: null,
      isLoggedIn: false,
      login: (token, username) => set({ token, username, isLoggedIn: true }),
      logout: () => set({ token: null, username: null, isLoggedIn: false }),
    }),
    { name: TOKEN_KEY },
  ),
)
