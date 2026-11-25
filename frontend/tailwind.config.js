/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: {
          ground: '#F8FAFC',     // Clean global canvas (Slate-50)
          subtle: '#F1F5F9',     // Secondary navigation / subtle panels (Slate-100)
          card: '#FFFFFF',       // Pure white cards & containers
          elevated: '#FFFFFF',   // Modals, dropdowns, popovers
          hover: '#F8FAFC',      // Hover states
          active: '#EFF6FF',     // Active selection (Blue-50)
        },
        border: {
          subtle: '#E2E8F0',     // Subtle dividers (Slate-200)
          muted: '#CBD5E1',      // Input & card borders (Slate-300)
          hover: '#94A3B8',      // Border hover
        },
        brand: {
          50: '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',        // Enterprise primary blue
          700: '#1d4ed8',
          800: '#1e40af',
          900: '#1e3a8a',
        },
      },
      fontFamily: {
        sans: [
          'Inter',
          '-apple-system',
          'BlinkMacSystemFont',
          'Segoe UI',
          'PingFang SC',
          'Hiragino Sans GB',
          'Microsoft YaHei',
          'sans-serif',
        ],
        mono: ['JetBrains Mono', 'Fira Code', 'Consolas', 'monospace'],
      },
      boxShadow: {
        'enterprise': '0 1px 3px 0 rgba(15, 23, 42, 0.06), 0 1px 2px -1px rgba(15, 23, 42, 0.04)',
        'enterprise-md': '0 4px 6px -1px rgba(15, 23, 42, 0.08), 0 2px 4px -2px rgba(15, 23, 42, 0.04)',
        'enterprise-lg': '0 10px 15px -3px rgba(15, 23, 42, 0.08), 0 4px 6px -4px rgba(15, 23, 42, 0.04)',
        'glow-brand': '0 0 15px -3px rgba(37, 99, 235, 0.25)',
      },
    },
  },
  plugins: [],
}
