import { useState } from 'react'
import { Bell, Droplets, FlaskConical, Wrench, Activity } from 'lucide-react'
import AlertCard from '../components/AlertCard'
import { LossChart } from '../components/FarmCharts'
import ModelsCard from '../components/ModelsCard'
import { Badge, Button, Card, ConfirmDialog, Details, Empty, InfoTip, fr } from '../components/ui'
import { useActions } from '../lib/actions'
import { useLive } from '../lib/live'
import { PIPE_HELP, emoji, pipeName } from '../lib/labels'
import { useCrops } from '../lib/useCrops'
import type { PlotId } from '../lib/types'

const PIPES = ['S1', 'S2', 'S3', 'S4'] as const
const PLOT_OF: Record<string, PlotId> = { S2: 'A', S3: 'B', S4: 'C' }
const SIZES = [
  { rate: 0.8, label: 'Petite', sub: '≈ 13 L/min' },
  { rate: 1.5, label: 'Moyenne', sub: '≈ 25 L/min' },
  { rate: 2.5, label: 'Grosse', sub: '≈ 42 L/min' },
]

export default function Leaks() {
  const live = useLive()
  const crops = useCrops()
  const act = useActions()
  const [pipe, setPipe] = useState<string>('S2')
  const [rate, setRate] = useState(1.5)
  const [confirmNormal, setConfirmNormal] = useState(false)
  const [isolate, setIsolate] = useState<PlotId | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const active = live.alerts.filter((a) => a.active && a.detector !== 'isolation')
  const flows = live.latest.flows
  const valves = live.actuators?.valves
  const pumpOn = !!live.actuators?.pump_on
  // en direct si disponible, sinon d'après l'état des robinets connu par le serveur
  const inWater = (s: string) => flows[s] ? (flows[s].plan ?? 0) > 0.1
    : PLOT_OF[s] ? pumpOn && !!valves?.[PLOT_OF[s]] : pumpOn
  const last = live.series.at(-1)
  const lossNow = Math.max(0, last?.imbalance ?? 0)

  return (
    <div className="space-y-5">
      <div className={`rounded-2xl border p-5 ${active.length ? 'border-red-200 bg-red-50' : 'border-leaf-100 bg-leaf-50'}`}>
        <div className="text-xl font-bold text-ink-900">
          {active.length ? `💦 ${active.length > 1 ? `${active.length} fuites en cours` : `Fuite en cours : ${pipeName(active[0].section, crops)}`}` : '✅ Aucune fuite détectée'}
        </div>
        <p className="mt-1 text-sm text-ink-700">
          {active.length
            ? <>Environ <b>{fr(active.reduce((s, a) => s + a.est_loss_m3_day, 0) * 1000, 0)} litres</b> perdus par jour si rien n'est fait. Coupez la parcelle concernée, puis faites réparer.</>
            : <>AquaMind compare en permanence l'eau qui sort de la cuve et l'eau qui arrive aux parcelles. S'il en manque, il vous prévient en quelques secondes.</>}
        </p>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Eau perdue en ce moment" icon={<Activity size={16} className="text-red-600" />}
          actions={<span className="text-sm text-ink-700">maintenant : <b className={lossNow > 0.5 ? 'text-red-600' : 'text-leaf-600'}>{fr(lossNow * 1000 / 60, 0)} L/min</b> <span className="text-xs text-ink-400">({last?.t ?? '—'})</span></span>}>
          {live.series.length ? (
            <>
              <LossChart series={live.series} />
              <p className="mt-2 rounded-xl bg-ink-50 p-3 text-sm text-ink-700">
                <b>Comment lire :</b> la courbe rouge = eau qui sort de la cuve mais n'arrive à aucune parcelle. Près de zéro, tout va bien
                (les petits tremblements viennent des compteurs). Si elle passe <b>au-dessus du trait pointillé</b> (0,5 m³/h ≈ 8 L/min) pendant 20 minutes, une alerte est donnée.
              </p>
            </>
          ) : <Empty>En attente des mesures de la ferme…</Empty>}
        </Card>

        <Card title="Les tuyaux de la ferme" icon={<Droplets size={16} className="text-water-600" />}>
          <ul className="space-y-2 text-sm">
            {(['S1', 'S2', 'S3', 'S4', 'S5'] as const).map((s) => (
              <li key={s} className="flex items-start gap-2">
                <span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${active.some((a) => a.section === s) ? 'bg-red-500' : inWater(s) ? 'bg-water-500' : 'bg-ink-200'}`} />
                <div><div className="font-medium text-ink-900">{PLOT_OF[s] ? emoji(crops[PLOT_OF[s]]) + ' ' : ''}{pipeName(s, crops)}</div>
                  <div className="text-xs text-ink-500">{PIPE_HELP[s]} {inWater(s) ? '· l\'eau passe en ce moment' : '· pas d\'eau en ce moment'}</div></div>
              </li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title="Alertes" icon={<Bell size={16} className={active.length ? 'text-red-600' : 'text-ink-500'} />}
        actions={<><Badge tone={active.length ? 'danger' : 'ok'}>{active.length} en cours</Badge><Badge>{live.alerts.length} au total</Badge>
          {live.alerts.length > active.length && <Button size="sm" variant="ghost" onClick={() => setConfirmClear(true)}>Effacer l'historique</Button>}</>}>
        {live.alerts.length ? (
          <div className="grid gap-3 md:grid-cols-2">
            {live.alerts.slice(0, 10).map((a) => (
              <AlertCard key={a.alert_id ?? a.ts} a={a} crops={crops} onIsolate={PLOT_OF[a.section] ? () => setIsolate(PLOT_OF[a.section]) : undefined} />
            ))}
          </div>
        ) : <Empty>Aucune alerte.</Empty>}
      </Card>

      <Card title="Mode démonstration : simuler des incidents" icon={<Wrench size={16} className="text-ink-700" />}
        actions={<Badge tone="sun">pour tester</Badge>}>
        <div className="grid gap-5 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <div className="text-sm font-semibold text-ink-900">1. Créer une fuite</div>
            <p className="mb-2 text-xs text-ink-500">Choisissez le tuyau. Une fuite ne coule que si l'eau passe dans ce tuyau (robinet ouvert) : choisissez de préférence un tuyau marqué « l'eau passe ».</p>
            <div className="grid grid-cols-2 gap-2">
              {PIPES.map((s) => (
                <button key={s} onClick={() => setPipe(s)}
                  className={`rounded-xl border px-3 py-2 text-left text-sm ${pipe === s ? 'border-water-400 bg-water-50 ring-2 ring-water-200' : 'border-ink-200'}`}>
                  <div className="font-medium text-ink-900">{PLOT_OF[s] ? emoji(crops[PLOT_OF[s]]) + ' ' : ''}{pipeName(s, crops)}</div>
                  <div className={`text-xs ${inWater(s) ? 'text-leaf-600' : 'text-ink-400'}`}>{inWater(s) ? '● l\'eau passe' : '○ pas d\'eau en ce moment'}</div>
                </button>
              ))}
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-sm text-ink-700">Taille :</span>
              {SIZES.map((z) => (
                <button key={z.rate} onClick={() => setRate(z.rate)}
                  className={`rounded-lg border px-3 py-1.5 text-sm ${rate === z.rate ? 'border-water-400 bg-water-50' : 'border-ink-200'}`}>
                  {z.label} <span className="text-xs text-ink-400">{z.sub}</span>
                </button>
              ))}
            </div>
            {!inWater(pipe) && <p className="mt-2 text-xs text-sun-600">Pas d'eau dans ce tuyau en ce moment : la fuite coulera seulement à sa prochaine ouverture.</p>}
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="danger" onClick={() => act.leak(pipe, rate)}>💦 Créer la fuite</Button>
              <Button variant="success" onClick={() => act.repair()}>🔧 Réparer toutes les fuites</Button>
            </div>
          </div>
          <div className="rounded-xl bg-ink-50 p-3">
            <div className="text-sm font-semibold text-ink-900">2. Nettoyage des filtres <span className="font-normal text-ink-500">(ce n'est pas une fuite)</span></div>
            <p className="mt-1 text-xs text-ink-700">
              Les filtres du goutte-à-goutte se nettoient en rejetant un peu d'eau. Les compteurs le voient comme une perte.
              AquaMind finit par comprendre que c'est normal (après quelques heures), ou tout de suite si vous cliquez <b>« C'est normal »</b>.
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <Button size="sm" variant="warn" onClick={() => act.flush(0.6)}>Démarrer le nettoyage</Button>
              <Button size="sm" onClick={() => act.flush(0)}>Arrêter</Button>
            </div>
            <div className="mt-3 border-t border-ink-200 pt-3">
              <Button size="sm" className="w-full" onClick={() => setConfirmNormal(true)}>🔄 C'est normal (j'ai changé mon installation)</Button>
              <p className="mt-1 text-xs text-ink-500">À utiliser après un changement voulu (nouveau robinet, nettoyage…). Jamais pour cacher une vraie fuite.
                <InfoTip text="AquaMind apprend à quoi ressemble votre ferme « normale ». Ce bouton lui dit : la situation actuelle est normale, prends-la comme nouvelle référence." /></p>
            </div>
          </div>
        </div>
      </Card>

      <Details summary={<><FlaskConical size={15} className="mr-1 inline" />Pour le jury : fiabilité des modèles et des détecteurs</>}>
        <ModelsCard />
      </Details>

      <ConfirmDialog open={confirmNormal} onClose={() => setConfirmNormal(false)} title="C'est normal ?"
        message="La situation actuelle devient la référence. Les écarts d'eau actuels ne seront plus signalés comme des fuites. À utiliser uniquement après un changement voulu."
        confirmLabel="Oui, c'est normal" onConfirm={() => act.newNormal()} />
      <ConfirmDialog open={confirmClear} onClose={() => setConfirmClear(false)} title="Effacer l'historique des alertes ?"
        message="Les alertes terminées (par exemple celles des tests) sont supprimées. Une alerte en cours est gardée."
        confirmLabel="Effacer" variant="danger" onConfirm={() => act.clearAlerts()} />
      <ConfirmDialog open={isolate !== null} onClose={() => setIsolate(null)} title="Couper cette parcelle"
        message={<>La pompe s'arrête, puis le robinet de cette parcelle se ferme. Les autres parcelles restent arrêtées jusqu'à ce que vous rendiez la main à l'IA.</>}
        requireReason="Pourquoi ? (ex. fuite détectée)" confirmLabel="Couper" variant="danger"
        onConfirm={(reason) => isolate && act.isolate(isolate, reason)} />
    </div>
  )
}
