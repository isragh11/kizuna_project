// Pilotage : arrêt d'urgence (toujours visible), état pompe/vannes en direct, commandes manuelles,
// isolement d'une parcelle, journal des commandes. Toute commande passe par les garde-fous de l'API.
import { useEffect, useState } from 'react'
import { OctagonAlert, Power, ShieldCheck, Lock, RotateCcw, ScrollText, Droplets } from 'lucide-react'
import { apiGet } from '../lib/api'
import { operatorName, useActions } from '../lib/actions'
import { useLive } from '../lib/live'
import { PLOTS, SECTION_OF, type JournalEntry, type PlotId } from '../lib/types'
import { Badge, Button, Card, ConfirmDialog, fr } from './ui'

export function EmergencyButton() {
  const { actuators } = useLive()
  const act = useActions()
  const [confirmRelease, setConfirmRelease] = useState(false)
  if (actuators?.estop) {
    return (
      <>
        <button onClick={() => setConfirmRelease(true)}
          className="flex animate-pulse items-center gap-2 rounded-lg bg-red-700 px-3 py-2 text-xs font-bold uppercase tracking-wide text-white ring-2 ring-red-300 sm:text-sm">
          <OctagonAlert size={18} /><span className="hidden sm:inline">Arrêt d'urgence actif — relâcher</span><span className="sm:hidden">Relâcher</span>
        </button>
        <ConfirmDialog open={confirmRelease} onClose={() => setConfirmRelease(false)} title="Relâcher l'arrêt d'urgence"
          message={<>Seul un humain peut relâcher l'arrêt d'urgence. Les actionneurs restent arrêtés ; reprenez ensuite la main
            ou revenez au plan automatique.</>}
          requireReason="Pourquoi est-ce sûr de relâcher ? (consigné dans le journal)" confirmLabel="Relâcher" variant="primary"
          onConfirm={(reason) => act.estop(false, reason)} />
      </>
    )
  }
  return (
    <button onClick={() => act.estop(true, 'bouton du dashboard')} title="Coupe immédiatement la pompe et ferme toutes les vannes"
      className="flex items-center gap-2 rounded-lg bg-red-600 px-3 py-2 text-xs font-bold uppercase tracking-wide text-white shadow-sm hover:bg-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 sm:text-sm">
      <OctagonAlert size={18} /><span className="hidden sm:inline">Arrêt d'urgence</span><span className="sm:hidden">Stop</span>
    </button>
  )
}

function Journal() {
  const [log, setLog] = useState<JournalEntry[]>([])
  const { events } = useLive()
  useEffect(() => {
    apiGet<JournalEntry[]>('/actuators/log?limit=30').then((r) => setLog(r.data)).catch(() => {})
  }, [events.length])
  if (!log.length) return <p className="text-xs text-ink-500">Aucune commande pour l'instant.</p>
  return (
    <ol className="max-h-72 space-y-1.5 overflow-y-auto pr-1 text-xs">
      {log.map((e) => (
        <li key={e.command.command_id + e.decided_at} className={`rounded-lg border px-2.5 py-1.5 ${e.accepted ? 'border-ink-100 bg-ink-50' : 'border-red-100 bg-red-50'}`}>
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium text-ink-900">
              {e.command.target}.{e.command.action}{e.command.valve_id ? ` ${e.command.valve_id}` : ''}
            </span>
            <Badge tone={e.accepted ? 'ok' : 'danger'}>{e.accepted ? 'acceptée' : 'refusée'}</Badge>
          </div>
          <div className="text-ink-500">
            {new Date(e.decided_at).toLocaleTimeString('fr-FR')} · {e.command.source}{e.command.actor ? ` / ${e.command.actor}` : ''}
          </div>
          <div className="text-ink-700">{e.reason}</div>
        </li>
      ))}
    </ol>
  )
}

