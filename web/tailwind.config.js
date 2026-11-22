/** @type {import('tailwindcss').Config} */
export default {
  // relative : chemins résolus depuis CE fichier, quel que soit le dossier d'où Vite est lancé
  content: { relative: true, files: ['./index.html', './src/**/*.{ts,tsx}'] },
  theme: {
    extend: {
      colors: {
        water: { 50: '#eff8ff', 100: '#dbeefe', 200: '#bfe2fe', 400: '#5fb4f8', 500: '#2f93ea', 600: '#1d74cc', 700: '#1b5ea6', 900: '#173f6b' },
        sun: { 50: '#fffbeb', 100: '#fef3c7', 400: '#fbbf24', 500: '#f59e0b', 600: '#d97706' },
        leaf: { 50: '#f0fdf4', 100: '#dcfce7', 400: '#4ade80', 500: '#22a755', 600: '#16803f', 700: '#166534' },
        ink: { 50: '#f7f8fa', 100: '#eef0f4', 200: '#dde1e8', 400: '#94a0b2', 500: '#64708a', 700: '#363f52', 900: '#141a26' },
      },
      fontFamily: { sans: ['Inter', 'system-ui', 'Segoe UI', 'Roboto', 'sans-serif'] },
      boxShadow: { card: '0 1px 2px rgba(20,26,38,.05), 0 1px 8px rgba(20,26,38,.04)' },
    },
  },
  plugins: [],
}
