// Commandes de la ferme : arrêt d'urgence (toujours visible), pompe, robinets, « couper une parcelle »,
// journal. Mots simples ; toute commande passe par les règles de sécurité du serveur.
import { useEffect, useState } from 'react'
import { OctagonAlert, ScrollText, ShieldCheck, Droplets, Power } from 'lucide-react'
import { apiGet } from '../lib/api'
import { operatorName, useActions } from '../lib/actions'
import { useLive } from '../lib/live'
import { SOURCE_TEXT, commandText, emoji, plotName, reasonText } from '../lib/labels'
import { useCrops } from '../lib/useCrops'
import { PLOTS, type JournalEntry, type PlotId } from '../lib/types'
import { Badge, Bar, Button, Card, ConfirmDialog, Details, InfoTip, fr } from './ui'

const TANK_CAPACITY = 80

export function EmergencyButton() {
  const { actuators } = useLive()
  const act = useActions()
  const [confirmRelease, setConfirmRelease] = useState(false)
  if (actuators?.estop) {
    return (
      <>
        <button onClick={() => setConfirmRelease(true)}
          className="flex shrink-0 animate-pulse items-center gap-2 whitespace-nowrap rounded-lg bg-red-700 px-3 py-2 text-xs font-bold uppercase text-white ring-2 ring-red-300 sm:text-sm">
          <OctagonAlert size={18} /><span className="hidden md:inline">Arrêt d'urgence actif · relâcher</span><span className="md:hidden">Relâcher</span>
        </button>
        <ConfirmDialog open={confirmRelease} onClose={() => setConfirmRelease(false)} title="Relâcher l'arrêt d'urgence"
          message={<>Avant de relâcher, vérifiez que tout est sûr (fuite réparée, personne près de la pompe).
            Ensuite, rien ne redémarre tout seul : cliquez sur <b>« Rendre la main à l'IA »</b> dans les Commandes.</>}
          requireReason="Pourquoi est-ce sûr maintenant ? (gardé dans le journal, ex. « fuite réparée »)" confirmLabel="Relâcher" variant="primary"
          onConfirm={(reason) => act.estop(false, reason)} />
      </>
    )
  }
  return (
    <button onClick={() => act.estop(true, 'bouton du tableau de bord')} title="Coupe immédiatement la pompe et ferme tous les robinets"
      className="flex shrink-0 items-center gap-2 whitespace-nowrap rounded-lg bg-red-600 px-3 py-2 text-xs font-bold uppercase text-white shadow-sm hover:bg-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-300 sm:text-sm">
      <OctagonAlert size={18} /><span className="hidden sm:inline">Arrêt d'urgence</span><span className="sm:hidden">Stop</span>
    </button>
  )
}

function Journal() {
  const [log, setLog] = useState<JournalEntry[]>([])
  const { events } = useLive()
  const crops = useCrops()
  useEffect(() => {
    apiGet<JournalEntry[]>('/actuators/log?limit=20').then((r) => setLog(r.data)).catch(() => {})
  }, [events.length])
  if (!log.length) return <p className="text-sm text-ink-500">Aucune commande pour l'instant.</p>
  return (
    <ol className="max-h-80 space-y-2 overflow-y-auto pr-1">
      {log.map((e) => (
        <li key={e.command.command_id + e.decided_at} className={`rounded-xl border px-3 py-2 text-sm ${e.accepted ? 'border-ink-100 bg-white' : 'border-red-200 bg-red-50'}`}>
          <div className="flex items-center justify-between gap-2">
            <span className="font-medium text-ink-900">{e.accepted ? '✅' : '⛔'} {commandText(e.command.target, e.command.action, e.command.valve_id, crops)}</span>
            <span className="shrink-0 text-xs text-ink-400">{new Date(e.decided_at).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}</span>
          </div>
          <div className="text-xs text-ink-500">{SOURCE_TEXT[e.command.source] ?? e.command.source}{e.command.actor && e.command.actor !== 'opérateur' ? ` · ${e.command.actor}` : ''}</div>
          <div className={`text-xs ${e.accepted ? 'text-ink-700' : 'font-medium text-red-700'}`}>{e.accepted ? '' : 'Refusé : '}{reasonText(e.reason)}</div>
          {e.command.reason && e.command.target === 'estop' && <div className="text-xs italic text-ink-500">Motif : « {e.command.reason} »</div>}
        </li>
      ))}
    </ol>
  )
}

export function PilotPanel() {
  const { actuators: a, latest } = useLive()
  const crops = useCrops()
  const act = useActions()
  const [duration, setDuration] = useState(30)
  const [isolate, setIsolate] = useState<PlotId | null>(null)
  const [name, setName] = useState(operatorName())
  const tank = a?.tank_m3 ?? latest.tank ?? null
  const manual = a?.mode === 'manual'

  return (
    <div className="space-y-4">
      <div className={`rounded-2xl p-4 text-sm ${a?.estop ? 'bg-red-50 text-red-800' : manual ? 'bg-sun-50 text-ink-900' : 'bg-leaf-50 text-ink-900'}`}>
        <div className="text-base font-semibold">{a?.estop ? "⛔ Arrêt d'urgence : tout est coupé" : manual ? '✋ Vous avez pris la main' : "🤖 L'IA arrose automatiquement"}</div>
        <p className="mt-1">{a?.estop ? "Relâchez-le avec le bouton rouge en haut quand c'est sûr."
          : manual ? "La ferme obéit à vos commandes. Quand vous avez fini, rendez la main à l'IA."
          : "La ferme suit le plan d'arrosage. Toute commande ci-dessous vous donne la main."}</p>
        {manual && !a?.estop && <Button variant="primary" className="mt-2" onClick={() => act.auto()}>🤖 Rendre la main à l'IA</Button>}
      </div>

      {!a ? <Card><p className="text-sm text-ink-500">État de la ferme inconnu (serveur ou ferme injoignable).</p></Card> : (
        <>
          <Card title="Pompe et cuve" icon={<Power size={16} className="text-water-600" />}>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-xl bg-ink-50 p-3">
                <div className="text-xs text-ink-500">Pompe</div>
                <div className={`mt-1 flex items-center gap-2 text-lg font-semibold ${a.pump_on ? 'text-leaf-600' : 'text-ink-700'}`}>
                  <span className={`h-3 w-3 rounded-full ${a.pump_on ? 'animate-pulse bg-leaf-500' : 'bg-ink-400'}`} />{a.pump_on ? 'Tourne' : 'Arrêtée'}
                </div>
                {a.pump_on && a.pump_remaining_s !== null && <div className="text-xs text-ink-500">s'arrête seule dans {Math.ceil(a.pump_remaining_s / 60)} min</div>}
              </div>
              <div className="rounded-xl bg-ink-50 p-3">
                <div className="text-xs text-ink-500">Eau dans la cuve<InfoTip text="La cuve contient au maximum 80 m³ (80 000 litres). La pompe du puits la remplit, surtout quand le soleil brille." /></div>
                <div className="mt-1 text-lg font-semibold text-water-700">{fr(tank, 0)} m³</div>
                <Bar value={(tank ?? 0) / TANK_CAPACITY} tone="water" />
              </div>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button variant="success" onClick={() => act.pump(true, duration)}><Droplets size={15} /> Démarrer la pompe</Button>
              <select className="rounded-lg border border-ink-200 bg-white px-2 py-2 text-sm" value={duration} onChange={(e) => setDuration(Number(e.target.value))} aria-label="Pendant combien de temps">
                {[10, 30, 60, 120].map((m) => <option key={m} value={m}>pendant {m} min</option>)}
              </select>
              <Button onClick={() => act.pump(false)}>Arrêter la pompe</Button>
            </div>
            <p className="mt-2 text-xs text-ink-500">Ouvrez au moins un robinet avant de démarrer la pompe.</p>
          </Card>

          <Card title="Robinets des parcelles" icon={<span>🚰</span>}>
            <div className="space-y-2">
              {PLOTS.map((id) => {
                const open = a.valves?.[id]
                return (
                  <div key={id} className={`flex flex-wrap items-center gap-2 rounded-xl border p-3 ${open ? 'border-water-200 bg-water-50' : 'border-ink-200'}`}>
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-ink-900">{emoji(crops[id])} {plotName(id, crops)}</div>
                      <div className={`text-xs ${open ? 'text-water-700' : 'text-ink-500'}`}>{open ? '💧 robinet ouvert' : 'robinet fermé'}</div>
                    </div>
                    <Button size="sm" onClick={() => act.valve(id, !open)}>{open ? 'Fermer' : 'Ouvrir'}</Button>
                    <Button size="sm" variant="ghost" className="text-red-600" onClick={() => setIsolate(id)}>Couper (fuite)</Button>
                  </div>
                )
              })}
            </div>
            <p className="mt-2 text-xs text-ink-500">« Couper (fuite) » arrête la pompe puis ferme le robinet de cette parcelle, en une seule fois.</p>
          </Card>

          <Details summary={<><ShieldCheck size={15} className="mr-1 inline text-leaf-600" />Règles de sécurité (toujours actives)</>}>
            <ul className="list-disc space-y-1 pl-5 text-sm text-ink-700">
              <li>L'arrêt d'urgence passe avant tout ; seule une personne peut le relâcher, avec un motif.</li>
              <li>La pompe ne tourne jamais plus de 2 heures d'affilée (arrêt automatique).</li>
              <li>Pas de pompe si la cuve est vide ou si on ne connaît pas son niveau.</li>
              <li>Pas de pompe si tous les robinets sont fermés.</li>
              <li>Pas de redémarrage trop rapide (protège le moteur).</li>
              <li>Ces règles s'appliquent aussi à un assistant IA ou à un boîtier électronique.</li>
            </ul>
          </Details>
        </>
      )}

      <Card title="Ce qui a été fait" icon={<ScrollText size={16} className="text-ink-500" />}
        actions={<label className="flex items-center gap-1 text-xs text-ink-500">Votre nom
          <input className="w-28 rounded border border-ink-200 px-2 py-1 text-ink-900" value={name}
            onChange={(e) => { setName(e.target.value); localStorage.setItem('aquamind:operator', e.target.value) }} /></label>}>
        <Journal />
      </Card>

      <ConfirmDialog open={isolate !== null} onClose={() => setIsolate(null)} title={isolate ? `Couper ${plotName(isolate, crops)}` : ''}
        message={<>En une seule fois : <b>1)</b> la pompe s'arrête, <b>2)</b> le robinet de cette parcelle se ferme, <b>3)</b> une alerte est enregistrée.
          Les autres parcelles restent arrêtées jusqu'à ce que vous rendiez la main à l'IA.</>}
        requireReason="Pourquoi ? (ex. fuite visible sur le tuyau des tomates)" confirmLabel="Couper" variant="danger"
        onConfirm={(reason) => isolate && act.isolate(isolate, reason)} />
      {a && <Badge tone="neutral">appareil : {a.device === 'simulateur' ? 'ferme simulée' : a.device}</Badge>}
    </div>
  )
}
