// Ma ferme : carte (parcelles colorées selon l'eau reçue, tuyaux en rouge en cas de fuite) et fiches.
// Les cultures affichées sont celles des dates du plan suivi (ex. scénario canicule = juillet).
import { useEffect, useState } from 'react'
import { Map as MapIcon, Sprout } from 'lucide-react'
import FarmMap, { type MapPlot } from '../components/FarmMap'
import { Badge, Bar, Card, Empty, InfoTip, SimLabel, fr, pct } from '../components/ui'
import { apiGet } from '../lib/api'
import { METHOD_TEXT, emoji, plotName, satisfactionText, stageText, thirstText, type Crops } from '../lib/labels'
import { useLive } from '../lib/live'
import { usePlan } from '../lib/usePlan'
import type { PlotId } from '../lib/types'

const LEGEND = [
  { color: '#22a755', label: 'Bien arrosée', range: '95 % et plus' },
  { color: '#84cc16', label: 'Un peu juste', range: '60 à 95 %' },
  { color: '#f59e0b', label: "Manque d'eau", range: '35 à 60 %' },
  { color: '#dc2626', label: 'Gros manque (survie)', range: 'moins de 35 %' },
  { color: '#94a0b2', label: 'Rien ne pousse', range: 'jachère' },
]

export default function Farm() {
  const live = useLive()
  const { plan } = usePlan()
  const [farm, setFarm] = useState<any>(null)
  // Date des cultures : celle du plan suivi s'il porte sur ce site (ex. juillet pour la canicule), sinon aujourd'hui
  const planSite = plan?.inputs?.site ?? 'tunisia'
  const planDay = plan && planSite === live.site ? String(plan.inputs?.start ?? '').slice(0, 10) : ''
  useEffect(() => {
    apiGet<any>(`/farm?site=${live.site}${planDay ? `&day=${planDay}` : ''}`).then((r) => setFarm(r.data)).catch(() => setFarm(null))
  }, [live.site, planDay])

  const usePlanSat = !!planDay
  const alertSections = new Set(live.alerts.filter((a) => a.active && a.detector !== 'isolation').map((a) => a.section))
  const crops: Crops = Object.fromEntries((farm?.farm?.plots ?? []).map((p: any) => [p.id, p.crop.name]))
  const plots: MapPlot[] = (farm?.farm?.plots ?? []).map((p: any) => ({
    id: p.id, lat: p.lat, lon: p.lon, area_ha: p.area_ha, crop: p.crop.name,
    satisfaction: usePlanSat ? plan?.satisfaction?.[p.id] : undefined,
    valveOpen: live.site === 'tunisia' ? live.actuators?.valves?.[p.id as PlotId] : undefined,
    fallow: p.kc_effective === 0,
  }))
  const dateText = farm?.date ? new Date(farm.date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' }) : '—'

  return (
    <div className="space-y-5">
      <Card title={farm?.farm?.name ?? 'Ma ferme'} icon={<MapIcon size={16} className="text-water-600" />}
        actions={<span className="text-xs text-ink-500">cultures au {dateText}{planDay ? ' (dates du plan suivi)' : ''}</span>}>
        {plots.length ? <FarmMap plots={plots} alertSections={alertSections} crops={crops} /> : <Empty>Carte indisponible (serveur injoignable).</Empty>}
        <div className="mt-3 rounded-xl bg-ink-50 p-3">
          <div className="mb-2 text-sm font-medium text-ink-900">
            Couleur d'une parcelle = part de l'eau demandée qu'elle reçoit dans le plan
            <InfoTip wide text="Exemple : une parcelle qui demande 100 m³ et en reçoit 48 est à 48 % : elle est en orange (manque d'eau). Le plan est calculé pour 2 jours." />
          </div>
          <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-sm text-ink-700">
            {LEGEND.map((l) => (
              <span key={l.label} className="flex items-center gap-1.5"><span className="h-3.5 w-3.5 rounded" style={{ background: l.color }} />{l.label} <span className="text-ink-400">({l.range})</span></span>
            ))}
            <span className="flex items-center gap-1.5"><span className="h-1 w-6 rounded bg-red-600" /> tuyau en rouge = fuite en cours</span>
          </div>
        </div>
      </Card>

      <div className="grid gap-4 md:grid-cols-3">
        {(farm?.farm?.plots ?? []).map((p: any) => {
          const id = p.id as PlotId
          const pr = usePlanSat ? plan?.plots?.[id] : undefined
          const soil = live.site === 'tunisia' ? live.latest.soil[id] : undefined
          const open = live.site === 'tunisia' ? live.actuators?.valves?.[id] : undefined
          const fallow = p.kc_effective === 0
          return (
            <Card key={id} title={<>{emoji(p.crop.name)} {plotName(id, crops)}</>} icon={<Sprout size={16} className="text-leaf-600" />}
              actions={fallow ? <Badge>au repos</Badge> : open !== undefined && <Badge tone={open ? 'water' : 'neutral'}>{open ? '💧 robinet ouvert' : 'robinet fermé'}</Badge>}>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-2 text-sm">
                <dt className="text-ink-500">Étape</dt><dd className="text-ink-900">{stageText(p.season_note)}</dd>
                <dt className="text-ink-500">Soif de la plante<InfoTip text="Plus la plante est grande et en pleine croissance, plus elle boit. Mesuré par le coefficient Kc (ici affiché entre parenthèses)." /></dt>
                <dd className="text-ink-900">{thirstText(p.kc_effective)} <span className="text-ink-400">({fr(p.kc_effective, 2)})</span></dd>
                <dt className="text-ink-500">Surface</dt><dd>{fr(p.area_ha, 1)} hectare ({fr(p.area_ha * 10000, 0)} m²)</dd>
                <dt className="text-ink-500">Arrosage</dt><dd>{METHOD_TEXT[p.irrigation_method] ?? p.irrigation_method}</dd>
                {soil !== undefined && (<><dt className="text-ink-500">Humidité du sol<InfoTip text="Part d'eau dans la terre. En dessous de 15 % environ, la terre est sèche ; au-dessus de 30 %, elle est bien humide." /></dt>
                  <dd className="text-water-700">{fr(soil * 100, 0)} %{soil < 0.15 ? ' · sèche' : soil > 0.3 ? ' · humide' : ''}</dd></>)}
              </dl>
              {pr && pr.need_m3 > 0 && (
                <div className="mt-3 rounded-xl bg-ink-50 p-3 text-sm">
                  <div className="flex items-center justify-between"><span className="font-medium text-ink-900">Plan sur 2 jours</span><SimLabel /></div>
                  <div className="mt-1 text-ink-700">demande {fr(pr.need_m3, 0)} m³ · reçoit <b>{fr(pr.base_served_m3, 0)} m³</b></div>
                  <div className="mt-2"><Bar value={pr.satisfaction} /></div>
                  <div className="mt-1 text-xs text-ink-500">{pct(pr.satisfaction)} — {satisfactionText(pr.satisfaction).label}</div>
                </div>
              )}
              {fallow && <p className="mt-3 text-sm text-ink-500">Hors saison : rien à arroser.</p>}
            </Card>
          )
        })}
      </div>
      {live.site === 'japan' && <p className="text-sm text-ink-500">Mode Japon : ferme de démonstration à Niigata (rizières). Les commandes et le direct restent ceux de la ferme tunisienne.</p>}
    </div>
  )
}