export function PilotPanel() {
  const { actuators: a, latest } = useLive()
  const act = useActions()
  const [duration, setDuration] = useState(30)
  const [isolate, setIsolate] = useState<PlotId | null>(null)
  const [name, setName] = useState(operatorName())
  const tank = a?.tank_m3 ?? latest.tank
  const manual = a?.mode === 'manual'

  return (
    <div className="space-y-4">
      <Card title="Pilotage" icon={<Power size={16} className="text-water-600" />}
        actions={<Badge tone={a?.estop ? 'danger' : manual ? 'warn' : 'ok'}>{a?.estop ? 'arrêt d\'urgence' : manual ? 'manuel' : 'automatique (plan)'}</Badge>}>
        {!a ? <p className="text-sm text-ink-500">État des actionneurs inconnu (API ou appareil injoignable).</p> : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-xl bg-ink-50 p-3">
                <div className="text-xs text-ink-500">Pompe principale</div>
                <div className={`mt-1 flex items-center gap-2 font-semibold ${a.pump_on ? 'text-leaf-600' : 'text-ink-700'}`}>
                  <span className={`h-2.5 w-2.5 rounded-full ${a.pump_on ? 'animate-pulse bg-leaf-500' : 'bg-ink-400'}`} />
                  {a.pump_on ? 'En marche' : 'Arrêtée'}
                </div>
                {a.pump_remaining_s !== null && a.pump_on && <div className="text-xs text-ink-500">arrêt auto dans {Math.ceil(a.pump_remaining_s / 60)} min</div>}
              </div>
              <div className="rounded-xl bg-ink-50 p-3">
                <div className="text-xs text-ink-500">Cuve</div>
                <div className="mt-1 font-semibold text-water-700">{fr(tank ?? null, 1)} m³</div>
                <div className="text-xs text-ink-500">appareil : {a.device}</div>
              </div>
            </div>

            <div>
              <div className="mb-2 text-xs font-medium text-ink-500">Vannes des parcelles</div>
              <div className="grid grid-cols-3 gap-2">
                {PLOTS.map((id) => {
                  const open = a.valves?.[id]
                  return (
                    <div key={id} className={`rounded-xl border p-2 text-center ${open ? 'border-water-200 bg-water-50' : 'border-ink-200 bg-white'}`}>
                      <div className="text-xs text-ink-500">Parcelle {id} · {SECTION_OF[id]}</div>
                      <div className={`text-sm font-semibold ${open ? 'text-water-700' : 'text-ink-700'}`}>{open ? 'ouverte' : 'fermée'}</div>
                      <Button size="sm" className="mt-1 w-full" onClick={() => act.valve(id, !open)}>{open ? 'Fermer' : 'Ouvrir'}</Button>
                      <Button size="sm" variant="ghost" className="mt-1 w-full text-red-600" onClick={() => setIsolate(id)}>
                        <Lock size={12} /> Isoler
                      </Button>
                    </div>
                  )
                })}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <select className="rounded-lg border border-ink-200 bg-white px-2 py-2 text-sm" value={duration} onChange={(e) => setDuration(Number(e.target.value))}
                aria-label="Durée de marche">
                {[10, 30, 60, 120].map((m) => <option key={m} value={m}>{m} min</option>)}
              </select>
              <Button variant="success" onClick={() => act.pump(true, duration)}><Droplets size={14} /> Marche</Button>
              <Button onClick={() => act.pump(false)}>Arrêt pompe</Button>
              <Button variant="ghost" onClick={() => act.auto()}><RotateCcw size={14} /> Retour au plan</Button>
            </div>
            <p className="flex items-start gap-1.5 text-xs text-ink-500">
              <ShieldCheck size={14} className="mt-0.5 shrink-0 text-leaf-600" />
              Garde-fous côté serveur : durée ≤ {a.limits?.max_pump_minutes ?? 120} min, pas de pompage cuve vide ou niveau inconnu,
              pas de pompe sans vanne ouverte, arrêt d'urgence prioritaire. Les mêmes règles s'appliquent à un agent IA ou à un ESP32.
            </p>
            <label className="flex items-center gap-2 text-xs text-ink-500">
              Opérateur
              <input className="w-36 rounded border border-ink-200 px-2 py-1 text-ink-900" value={name}
                onChange={(e) => { setName(e.target.value); localStorage.setItem('aquamind:operator', e.target.value) }} />
            </label>
          </div>
        )}
      </Card>
      <Card title="Journal des commandes" icon={<ScrollText size={16} className="text-ink-500" />}><Journal /></Card>
      <ConfirmDialog open={isolate !== null} onClose={() => setIsolate(null)} title={`Isoler la parcelle ${isolate}`}
        message={<>Séquence atomique : <b>1)</b> arrêt de la pompe, <b>2)</b> fermeture de la vanne {isolate} ({isolate && SECTION_OF[isolate]}),
          <b> 3)</b> alerte. Toutes les parcelles seront à l'arrêt jusqu'à une nouvelle commande.</>}
        requireReason="Motif (ex. fuite visible sur S3)" confirmLabel="Isoler" variant="danger"
        onConfirm={(reason) => isolate && act.isolate(isolate, reason)} />
    </div>
  )
}
