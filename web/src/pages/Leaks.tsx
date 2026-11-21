import { useState } from 'react'
import { Bell, Droplets, FlaskConical, Wrench, Waves, RotateCcw, Activity } from 'lucide-react'
import AlertCard from '../components/AlertCard'
import { ImbalanceChart, LiveFlowChart } from '../components/Charts'
import ModelsCard from '../components/ModelsCard'
import { Badge, Button, Card, ConfirmDialog, Empty, fr } from '../components/ui'
import { useActions } from '../lib/actions'
import { useLive } from '../lib/live'

const SECTIONS = [
  { id: 'S1', label: 'S1 · conduite principale' }, { id: 'S2', label: 'S2 · parcelle A' },
  { id: 'S3', label: 'S3 · parcelle B' }, { id: 'S4', label: 'S4 · parcelle C' },
]

export default function Leaks() {
  const live = useLive()
  const act = useActions()
  const [section, setSection] = useState('S3')
  const [rate, setRate] = useState(1.5)
  const [flushRate, setFlushRate] = useState(0.6)
  const [confirmNormal, setConfirmNormal] = useState(false)
  const active = live.alerts.filter((a) => a.active)
  const flowing = live.latest.flows
  const inWater = (s: string) => (flowing[s]?.plan ?? 0) > 0.1
  const last = live.series.at(-1)

  return (
    <div className="space-y-5">
      <div className="grid gap-5 lg:grid-cols-3">
        <Card title="Simulation et actions" icon={<Wrench size={16} className="text-ink-700" />} className="lg:col-span-1">
          <div className="space-y-4 text-sm">
            <div>
              <div className="mb-1 text-xs font-medium text-ink-500">Injecter une fuite</div>
              <div className="grid grid-cols-2 gap-2">
                {SECTIONS.map((s) => (
                  <button key={s.id} onClick={() => setSection(s.id)}
                    className={`rounded-lg border px-2 py-1.5 text-left text-xs ${section === s.id ? 'border-water-400 bg-water-50 text-water-900' : 'border-ink-200 text-ink-700'}`}>
                    {s.label}<br /><span className={inWater(s.id) ? 'text-leaf-600' : 'text-ink-400'}>{inWater(s.id) ? '● en eau' : '○ à l\'arrêt'}</span>
                  </button>
                ))}
              </div>
              <label className="mt-2 flex items-center gap-2 text-xs text-ink-500">
                Débit <input type="range" min={0.4} max={3} step={0.1} value={rate} onChange={(e) => setRate(Number(e.target.value))} className="flex-1 accent-water-600" />
                <b className="w-16 text-right text-ink-900">{fr(rate, 1)} m³/h</b>
              </label>
              {!inWater(section) && <p className="mt-1 text-xs text-sun-600">Ce tronçon n'est pas en eau : la fuite ne coulera qu'à sa prochaine irrigation.</p>}
              <Button variant="danger" className="mt-2 w-full" onClick={() => act.leak(section, rate)}><Droplets size={14} /> Injecter une fuite sur {section}</Button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <Button variant="success" onClick={() => act.repair()}><Wrench size={14} /> Réparer</Button>
              <Button onClick={() => setConfirmNormal(true)}><RotateCcw size={14} /> Nouveau normal</Button>
            </div>
            <div className="rounded-xl bg-ink-50 p-3">
              <div className="text-xs font-medium text-ink-500">Rinçage des filtres (dérive légitime, pas une fuite)</div>
              <label className="mt-1 flex items-center gap-2 text-xs text-ink-500">
                <input type="range" min={0.2} max={1.5} step={0.1} value={flushRate} onChange={(e) => setFlushRate(Number(e.target.value))} className="flex-1 accent-sun-500" />
                <b className="w-16 text-right text-ink-900">{fr(flushRate, 1)} m³/h</b>
              </label>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <Button variant="warn" size="sm" onClick={() => act.flush(flushRate)}><Waves size={14} /> Démarrer</Button>
                <Button size="sm" onClick={() => act.flush(0)}>Arrêter</Button>
              </div>
              <p className="mt-2 text-xs text-ink-500">La règle adaptative l'intègre à sa référence (quelques alertes au début au plus) ; « Nouveau normal » l'accepte immédiatement.</p>
            </div>
          </div>
        </Card>

        <Card className="lg:col-span-2" title="Bilan de masse en direct" icon={<Activity size={16} className="text-water-600" />}
          actions={<>{last && <span className="text-xs text-ink-500">S1 − ΣS = <b className={Math.abs(last.imbalance) > 0.5 ? 'text-red-600' : 'text-ink-900'}>{fr(last.imbalance, 2)} m³/h</b> · {last.t}</span>}</>}>
          {live.series.length ? (
            <div className="space-y-2">
              <ImbalanceChart series={live.series} />
              <LiveFlowChart series={live.series} height={180} />
            </div>
          ) : <Empty>En attente des mesures du simulateur…</Empty>}
        </Card>
      </div>

      <Card title="Alertes" icon={<Bell size={16} className={active.length ? 'text-red-600' : 'text-ink-500'} />}
        actions={<><Badge tone={active.length ? 'danger' : 'ok'}>{active.length} en cours</Badge><Badge>{live.alerts.length} au total</Badge></>}>
        {live.alerts.length ? (
          <div className="grid gap-3 md:grid-cols-2">{live.alerts.slice(0, 12).map((a) => <AlertCard key={a.alert_id ?? a.ts} a={a} />)}</div>
        ) : <Empty>Aucune alerte. Injectez une fuite sur un tronçon en eau pour la démo.</Empty>}
      </Card>

      <div className="flex items-center gap-2 text-sm text-ink-500"><FlaskConical size={16} /> Validation des détecteurs</div>
      <ModelsCard />
      <ConfirmDialog open={confirmNormal} onClose={() => setConfirmNormal(false)} title="Nouveau normal"
        message="La situation actuelle (débits, prélèvements) devient la référence du bilan de masse. À n'utiliser que pour un changement voulu (rinçage, nouvelle vanne), jamais pour masquer une fuite."
        confirmLabel="Confirmer" onConfirm={() => act.newNormal()} />
    </div>
  )
}
