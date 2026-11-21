import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// envDir '..' : lit VITE_API_URL / VITE_WS_URL dans le .env de la racine (seules les variables VITE_*
// sont exposées au navigateur, jamais les secrets InfluxDB).
export default defineConfig({
  plugins: [react()],
  envDir: '..',
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  build: {
    chunkSizeWarningLimit: 700, // Recharts seul pèse ~560 kB (158 kB gzip)
    rollupOptions: {
      output: { manualChunks: { charts: ['recharts'], map: ['leaflet', 'react-leaflet'], react: ['react', 'react-dom'] } },
    },
  },
})
