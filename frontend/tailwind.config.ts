import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: 'class',
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './lib/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        // Yamamoto palette — deep indigo, crisp white, verdant pitch green
        pitch: {
          black: '#0A0A0F',
          indigo: {
            deep: '#0F0F2E',
            mid: '#1A1A4E',
            soft: '#2D2D7A',
            glow: '#4F4FBA',
          },
          green: {
            DEFAULT: '#1A6B3A',
            light: '#2ECC71',
            neon: '#39FF14',
            field: '#2D5A27',
          },
          white: '#F8F9FA',
          muted: '#6B7280',
          accent: '#8B5CF6',
        },
        xg: {
          low: '#3B82F6',
          mid: '#F59E0B',
          high: '#EF4444',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'float': 'float 6s ease-in-out infinite',
        'scan': 'scan 2s linear infinite',
        'fade-in': 'fadeIn 0.5s ease-in-out',
        'slide-up': 'slideUp 0.4s cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        float: {
          '0%, 100%': { transform: 'translateY(0px)' },
          '50%': { transform: 'translateY(-10px)' },
        },
        scan: {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(100%)' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { opacity: '0', transform: 'translateY(20px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
      },
      backgroundImage: {
        'pitch-gradient': 'linear-gradient(135deg, #0A0A0F 0%, #0F0F2E 50%, #1A1A4E 100%)',
        'field-gradient': 'linear-gradient(180deg, #2D5A27 0%, #1A6B3A 100%)',
        'hero-mesh': 'radial-gradient(at 40% 20%, #1A1A4E 0px, transparent 50%), radial-gradient(at 80% 0%, #2D2D7A 0px, transparent 50%), radial-gradient(at 0% 50%, #1A6B3A 0px, transparent 50%)',
      },
      boxShadow: {
        'pitch': '0 0 40px rgba(79, 79, 186, 0.15)',
        'glow': '0 0 20px rgba(46, 204, 113, 0.3)',
        'card': '0 4px 6px -1px rgba(0, 0, 0, 0.5), 0 2px 4px -2px rgba(0, 0, 0, 0.3)',
      },
    },
  },
  plugins: [],
};

export default config;
