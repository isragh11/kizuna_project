import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import { ToastProvider } from './lib/actions'
import { LiveProvider } from './lib/live'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <LiveProvider>
      <ToastProvider>
        <App />
      </ToastProvider>
    </LiveProvider>
  </StrictMode>,
)
