import { AlertTriangle, Droplets, Gauge, Sun, Zap, Leaf, Warehouse, Bell } from 'lucide-react'
import Explanations from '../components/Explanations'
import { LiveFlowChart, TankLiveChart } from '../components/Charts'
import ModelsCard from '../components/ModelsCard'
import { Badge, Card, Empty, SimLabel, Stat, fr } from '../components/ui'
import { useLive } from '../lib/live'
import { usePlan } from '../lib/usePlan'

const LIMIT_TEXT: Record<string, { label: string; tone: string; text: string }> = {
  water: { label: "L'EAU", tone: 'bg-water-600', text: 'Le quota est entièrement utilisé : chaque m³ supplémentaire protégerait de la récolte.' },
  energy: { label: "L'ÉNERGIE", tone: 'bg-sun-500', text: "Le solaire et la puissance réseau limitent le pompage." },
  pump: { label: 'LA POMPE', tone: 'bg-ink-700', text: 'Le débit de la pompe est saturé aux heures utiles.' },
  none: { label: 'AUCUNE', tone: 'bg-leaf-600', text: 'Toutes les parcelles reçoivent leur besoin prévu.' },
}

export default function Overview() {
  const live = useLive()
  const { plan, fromCache } = usePlan()
  const k = plan?.kpis
  const active = live.alerts.filter((a) => a.active)
  const lim = LIMIT_TEXT[plan?.limiting_factor ?? 'none']

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Eau prévue (48 h)" value={fr(k?.water_pumped_m3, 0)} unit="m³" icon={<Droplets size={18} />} tone="water"
          sub={k ? `besoin ${fr(k.need_m3, 0)} m³ · quota ${fr(k.quota_m3, 0)} m³` : undefined} />
        <Stat label="Part solaire" value={fr(k?.solar_share_pct, 0)} unit="%" icon={<Sun size={18} />} tone="sun"
          sub={k ? `${fr(k.pv_kwh, 1)} kWh solaire · ${fr(k.grid_kwh, 1)} kWh réseau` : undefined} />
        <Stat label="kWh réseau évités" value={fr(k?.grid_kwh_avoided, 1)} unit="kWh" icon={<Zap size={18} />} tone="ink"
          sub={k ? `vs irrigation traditionnelle (${fr(k.grid_cost_saved_tnd, 2)} TND)` : undefined} />
        <Stat label="Satisfaction moyenne" value={fr(k?.satisfaction_mean_pct, 0)} unit="%" icon={<Leaf size={18} />} tone="leaf"
          sub={k ? `minimum ${fr(k.satisfaction_min_pct, 0)} % · valeur protégée ${fr(k.value_protected_tnd, 0)} TND` : undefined} />
        <Stat label="Alertes actives" value={active.length} icon={<Bell size={18} />} tone={active.length ? 'danger' : 'leaf'}
          sub={`cuve ${fr(live.latest.tank ?? null, 1)} m³ · solaire ${fr(live.latest.pv ?? null, 2)} kW`} />
      </div>

      {plan && (
        <div className="flex flex-col gap-3 rounded-2xl border border-ink-200/70 bg-white p-4 shadow-card md:flex-row md:items-center">
          <div className={`flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-bold text-white ${lim.tone}`}>
            <Gauge size={18} /> Facteur limitant : {lim.label}
          </div>
          <p className="text-sm text-ink-700">{lim.text} {plan.limiting_factor === 'water' && <>Prix fictif de l'eau : <b>{fr(plan.duals.water, 2)} TND/m³</b>.</>}</p>
          <div className="md:ml-auto"><SimLabel /> {fromCache && <Badge tone="warn">cache</Badge>}</div>
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Débits en direct" icon={<Droplets size={16} className="text-water-600" />}
          actions={<span className="text-xs text-ink-500">heure simulée {live.series.at(-1)?.t ?? '—'}</span>}>
          {live.series.length ? <LiveFlowChart series={live.series} /> : <Empty>En attente des mesures du simulateur…</Empty>}
        </Card>
        <Card title="Cuve et solaire" icon={<Warehouse size={16} className="text-water-600" />}>
          {live.series.length ? <TankLiveChart series={live.series} height={220} /> : <Empty>En attente des mesures…</Empty>}
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Pourquoi ce plan ?" icon={<Leaf size={16} className="text-leaf-600" />}
          actions={plan && <span className="text-xs text-ink-500">{plan.inputs?.sources?.join(' · ')}</span>}>
          {plan ? <Explanations items={plan.explanations} max={5} /> : <Empty>Aucun plan appliqué pour l'instant.</Empty>}
        </Card>
        <Card title="Évènements" icon={<AlertTriangle size={16} className="text-sun-600" />}>
          {live.events.length ? (
            <ul className="max-h-80 space-y-2 overflow-y-auto text-xs">
              {live.events.map((e, i) => (
                <li key={i} className="border-b border-ink-100 pb-1.5 last:border-0">
                  <span className="text-ink-400">{new Date(e.ts).toLocaleTimeString('fr-FR')}</span> · <span className="text-ink-700">{e.text}</span>
                </li>
              ))}
            </ul>
          ) : <Empty>Rien à signaler.</Empty>}
        </Card>
      </div>

      <ModelsCard compact />
    </div>
  )
}
