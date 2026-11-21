import { useEffect, useState } from 'react'
import { Map as MapIcon, Sprout, Droplet } from 'lucide-react'
import FarmMap, { type MapPlot } from '../components/FarmMap'
import { Badge, Card, Empty, SimLabel, fr, pct } from '../components/ui'
import { apiGet } from '../lib/api'
import { useLive } from '../lib/live'
import { usePlan } from '../lib/usePlan'
import { SECTION_OF, type PlotId } from '../lib/types'

const METHOD: Record<string, string> = { drip: 'goutte-à-goutte', sprinkler: 'aspersion', flood: 'submersion' }

export default function Farm() {
  const live = useLive()
  const { plan } = usePlan()
  const [farm, setFarm] = useState<any>(null)
  useEffect(() => {
    apiGet<any>(`/farm?site=${live.site}`).then((r) => setFarm(r.data)).catch(() => setFarm(null))
  }, [live.site])

  const usePlanSat = live.site === 'tunisia' && plan?.inputs?.site !== 'japan'
  const alertSections = new Set(live.alerts.filter((a) => a.active).map((a) => a.section))
  const plots: MapPlot[] = (farm?.farm?.plots ?? []).map((p: any) => ({
    id: p.id, lat: p.lat, lon: p.lon, area_ha: p.area_ha, crop: p.crop.name,
    satisfaction: usePlanSat ? plan?.satisfaction?.[p.id] : undefined,
    fallow: p.kc_effective === 0,
    valveOpen: live.site === 'tunisia' ? live.actuators?.valves?.[p.id as PlotId] : undefined,
  }))

  return (
    <div className="space-y-5">
      <Card title={`Ferme — ${farm?.farm?.name ?? '…'}`} icon={<MapIcon size={16} className="text-water-600" />}
        actions={<span className="text-xs text-ink-500">culture et stade au {farm?.date ?? '—'} (calendrier cultural)</span>}>
        {plots.length ? <FarmMap plots={plots} alertSections={alertSections} /> : <Empty>Carte indisponible (API hors-ligne et pas de cache).</Empty>}
        <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-ink-500">
          <span className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-leaf-500" /> ≥ 95 %</span>
          <span className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-lime-500" /> 60–95 %</span>
          <span className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-sun-500" /> 35–60 %</span>
          <span className="flex items-center gap-1"><span className="h-3 w-3 rounded bg-red-600" /> &lt; 35 % (satisfaction du plan)</span>
          <span className="flex items-center gap-1"><span className="h-1 w-5 rounded bg-red-600" /> tronçon en alerte</span>
        </div>
      </Card>

      <div className="grid gap-4 md:grid-cols-3">
        {(farm?.farm?.plots ?? []).map((p: any) => {
          const pr = usePlanSat ? plan?.plots?.[p.id] : undefined
          const soil = live.site === 'tunisia' ? live.latest.soil[p.id] : undefined
          const open = live.site === 'tunisia' ? live.actuators?.valves?.[p.id as PlotId] : undefined
          const fallow = p.kc_effective === 0
          return (
            <Card key={p.id} title={`Parcelle ${p.id} · ${p.crop.name}`} icon={<Sprout size={16} className="text-leaf-600" />}
              actions={fallow ? <Badge>jachère</Badge> : open !== undefined && <Badge tone={open ? 'water' : 'neutral'}>vanne {open ? 'ouverte' : 'fermée'}</Badge>}>
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-sm">
                <dt className="text-ink-500">Stade</dt><dd className="text-ink-900">{p.season_note ?? p.growth_stage}</dd>
                <dt className="text-ink-500">Kc du jour</dt><dd className="tabular-nums">{fr(p.kc_effective, 2)}</dd>
                <dt className="text-ink-500">Surface</dt><dd>{fr(p.area_ha, 1)} ha</dd>
                <dt className="text-ink-500">Irrigation</dt><dd>{METHOD[p.irrigation_method] ?? p.irrigation_method} ({pct(p.irrigation_efficiency)})</dd>
                <dt className="text-ink-500">Tronçon</dt><dd>{SECTION_OF[p.id as PlotId]}</dd>
                <dt className="text-ink-500">Ky · valeur</dt><dd>{fr(p.crop.ky, 2)} · {fr(p.crop.value_tnd_per_ha, 0)} TND/ha</dd>
                {soil !== undefined && (<><dt className="text-ink-500">Humidité du sol</dt><dd className="flex items-center gap-1 text-water-700"><Droplet size={13} />{fr(soil * 100, 1)} %</dd></>)}
              </dl>
              {pr && pr.need_m3 > 0 && (
                <div className="mt-3 rounded-xl bg-ink-50 p-3 text-sm">
                  <div className="flex items-center justify-between"><span className="text-ink-500">Plan sur 48 h</span><SimLabel /></div>
                  <div className="mt-1">Besoin {fr(pr.need_m3, 1)} m³ · servi {fr(pr.base_served_m3, 1)} m³</div>
                  <div className="mt-2 h-2 overflow-hidden rounded-full bg-ink-200">
                    <div className="h-full rounded-full bg-leaf-500" style={{ width: `${Math.min(100, pr.satisfaction * 100)}%` }} />
                  </div>
                  <div className="mt-1 text-xs text-ink-500">satisfaction {pct(pr.satisfaction)} · paliers survie/moyen/confort {pr.tranche_fill.map((f) => pct(f)).join(' / ')}</div>
                </div>
              )}
            </Card>
          )
        })}
      </div>
      {live.site === 'japan' && <p className="text-xs text-ink-500">Mode Japon : ferme de démonstration à Niigata (riz). Le flux en direct et les actionneurs restent ceux de la ferme tunisienne.</p>}
    </div>
  )
}
