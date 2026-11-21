import { useEffect, useState } from 'react'
import { CalendarClock, Droplet, LayoutDashboard, Map, SlidersHorizontal, Gamepad2, X, AlertTriangle } from 'lucide-react'
import StatusBar from './components/StatusBar'
import { EmergencyButton, PilotPanel } from './components/Pilot'
import { Segmented } from './components/ui'
import { useLive } from './lib/live'
import type { Site } from './lib/types'
import Overview from './pages/Overview'
import Farm from './pages/Farm'
import PlanPage from './pages/PlanPage'
import Leaks from './pages/Leaks'
import WhatIf from './pages/WhatIf'

const PAGES = [
  { id: 'apercu', label: "Vue d'ensemble", icon: LayoutDashboard, el: <Overview /> },
  { id: 'ferme', label: 'Ferme', icon: Map, el: <Farm /> },
  { id: 'plan', label: 'Plan IA', icon: CalendarClock, el: <PlanPage /> },
  { id: 'fuites', label: 'Fuites', icon: AlertTriangle, el: <Leaks /> },
  { id: 'etsi', label: 'Et si ?', icon: SlidersHorizontal, el: <WhatIf /> },
] as const

function usePage() {
  const read = () => (window.location.hash.slice(1) || 'apercu')
  const [page, setPage] = useState(read)
  useEffect(() => {
    const h = () => setPage(read())
    window.addEventListener('hashchange', h)
    return () => window.removeEventListener('hashchange', h)
  }, [])
  return [page, (p: string) => { window.location.hash = p }] as const
}

export default function App() {
  const [page, go] = usePage()
  const [pilotOpen, setPilotOpen] = useState(false)
  const live = useLive()
  const current = PAGES.find((p) => p.id === page) ?? PAGES[0]
  const activeAlerts = live.alerts.filter((a) => a.active).length

  return (
    <div className="min-h-screen overflow-x-hidden bg-ink-50 text-ink-900">
      <div className="sticky top-0 z-[1000]">
        <header className="border-b border-ink-200/70 bg-white">
          <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-2.5">
            <div className="flex items-center gap-2">
              <div className="rounded-xl bg-gradient-to-br from-water-500 to-leaf-500 p-1.5 text-white"><Droplet size={18} /></div>
              <div className="leading-tight">
                <div className="font-semibold">AquaMind</div>
                <div className="hidden text-[11px] text-ink-500 sm:block">eau · énergie · agriculture</div>
              </div>
            </div>
            <nav className="ml-4 hidden items-center gap-1 lg:flex">
              {PAGES.map((p) => (
                <button key={p.id} onClick={() => go(p.id)}
                  className={`flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium transition ${page === p.id ? 'bg-water-50 text-water-700' : 'text-ink-500 hover:bg-ink-100 hover:text-ink-900'}`}>
                  <p.icon size={16} />{p.label}
                  {p.id === 'fuites' && activeAlerts > 0 && <span className="ml-0.5 rounded-full bg-red-600 px-1.5 text-[10px] text-white">{activeAlerts}</span>}
                </button>
              ))}
            </nav>
            <div className="ml-auto flex items-center gap-2">
              <div className="hidden sm:block">
                <Segmented<Site> value={live.site} onChange={live.setSite} options={[{ value: 'tunisia', label: 'Tunisie' }, { value: 'japan', label: 'Japon' }]} />
              </div>
              <button onClick={() => setPilotOpen(true)}
                className="flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm font-medium text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50">
                <Gamepad2 size={16} /><span className="hidden sm:inline">Pilotage</span>
                {live.actuators?.pump_on && <span className="h-2 w-2 animate-pulse rounded-full bg-leaf-500" />}
              </button>
              <EmergencyButton />
            </div>
          </div>
          <nav className="flex gap-1 overflow-x-auto border-t border-ink-100 px-2 py-1 lg:hidden">
            {PAGES.map((p) => (
              <button key={p.id} onClick={() => go(p.id)}
                className={`flex shrink-0 items-center gap-1 rounded-md px-2.5 py-1.5 text-xs font-medium ${page === p.id ? 'bg-water-50 text-water-700' : 'text-ink-500'}`}>
                <p.icon size={14} />{p.label}
              </button>
            ))}
            <div className="ml-auto shrink-0 sm:hidden">
              <Segmented<Site> value={live.site} onChange={live.setSite} options={[{ value: 'tunisia', label: 'TN' }, { value: 'japan', label: 'JP' }]} />
            </div>
          </nav>
        </header>
        <StatusBar />
      </div>

      <main className="mx-auto max-w-7xl px-4 py-5">
        <h1 className="mb-4 text-lg font-semibold text-ink-900">{current.label}</h1>
        {current.el}
        <footer className="mt-10 border-t border-ink-200 pt-4 text-xs text-ink-500">
          AquaMind · KIZUNA 2026. Données météo Open-Meteo, PVGIS, SoilGrids ; capteurs et fuites simulés.
          Valeurs de récolte et tarifs : hypothèses — tous les impacts affichés sont des <b>estimations simulées</b>.
        </footer>
      </main>

      {pilotOpen && (
        <div className="fixed inset-0 z-[1500] flex justify-end bg-ink-900/30" onClick={() => setPilotOpen(false)}>
          <aside className="h-full w-full max-w-md overflow-y-auto bg-ink-50 p-4 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center justify-between">
              <h2 className="font-semibold">Pilotage des actionneurs</h2>
              <button onClick={() => setPilotOpen(false)} className="rounded p-1 hover:bg-ink-200" aria-label="Fermer"><X size={18} /></button>
            </div>
            <PilotPanel />
          </aside>
        </div>
      )}
    </div>
  )
}
