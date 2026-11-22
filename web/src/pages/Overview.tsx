import { Droplets, Sun, Coins, Sprout, Bell, Warehouse } from 'lucide-react'
import { WaterFlowChart } from '../components/FarmCharts'
import { PlanSituation } from '../components/PlanSummary'
import { Bar, Card, Empty, InfoTip, SimLabel, fr, pct } from '../components/ui'
import { readingAgeSeconds, useLive } from '../lib/live'
import { emoji, pipeName, plotName, satisfactionText } from '../lib/labels'
import { useCrops } from '../lib/useCrops'
import { usePlan } from '../lib/usePlan'
import { PLOTS, SECTION_OF } from '../lib/types'

function Tile({ icon, label, value, sub, help, tone }: {
  icon: React.ReactNode; label: string; value: React.ReactNode; sub?: React.ReactNode; help: string; tone: string
}) {
  return (
    <div className="rounded-2xl border border-ink-200/70 bg-white p-4 shadow-card">
      <div className="flex items-center gap-2 text-sm text-ink-500"><span className={`rounded-lg p-1.5 ${tone}`}>{icon}</span>{label}<InfoTip text={help} /></div>
      <div className="mt-2 text-2xl font-bold tabular-nums text-ink-900">{value}</div>
      {sub && <div className="mt-1 text-sm text-ink-500">{sub}</div>}
    </div>
  )
}

