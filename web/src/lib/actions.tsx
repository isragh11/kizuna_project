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

  /** Envoie une commande. Pour les actionneurs (`track`), affiche « envoyée… » puis la réponse de
   *  l'APPAREIL : confirmée ✅ (avec le délai), refusée ❌ (sa raison) ou sans réponse ⚠️. */
  const send = useCallback(async (path: string, body: unknown, okText: (d: any) => string, track = false) => {
    const t0 = performance.now()
    try {
      const r = await apiSend(path, body)
      if (r.ok) {
        const text = okText(r.data)
        const steps = r.data?.steps as any[] | undefined
        const cid: string | undefined = (steps?.length ? steps[steps.length - 1] : r.data)?.command?.command_id
        if (track && cid) {
          const who = live.device?.label ?? 'la ferme'
          notify(true, `⏳ ${text} — envoyé, en attente de confirmation (${who})…`)
          live.watchCommand(cid, (c) => {
            const total = ((performance.now() - t0) / 1000).toLocaleString('fr-FR', { maximumFractionDigits: 2 })
            if (c.result === 'ok') notify(true, `✅ Confirmé par ${who} en ${total} s : ${text}`)
            else if (c.result === 'refused') notify(false, `❌ Refusé par ${who} : ${reasonText(c.reason)}`)
            else notify(false, `⚠️ ${who} n'a pas confirmé en 5 s : ${text} (état non confirmé)`)
          })
        } else notify(true, text)
      }
      else if (r.status === 409) notify(false, `❌ Refusé pour votre sécurité : ${reasonText(errorText(r.data, 'raison inconnue'))}`)
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
      send('/actuators/pump', { ...origin(), on, duration_min }, (d) => (on ? `Pompe démarrée : ${reasonText(d.reason)}` : 'Pompe arrêtée'), true),
    valve: (id: PlotId, open: boolean) =>
      send(`/actuators/valve/${id}`, { ...origin(), open }, () => `Robinet de la parcelle ${id} ${open ? 'ouvert' : 'fermé'}`, true),
    isolate: (id: PlotId, reason: string) =>
      send(`/actuators/isolate/${id}`, { ...origin(), reason }, () => `Parcelle ${id} coupée : pompe arrêtée puis robinet fermé`, true),
    auto: () => send('/actuators/auto', origin(), () => "L'IA reprend l'arrosage automatique", true),
    estop: (engaged: boolean, reason = '') =>
      send('/emergency-stop', { ...origin(), engaged, reason }, () => (engaged ? "ARRÊT D'URGENCE : tout est coupé" : "Arrêt d'urgence relâché. Pensez à rendre la main à l'IA."), true),
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
