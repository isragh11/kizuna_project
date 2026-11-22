// Bandeau d'état simple : le serveur répond-il, la ferme envoie-t-elle ses mesures, qui arrose ?
import { useEffect } from 'react'
import { CloudOff, Cpu } from 'lucide-react'
import { useNotify } from '../lib/actions'
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
  const dev = live.device
  const deviceDown = live.apiOnline && dev?.kind === 'esp32' && !dev.online
  const notify = useNotify()
  // Action de sécurité faite sur le boîtier (bouton rouge, coupure « API muette ») : affichée aussitôt
  const localId = live.localAlert?.id
  useEffect(() => { if (live.localAlert) notify(false, live.localAlert.text) }, [localId])   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <>
      {!live.apiOnline && (
        <div className="flex items-center justify-center gap-2 bg-red-600 px-4 py-2 text-sm font-medium text-white">
          <CloudOff size={16} /> Pas de connexion au serveur — vous voyez les dernières informations reçues{lastOk ? ` (${lastOk})` : ''}. Reconnexion automatique…
        </div>
      )}
      {deviceDown && (
        <div className="flex flex-wrap items-center justify-center gap-x-2 bg-red-600 px-4 py-2 text-center text-sm font-medium text-white" role="alert">
          <Cpu size={16} /> Appareil hors-ligne : {dev!.label} ne donne plus signe de vie
          {dev!.heartbeat_age_s !== null ? ` depuis ${ago(dev!.heartbeat_age_s)}` : ''}.
          <span className="font-normal">Les commandes ne peuvent pas être confirmées. Par sécurité, le boîtier coupe la pompe tout seul s'il perd le serveur 30 s.</span>
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
          {dev && (
            <span className="flex items-center gap-1.5"><Dot ok={dev.online} />
              Appareil : <b className="font-semibold">{dev.label}</b>
              {dev.kind === 'esp32' && dev.online && dev.heartbeat_age_s !== null && <span className="text-ink-400">· signe de vie il y a {ago(dev.heartbeat_age_s)}</span>}
              <InfoTip wide text="Qui pilote vraiment la pompe et les robinets : le boîtier ESP32 (simulé dans Wokwi) ou le simulateur Python. Chaque commande est confirmée par l'appareil ; s'il ne donne plus de battement de cœur pendant 15 s, il est déclaré hors-ligne." /></span>
          )}
          <span className="flex items-center gap-1.5">📅 Plan suivi : {manual ? 'situation choisie (démonstration ou « Et si ? »)' : 'météo réelle de demain'}
            <InfoTip text="Le plan d'arrosage que l'IA applique. « Météo réelle » : recalculé chaque heure avec la prévision. « Situation choisie » : un scénario de démonstration ou un plan « Et si ? » a été appliqué ; « Revenir à la vraie météo » sur la page Plan d'arrosage." /></span>
          {!live.wsConnected && live.apiOnline && <span className="text-sun-600">Mise à jour en direct : reconnexion…</span>}
        </div>
      </div>
    </>
  )
}
