/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // Terminal dark palette
        bg: {
          base:     '#080A0E',
          card:     '#101420',   // was #0F1117  — 卡片更清晰
          elevated: '#181D2A',   // was #161A23  — 层次感更强
          hover:    '#1E2436',   // was #1C2130
          border:   '#262D40',   // was #1E2538  — 边框更可见
        },
        text: {
          primary:   '#EDF0F5',  // was #E8EAED  — 稍亮
          secondary: '#A2A9C4',  // was #8A90A8  — +15% 亮度
          muted:     '#737A96',  // was #505570  — +40% 亮度，/50 opacity 仍可读
          accent:    '#5EA6FF',  // was #4F9CF9  — 稍亮
        },
        // Semantic — A-share convention: red = up (涨), green = down (跌)
        up: {
          DEFAULT: '#FF4560',
          dim: '#4D1A24',
          bright: '#FF6B7A',
        },
        down: {
          DEFAULT: '#26C281',
          dim: '#1A4D35',
          bright: '#00E5A0',
        },
        warn: {
          DEFAULT: '#F59E0B',
          dim: '#3D2A06',
        },
        dragon: {
          DEFAULT: '#FFD700',
          dim: '#4D3F00',
        },
        accent: {
          DEFAULT: '#5EA6FF',   // was #4F9CF9 — 保持与 text.accent 一致
          dim: '#162840',
        },
        // Phase colors
        phase: {
          0: '#737A96',  // Stealth   — 跟随 text-muted 更新
          1: '#5EA6FF',  // Initiation
          2: '#FF4560',  // Expansion
          3: '#FFD700',  // Euphoria
          4: '#F59E0B',  // Divergence
          5: '#26C281',  // Decline
          6: '#7A2B3B',  // Dead Zone — 稍亮
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in': 'fadeIn 0.2s ease-in-out',
        'fade-in-up': 'fadeInUp 0.5s ease-out backwards',
        'glow-drift': 'glowDrift 9s ease-in-out infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        fadeInUp: {
          '0%': { opacity: '0', transform: 'translateY(14px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        glowDrift: {
          '0%, 100%': { transform: 'translate(0, 0) scale(1)', opacity: '0.55' },
          '50%': { transform: 'translate(60px, 14px) scale(1.15)', opacity: '0.85' },
        },
      },
    },
  },
  plugins: [],
}
