// Commandes envoyées à l'API depuis le dashboard (source = "dashboard"), avec retour lisible.
import { createContext, useCallback, useContext, useState, type ReactNode } from 'react'
import { Toast } from '../components/ui'
import { apiSend, errorText } from './api'
import { useLive } from './live'
import { reasonText } from './labels'
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
      else if (r.status === 409) notify(false, `Refusé pour votre sécurité : ${reasonText(errorText(r.data, 'raison inconnue'))}`)
      else if (r.status === 503) notify(false, 'Commande acceptée, mais la ferme ne répond pas (liaison coupée).')
      else notify(false, `Erreur ${r.status} : ${errorText(r.data, 'requête refusée')}`)
      live.refreshActuators()
      return r
    } catch {
      notify(false, 'Serveur injoignable : commande non envoyée')
      return null
    }
  }, [notify, live])

  return {
    pump: (on: boolean, duration_min?: number) =>
      send('/actuators/pump', { ...origin(), on, duration_min }, (d) => (on ? `Pompe démarrée : ${reasonText(d.reason)}` : 'Pompe arrêtée')),
    valve: (id: PlotId, open: boolean) =>
      send(`/actuators/valve/${id}`, { ...origin(), open }, () => `Robinet de la parcelle ${id} ${open ? 'ouvert' : 'fermé'}`),
    isolate: (id: PlotId, reason: string) =>
      send(`/actuators/isolate/${id}`, { ...origin(), reason }, () => `Parcelle ${id} coupée : pompe arrêtée puis robinet fermé`),
    auto: () => send('/actuators/auto', origin(), () => "L'IA reprend l'arrosage automatique"),
    estop: (engaged: boolean, reason = '') =>
      send('/emergency-stop', { ...origin(), engaged, reason }, () => (engaged ? "ARRÊT D'URGENCE : tout est coupé" : "Arrêt d'urgence relâché. Pensez à rendre la main à l'IA.")),
    leak: (_section: string, rate_m3h: number) =>
      send('/sim/leak', { section: _section, rate_m3h }, () => `Fuite créée pour la démonstration (${Math.round(rate_m3h * 1000 / 60)} litres/minute)`),
    repair: () => send('/sim/reset', undefined, () => 'Fuites réparées et nettoyage arrêté (démonstration)'),
    flush: (rate_m3h: number) =>
      send('/sim/flush', { rate_m3h }, () => (rate_m3h > 0 ? 'Nettoyage des filtres démarré' : 'Nettoyage des filtres arrêté')),
    newNormal: () => send('/sim/new_normal', undefined, () => "C'est noté : la situation actuelle est la nouvelle référence"),
    refreshPlan: () => send('/optimize/refresh', undefined, () => 'Plan recalculé avec la vraie prévision météo'),
    clearAlerts: () => send('/alerts/clear', undefined, () => "Historique des alertes effacé (l'alerte en cours est gardée)"),
  }
}