export default function Overview() {
  const live = useLive()
  const { plan } = usePlan()
  const crops = useCrops(plan)
  const k = plan?.kpis
  const active = live.alerts.filter((a) => a.active && a.detector !== 'isolation')
  const age = readingAgeSeconds(live)
  const a = live.actuators
  const flows = live.latest.flows
  const last = live.series.at(-1)

  // Message principal, en une phrase
  let headline = { tone: 'bg-leaf-50 border-leaf-100', text: '✅ Tout va bien sur la ferme', sub: "Pas de fuite détectée. L'IA s'occupe de l'arrosage." }
  if (a?.estop) headline = { tone: 'bg-red-50 border-red-200', text: "⛔ Arrêt d'urgence en cours", sub: 'Tout est coupé. Relâchez avec le bouton rouge quand c\'est sûr.' }
  else if (active.length) headline = { tone: 'bg-red-50 border-red-200', text: `💦 Fuite détectée : ${pipeName(active[0].section, crops)}`, sub: `Environ ${fr(active[0].est_loss_m3_day * 1000, 0)} litres perdus par jour. Voir la page Fuites.` }
  else if (age === null || age > 30) headline = { tone: 'bg-sun-50 border-sun-400/40', text: '⚠️ La ferme n\'envoie plus de nouvelles', sub: 'Vérifiez que le simulateur tourne (fenêtre 2).' }
  else if (a?.mode === 'manual') headline = { tone: 'bg-sun-50 border-sun-400/40', text: '✋ Vous pilotez la ferme à la main', sub: "Pensez à rendre la main à l'IA (bouton Commandes)." }

  const plotsSorted = PLOTS
  const minPlot = plan ? plotsSorted.filter((id) => plan.plots[id]?.need_m3 > 0).sort((x, y) => plan.satisfaction[x] - plan.satisfaction[y])[0] : undefined

  return (
    <div className="space-y-5">
      <div className={`rounded-2xl border p-5 ${headline.tone}`}>
        <div className="text-xl font-bold text-ink-900">{headline.text}</div>
        <div className="mt-1 text-sm text-ink-700">{headline.sub}</div>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Tile icon={<Droplets size={18} />} tone="bg-water-50 text-water-600" label="Eau prévue sur 2 jours"
          help="L'eau que l'IA va pomper dans les 2 prochains jours (1 m³ = 1 000 litres). Le quota est ce que vous avez le droit de prendre."
          value={<>{fr(k?.water_pumped_m3, 0)} m³</>}
          sub={k ? <>vos cultures en demandent {fr(k.need_m3, 0)} m³ · autorisé : {fr(k.quota_m3, 0)} m³</> : undefined} />
        <Tile icon={<Sun size={18} />} tone="bg-sun-50 text-sun-600" label="Énergie du soleil"
          help="Part de l'électricité de la pompe qui vient de vos panneaux solaires (gratuite). Le reste est acheté au réseau."
          value={<>{fr(k?.solar_share_pct, 0)} %</>}
          sub={k ? <>de l'électricité de la pompe vient de vos panneaux</> : undefined} />
        <Tile icon={<Coins size={18} />} tone="bg-leaf-50 text-leaf-600" label="Argent économisé sur l'électricité"
          help="Comparé à une irrigation « à l'ancienne » (pompe allumée dès 7h à fond, même dose pour toutes les parcelles), l'IA pompe au soleil et évite d'acheter de l'électricité. Estimation simulée."
          value={<>{fr(k?.grid_cost_saved_tnd, 2)} TND</>}
          sub={k ? <>en 2 jours, soit {fr(k.grid_kwh_avoided, 1)} kWh d'électricité en moins qu'à l'ancienne</> : undefined} />
        <Tile icon={<Sprout size={18} />} tone="bg-leaf-50 text-leaf-600" label="Eau reçue par vos cultures"
          help="En moyenne, la part de l'eau demandée que chaque culture recevra. 100 % = rien ne manque. Quand l'eau manque, l'IA partage pour sauver le plus de récolte."
          value={<>{fr(k?.satisfaction_mean_pct, 0)} % <span className="text-sm font-medium text-ink-500">de leurs besoins</span></>}
          sub={plan ? <>
            {minPlot && plan.satisfaction[minPlot] < 0.95 && <>la moins servie : {emoji(crops[minPlot])} {crops[minPlot] ?? minPlot} ({pct(plan.satisfaction[minPlot])})<br /></>}
            récolte sauvée : <b className="text-ink-900">{fr(k?.value_protected_tnd, 0)} TND</b><InfoTip text="Valeur de la récolte protégée par l'eau donnée sur ces 2 jours (prix de récolte supposés). Estimation simulée." />
          </> : undefined} />
      </div>

      {plan && <PlanSituation plan={plan} />}

      <Card title="Qui est arrosé en ce moment ?" icon={<Droplets size={16} className="text-water-600" />}
        actions={<span className="text-xs text-ink-500">heure de la ferme : {last?.t ?? '—'}</span>}>
        <div className="grid gap-3 sm:grid-cols-3">
          {PLOTS.map((id) => {
            const f = flows[SECTION_OF[id]]
            const flowing = f ? f.flow > 0.3 : !!a?.pump_on && !!a?.valves?.[id]
            const sat = plan?.satisfaction?.[id]
            return (
              <div key={id} className={`rounded-xl border p-3 ${flowing ? 'border-water-200 bg-water-50' : 'border-ink-200 bg-white'}`}>
                <div className="font-semibold text-ink-900">{emoji(crops[id])} {plotName(id, crops)}</div>
                <div className={`mt-1 text-sm ${flowing ? 'text-water-700' : 'text-ink-500'}`}>
                  {flowing ? (f ? <>💧 arrosée : {fr(f.flow * 1000 / 60, 0)} litres/minute</> : <>💧 robinet ouvert, arrosage en cours</>) : 'pas arrosée en ce moment'}
                </div>
                {sat !== undefined && plan!.plots[id]?.need_m3 > 0 && (
                  <div className="mt-2"><Bar value={sat} /><div className="mt-1 text-xs text-ink-500">plan : {pct(sat)} de ses besoins · {satisfactionText(sat).label.toLowerCase()}</div></div>
                )}
              </div>
            )
          })}
        </div>
      </Card>

      <div className="grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="L'eau sort-elle bien jusqu'aux cultures ?" icon={<Droplets size={16} className="text-water-600" />}
          actions={<SimLabel />}>
          {live.series.length ? (
            <>
              <WaterFlowChart series={live.series} />
              <div className="mt-2 flex flex-wrap gap-4 text-sm">
                <span className="flex items-center gap-1.5"><span className="h-3 w-6 rounded bg-water-200 ring-2 ring-water-600" /> Eau partie de la cuve</span>
                <span className="flex items-center gap-1.5"><span className="h-0.5 w-6 border-t-2 border-dashed border-leaf-600" /> Eau arrivée aux cultures</span>
              </div>
              <p className="mt-2 rounded-xl bg-ink-50 p-3 text-sm text-ink-700">
                <b>Comment lire :</b> les deux courbes doivent se superposer. Si la zone bleue passe nettement au-dessus de la ligne verte,
                de l'eau se perd entre la cuve et les parcelles : c'est une fuite. La nuit, les courbes sont à zéro (on n'arrose pas).
              </p>
            </>
          ) : <Empty>En attente des mesures de la ferme…</Empty>}
        </Card>
        <div className="space-y-5">
          <Card title="Cuve et soleil" icon={<Warehouse size={16} className="text-water-600" />}>
            <div className="text-sm text-ink-500">Eau dans la cuve</div>
            <div className="text-2xl font-bold text-water-700">{fr(live.latest.tank ?? a?.tank_m3 ?? null, 0)} m³ <span className="text-sm font-medium text-ink-500">sur 80</span></div>
            <Bar value={(live.latest.tank ?? a?.tank_m3 ?? 0) / 80} tone="water" />
            <div className="mt-4 text-sm text-ink-500">Soleil sur les panneaux maintenant</div>
            <div className="text-2xl font-bold text-sun-600">{fr(live.latest.pv ?? null, 1)} kW <span className="text-sm font-medium text-ink-500">sur 5 max</span></div>
            <Bar value={(live.latest.pv ?? 0) / 5} tone="sun" />
          </Card>
          <Card title="Dernières nouvelles" icon={<Bell size={16} className="text-sun-600" />}>
            {live.events.length ? (
              <ul className="max-h-56 space-y-2 overflow-y-auto text-sm">
                {live.events.slice(0, 8).map((e, i) => (
                  <li key={i} className="border-b border-ink-100 pb-1.5 last:border-0">
                    <span className="text-xs text-ink-400">{new Date(e.ts).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}</span> · {friendlyEvent(e.type, e.text)}
                  </li>
                ))}
              </ul>
            ) : <Empty>Rien à signaler.</Empty>}
          </Card>
        </div>
      </div>
    </div>
  )
}

function friendlyEvent(type: string, text: string): string {
  switch (type) {
    case 'alert': return '💦 Une fuite a été détectée.'
    case 'alert_end': return '✅ La fuite est terminée (l\'eau arrive de nouveau normalement).'
    case 'new_normal': return '🔄 Nouvelle situation acceptée comme normale.'
    case 'no_flow': return '⚠️ Le plan prévoit d\'arroser mais aucune eau ne coule (cuve vide ? pompe arrêtée ?).'
    case 'plan': return '📅 Nouveau plan d\'arrosage.'
    case 'command': return `🎛️ ${text}`
    default: return text
  }
}
