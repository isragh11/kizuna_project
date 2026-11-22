import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

// Racine explicite (dossier web/) : sous Windows, un terminal en « c:\… » et des fichiers en « C:\… » faisaient
// servir le CSS hors racine (/@fs/…) sans passer par Tailwind → page sans mise en forme.
const root = fileURLToPath(new URL('.', import.meta.url))

// envDir '..' : lit VITE_API_URL / VITE_WS_URL dans le .env de la racine (seules les variables VITE_*
// sont exposées au navigateur, jamais les secrets InfluxDB).
export default defineConfig({
  root,
  plugins: [react()],
  envDir: '..',
  // Tailwind branché ici (et non via la recherche de postcss.config.js) : toujours appliqué
  css: { postcss: { plugins: [tailwindcss({ config: fileURLToPath(new URL('./tailwind.config.js', import.meta.url)) }), autoprefixer()] } },
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  build: {
    chunkSizeWarningLimit: 700, // Recharts seul pèse ~560 kB (158 kB gzip)
    rollupOptions: {
      output: { manualChunks: { charts: ['recharts'], map: ['leaflet', 'react-leaflet'], react: ['react', 'react-dom'] } },
    },
  },
})
