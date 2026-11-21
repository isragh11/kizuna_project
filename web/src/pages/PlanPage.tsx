import { useState } from 'react'
import { CalendarClock, RefreshCw, Sparkles, Table2, Warehouse } from 'lucide-react'
import { PlanChart, TankChart } from '../components/Charts'
import Explanations from '../components/Explanations'
import { Badge, Button, Card, ConfirmDialog, Empty, SimLabel, fr, pct } from '../components/ui'
import { apiGet } from '../lib/api'
import { useActions, useNotify } from '../lib/actions'
import { usePlan, useScenarios } from '../lib/usePlan'
import type { OptimizeResult } from '../lib/types'

export function PlotTable({ plan }: { plan: OptimizeResult }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead className="text-xs text-ink-500">
          <tr><th className="py-2">Parcelle</th><th>Stade · Kc</th><th>Besoin 48 h</th><th>Servi</th><th>Satisfaction</th><th>Paliers survie / moyen / confort</th><th>Valeur par m³ (paliers)</th><th>Perte</th></tr>
        </thead>
        <tbody>
          {Object.entries(plan.plots).map(([id, p]) => (
            <tr key={id} className="border-t border-ink-100">
              <td className="py-2 font-medium text-ink-900">{id} · {p.crop}{p.perennial && <span className="ml-1 text-xs text-leaf-600">(pérenne)</span>}</td>
              <td className="text-ink-700">{p.stage_note} · {fr(p.kc, 2)}</td>
              <td className="tabular-nums">{fr(p.need_m3, 1)} m³</td>
              <td className="tabular-nums">{fr(p.base_served_m3, 1)} m³</td>
              {p.need_m3 <= 0 ? <td colSpan={3} className="text-ink-400">aucun besoin (jachère ou pluie)</td> : (<>
                <td><Badge tone={p.satisfaction >= 0.95 ? 'ok' : p.satisfaction >= 0.5 ? 'warn' : 'danger'}>{pct(p.satisfaction)}</Badge></td>
                <td className="tabular-nums">{p.tranche_fill.map((f) => pct(f)).join(' / ')}</td>
                <td className="tabular-nums text-ink-700">{p.tranche_values_tnd_per_m3.map((v) => fr(v, 2)).join(' / ')} TND</td>
              </>)}
              <td className={`tabular-nums ${p.value_lost_tnd > 0.5 ? 'text-red-600' : 'text-ink-400'}`}>{fr(Math.max(0, p.value_lost_tnd), 0)} TND</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Comparison({ plan }: { plan: OptimizeResult }) {
  const k = plan.kpis, b = plan.baseline_kpis
  const rows: [string, number, number, string, number, boolean][] = [
    ['Eau pompée', b.water_pumped_m3, k.water_pumped_m3, 'm³', 0, true],
    ['Énergie réseau', b.grid_kwh, k.grid_kwh, 'kWh', 1, true],
    ['Part solaire', b.solar_share_pct, k.solar_share_pct, '%', 0, false],
    ['Coût réseau', b.grid_cost_tnd, k.grid_cost_tnd, 'TND', 2, true],
    ['Satisfaction moyenne', b.satisfaction_mean_pct, k.satisfaction_mean_pct, '%', 0, false],
    ['Valeur de récolte protégée', b.value_protected_tnd, k.value_protected_tnd, 'TND', 0, false],
  ]
  return (
    <table className="w-full text-sm">
      <thead className="text-xs text-ink-500"><tr><th className="py-1 text-left">48 h</th><th className="text-right">Traditionnel</th><th className="text-right">AquaMind</th><th className="text-right">Écart</th></tr></thead>
      <tbody>
        {rows.map(([label, base, opt, unit, d, lowerBetter]) => {
          const diff = opt - base
          const good = lowerBetter ? diff < -1e-6 : diff > 1e-6
          return (
            <tr key={label} className="border-t border-ink-100">
              <td className="py-1.5 text-ink-700">{label}</td>
              <td className="text-right tabular-nums text-ink-500">{fr(base, d)} {unit}</td>
              <td className="text-right font-semibold tabular-nums text-ink-900">{fr(opt, d)} {unit}</td>
              <td className={`text-right tabular-nums ${Math.abs(diff) < 1e-6 ? 'text-ink-400' : good ? 'text-leaf-600' : 'text-red-600'}`}>{diff > 0 ? '+' : ''}{fr(diff, d)}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export default function PlanPage() {
  const { plan, error, fromCache } = usePlan()
  const act = useActions()
  const notify = useNotify()
  const scenarios = useScenarios()
  const [choice, setChoice] = useState('tunisia_heatwave')
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)

  const applyScenario = async () => {
    setBusy(true)
    try {
      await apiGet(`/scenarios/${choice}?apply=true`, { cache: false, timeoutMs: 60000 })
      notify(true, 'Plan du scénario appliqué : le simulateur le suit')
    } catch (e: any) {
      notify(false, `Scénario non appliqué : ${e?.message ?? e}`)
    } finally { setBusy(false) }
  }

  return (
    <div className="space-y-5">
      <Card title="Plan IA appliqué (48 h)" icon={<CalendarClock size={16} className="text-water-600" />}
        actions={<>
          {plan && <span className="text-xs text-ink-500">{plan.inputs?.start} · résolu en {fr(plan.solve_time_ms, 0)} ms</span>}
          <SimLabel />{fromCache && <Badge tone="warn">cache</Badge>}
          <Button size="sm" variant="primary" disabled={busy} onClick={async () => { setBusy(true); await act.refreshPlan(); setBusy(false) }}>
            <RefreshCw size={14} /> Recalculer (prévision)
          </Button>
        </>}>
        {plan ? <PlanChart plan={plan} /> : <Empty>{error ?? 'Aucun plan appliqué.'}</Empty>}
      </Card>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Explications de l'optimiseur" icon={<Sparkles size={16} className="text-sun-600" />}>
          {plan ? <Explanations items={plan.explanations} /> : <Empty>—</Empty>}
        </Card>
        <div className="space-y-5">
          <Card title="Comparaison avec l'irrigation traditionnelle"><>{plan ? <Comparison plan={plan} /> : <Empty>—</Empty>}</></Card>
          <Card title="Appliquer un scénario de démonstration">
            <div className="space-y-3">
              <select className="w-full rounded-lg border border-ink-200 bg-white px-2 py-2 text-sm" value={choice} onChange={(e) => setChoice(e.target.value)}>
                {scenarios.map((s) => <option key={s.key} value={s.key}>{s.title}</option>)}
              </select>
              <p className="text-xs text-ink-500">{scenarios.find((s) => s.key === choice)?.description}</p>
              <Button variant="warn" className="w-full" disabled={busy} onClick={() => setConfirm(true)}>Appliquer ce scénario…</Button>
              <p className="text-xs text-ink-500">Le plan automatique (prévision) reprend avec « Recalculer ».</p>
            </div>
          </Card>
        </div>
      </div>

      {plan && (
        <div className="grid gap-5 lg:grid-cols-3">
          <Card className="lg:col-span-2" title="Parcelles : paliers de valeur et rationnement" icon={<Table2 size={16} className="text-leaf-600" />}><PlotTable plan={plan} /></Card>
          <Card title="Réservoir et pompage" icon={<Warehouse size={16} className="text-water-600" />}><TankChart plan={plan} height={220} /></Card>
        </div>
      )}
      <ConfirmDialog open={confirm} onClose={() => setConfirm(false)} title="Appliquer un scénario"
        message={<>La ferme (simulateur) suivra le plan « {scenarios.find((s) => s.key === choice)?.title} » à la place du plan automatique.</>}
        confirmLabel="Appliquer" variant="warn" onConfirm={applyScenario} />
    </div>
  )
}
