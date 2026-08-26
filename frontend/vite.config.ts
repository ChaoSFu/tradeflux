import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // 默认打本地后端；调 UI 时本地库常常没有真实数据（生产库跟本地不是一回事），
        // 这时用 VITE_API_PROXY 指到线上，界面在本地跑、数据用真的：
        //   VITE_API_PROXY=http://47.250.165.189 npm run dev
        target: process.env.VITE_API_PROXY || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
