/** @type {import('tailwindcss').Config} */
export default {
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        background: '#0A0A0F',
        'background-elev': '#13131A',
        foreground: '#EDEDF2',
        muted: '#5A5A66',
        'muted-foreground': '#8A8A99',
        border: 'rgba(255,255,255,0.10)',
        'border-hover': 'rgba(255,255,255,0.18)',
        accent: '#E8A658',
        'accent-hover': '#F2B970',
        hot: '#FF5C4D',
        ok: '#9BD66B',
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Manrope', '"PingFang SC"', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
    },
  },
  plugins: [],
}
