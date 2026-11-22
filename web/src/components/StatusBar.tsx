// Bandeau d'état simple : le serveur répond-il, la ferme envoie-t-elle ses mesures, qui arrose ?
import { CloudOff } from 'lucide-react'
import { readingAgeSeconds, useLive } from '../lib/live'
import { InfoTip, fr } from './ui'

function Dot({ ok }: { ok: boolean }) {
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${ok ? 'bg-leaf-500' : 'bg-red-500'}`} />
}

function ago(s: number | null) {
  if (s === null) return null
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.floor(s / 60)} min`
  return `${fr(s / 3600, 1)} h`
}

export default function StatusBar() {
  const live = useLive()
  const h = live.health
  const age = readingAgeSeconds(live)
  const farmOk = live.apiOnline && !!h?.mqtt && age !== null && age <= 30
  const manual = h?.plan?.mode === 'manual'
  const lastOk = live.lastApiOkAt ? new Date(live.lastApiOkAt).toLocaleTimeString('fr-FR') : null

  return (
    <>
      {!live.apiOnline && (
        <div className="flex items-center justify-center gap-2 bg-red-600 px-4 py-2 text-sm font-medium text-white">
          <CloudOff size={16} /> Pas de connexion au serveur — vous voyez les dernières informations reçues{lastOk ? ` (${lastOk})` : ''}. Reconnexion automatique…
        </div>
      )}
      <div className="border-b border-ink-200/70 bg-white/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-1 px-4 py-1.5 text-[13px] text-ink-700">
          <span className="flex items-center gap-1.5"><Dot ok={live.apiOnline} /> Serveur {live.apiOnline ? 'connecté' : 'injoignable'}
            <InfoTip text="Le programme AquaMind (l'API) qui calcule le plan et surveille les fuites." /></span>
          <span className="flex items-center gap-1.5"><Dot ok={farmOk} />
            {farmOk ? <>Ferme : mesures reçues il y a {ago(age)}</>
              : <b className="text-red-600">Ferme : {age === null ? 'aucune mesure reçue' : `plus de nouvelles depuis ${ago(age)}`}</b>}
            <InfoTip text="Les capteurs de la ferme (ici simulée) envoient une mesure toutes les 10 minutes de la ferme. Si rien n'arrive depuis plus de 30 secondes, le simulateur est arrêté ou figé." /></span>
          <span className="flex items-center gap-1.5">📅 Plan suivi : {manual ? 'situation choisie (démonstration ou « Et si ? »)' : 'météo réelle de demain'}
            <InfoTip text="Le plan d'arrosage que l'IA applique. « Météo réelle » : recalculé chaque heure avec la prévision. « Situation choisie » : un scénario de démonstration ou un plan « Et si ? » a été appliqué ; « Revenir à la vraie météo » sur la page Plan d'arrosage." /></span>
          {!live.wsConnected && live.apiOnline && <span className="text-sun-600">Mise à jour en direct : reconnexion…</span>}
        </div>
      </div>
    </>
  )
}
