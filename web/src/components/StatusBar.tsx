// Bandeau d'état toujours visible : API, MQTT, simulateur (vivant ou arrêté depuis X s), WebSocket, plan.
import { CloudOff, Radio, Server, Activity, Database, CalendarClock } from 'lucide-react'
import { readingAgeSeconds, useLive } from '../lib/live'
import { fr } from './ui'

function Dot({ ok, warn }: { ok: boolean; warn?: boolean }) {
  return <span className={`inline-block h-2 w-2 rounded-full ${ok ? 'bg-leaf-500' : warn ? 'bg-sun-500' : 'bg-red-500'}`} />
}

function ago(s: number | null) {
  if (s === null) return 'jamais'
  if (s < 60) return `${Math.round(s)} s`
  if (s < 3600) return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`
  return `${fr(s / 3600, 1)} h`
}

export default function StatusBar() {
  const live = useLive()
  const h = live.health
  const age = readingAgeSeconds(live)
  const simAlive = age !== null && age <= 30
  const lastOk = live.lastApiOkAt ? new Date(live.lastApiOkAt).toLocaleTimeString('fr-FR') : null
  const item = 'flex items-center gap-1.5 whitespace-nowrap'

  return (
    <>
      {!live.apiOnline && (
        <div className="flex items-center justify-center gap-2 bg-red-600 px-4 py-2 text-sm font-medium text-white">
          <CloudOff size={16} /> API hors-ligne — affichage des dernières données en cache{lastOk ? ` (dernier contact ${lastOk})` : ''}. Reconnexion automatique…
        </div>
      )}
      <div className="border-b border-ink-200/70 bg-white/80 backdrop-blur">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-5 gap-y-1 px-4 py-1.5 text-xs text-ink-700">
          <span className={item}><Server size={13} className="text-ink-400" /><Dot ok={live.apiOnline} /> API {live.apiOnline ? 'en ligne' : 'hors-ligne'}</span>
          <span className={item}><Radio size={13} className="text-ink-400" /><Dot ok={!!h?.mqtt && live.apiOnline} /> MQTT {h?.mqtt ? 'connecté' : 'déconnecté'}</span>
          <span className={item} title="Âge de la dernière mesure reçue">
            <Activity size={13} className="text-ink-400" /><Dot ok={simAlive} />
            Simulateur {simAlive ? `vivant (dernière mesure il y a ${ago(age)})`
              : <b className="text-red-600">{age === null ? 'aucune mesure reçue' : `arrêté depuis ${ago(age)}`}</b>}
          </span>
          <span className={item}><Dot ok={live.wsConnected} warn /> Temps réel {live.wsConnected ? 'actif' : 'reconnexion…'}</span>
          <span className={item}><Database size={13} className="text-ink-400" /> Stockage {h?.storage ?? '—'}</span>
          <span className={item}><CalendarClock size={13} className="text-ink-400" /> Plan {h?.plan?.mode === 'manual' ? 'manuel' : 'automatique'}{h?.plan?.source ? ` · ${h.plan.source}` : ''}</span>
          {h?.status === 'degraded' && live.apiOnline && (
            <span className="font-medium text-sun-600">⚠ {h.reasons?.join(' ; ') || 'mode dégradé'}</span>
          )}
        </div>
      </div>
    </>
  )
}
