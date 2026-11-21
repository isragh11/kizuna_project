// Commandes envoyées à l'API depuis le dashboard (source = "dashboard"), avec retour lisible.
import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { Toast } from '../components/ui'
import { apiSend, errorText } from './api'
import { useLive } from './live'
import type { PlotId } from './types'

type Notify = (ok: boolean, text: string) => void
const ToastCtx = createContext<Notify>(() => {})

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ ok: boolean; text: string } | null>(null)
  const notify = useCallback<Notify>((ok, text) => setToast({ ok, text }), [])
  return (
    <ToastCtx.Provider value={notify}>
      {children}
      <Toast toast={toast} onClose={() => setToast(null)} />
    </ToastCtx.Provider>
  )
}

export const useNotify = () => useContext(ToastCtx)

export function operatorName(): string {
  return localStorage.getItem('aquamind:operator') || 'opérateur'
}

export function useActions() {
  const notify = useNotify()
  const live = useLive()
  const origin = () => ({ source: 'dashboard', actor: operatorName() })

  const send = useCallback(async (path: string, body: unknown, okText: (d: any) => string) => {
    try {
      const r = await apiSend(path, body)
      if (r.ok) notify(true, okText(r.data))
      else if (r.status === 409) notify(false, `Refusé par un garde-fou : ${errorText(r.data, 'raison inconnue')}`)
      else if (r.status === 503) notify(false, `Non transmis : ${errorText(r.data, 'broker MQTT indisponible')}`)
      else notify(false, `Erreur ${r.status} : ${errorText(r.data, 'requête refusée')}`)
      live.refreshActuators()
      return r
    } catch {
      notify(false, 'API injoignable : commande non envoyée')
      return null
    }
  }, [notify, live])

  return {
    pump: (on: boolean, duration_min?: number) =>
      send('/actuators/pump', { ...origin(), on, duration_min }, (d) => (on ? `Pompe en marche : ${d.reason}` : 'Pompe arrêtée')),
    valve: (id: PlotId, open: boolean) =>
      send(`/actuators/valve/${id}`, { ...origin(), open }, () => `Vanne ${id} ${open ? 'ouverte' : 'fermée'}`),
    isolate: (id: PlotId, reason: string) =>
      send(`/actuators/isolate/${id}`, { ...origin(), reason }, () => `Parcelle ${id} isolée : pompe arrêtée puis vanne fermée`),
    auto: () => send('/actuators/auto', origin(), () => 'Retour au pilotage automatique (plan de l\'optimiseur)'),
    estop: (engaged: boolean, reason = '') =>
      send('/emergency-stop', { ...origin(), engaged, reason }, () => (engaged ? 'ARRÊT D\'URGENCE engagé : tout est coupé' : 'Arrêt d\'urgence relâché')),
    leak: (section: string, rate_m3h: number) =>
      send('/sim/leak', { section, rate_m3h }, () => `Fuite injectée sur ${section} (${rate_m3h} m³/h)`),
    repair: () => send('/sim/reset', undefined, () => 'Réparation : fuites et rinçage arrêtés'),
    flush: (rate_m3h: number) =>
      send('/sim/flush', { rate_m3h }, () => (rate_m3h > 0 ? `Rinçage des filtres : ${rate_m3h} m³/h` : 'Rinçage arrêté')),
    newNormal: () => send('/sim/new_normal', undefined, () => 'Nouveau normal : la situation actuelle devient la référence'),
    refreshPlan: () => send('/optimize/refresh', undefined, () => 'Plan automatique recalculé avec la prévision'),
  }
}
