// « Et si ? » : on bouge des curseurs → plan SIMULÉ (rien ne change à la ferme). « Appliquer » est séparé.
import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, FlaskConical, Loader2, SlidersHorizontal, CalendarClock, Scale } from 'lucide-react'
import { ScheduleGrid } from '../components/FarmCharts'
import { CropCards, OldVsNew, PlanSituation, RawExplanations } from '../components/PlanSummary'
import { Badge, Button, Card, ConfirmDialog, Empty, InfoTip, Segmented, SimLabel, fr } from '../components/ui'
import { apiSend, errorText } from '../lib/api'
import { useNotify } from '../lib/actions'
import { useLive } from '../lib/live'
import { useScenarios, type ScenarioInfo } from '../lib/usePlan'
import type { OptimizeResult, Site } from '../lib/types'

interface Params { quota: number; pv: number; temp: number; depth: number; smin: number; quotaMode: 'horizon' | 'daily' }
const DEFAULTS: Record<Site, Params> = {
  tunisia: { quota: 60, pv: 1, temp: 0, depth: 60, smin: 0, quotaMode: 'horizon' },
  japan: { quota: 150, pv: 1, temp: 0, depth: 15, smin: 0, quotaMode: 'horizon' },
}

function Slider({ label, value, display, min, max, step, onChange, help, marks }: {
  label: string; value: number; display: string; min: number; max: number; step: number; onChange: (v: number) => void; help: string; marks?: [string, string]
}) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between text-sm"><span className="text-ink-700">{label}<InfoTip text={help} /></span><b className="tabular-nums text-ink-900">{display}</b></div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="mt-1 w-full accent-water-600" />
      {marks && <div className="flex justify-between text-[11px] text-ink-400"><span>{marks[0]}</span><span>{marks[1]}</span></div>}
    </label>
  )
}

const sunText = (v: number) => (v < 0.4 ? 'très nuageux' : v < 0.8 ? 'nuageux' : v <= 1.1 ? 'normal' : 'très ensoleillé')

export default function WhatIf() {
  const live = useLive()
  const notify = useNotify()
  const scenarios = useScenarios()
  const [scenario, setScenario] = useState<string>(live.site === 'japan' ? 'japan_rice' : 'tunisia_heatwave')
  const [p, setP] = useState<Params>(DEFAULTS[live.site])
  const [result, setResult] = useState<OptimizeResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  const reqId = useRef(0)

  const sc: ScenarioInfo | undefined = scenarios.find((s) => s.key === scenario)
  useEffect(() => {
    const first = scenarios.find((s) => s.site === live.site)
    if (sc && sc.site !== live.site && first) setScenario(first.key)
    setP((cur) => ({ ...DEFAULTS[live.site], quotaMode: cur.quotaMode }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.site, scenarios.length])
  useEffect(() => {
    if (sc) setP((cur) => ({ ...cur, quota: DEFAULTS[sc.site].quota * sc.quota_factor }))
  }, [sc?.key]) // eslint-disable-line react-hooks/exhaustive-deps

  const body = sc && {
    site: sc.site, date: sc.start, horizon_h: 48, water_quota_m3: p.quota, pv_scale: p.pv, temp_offset_c: p.temp,
    well_depth_m: p.depth, s_min: p.smin, quota_mode: p.quotaMode,
  }

  useEffect(() => {
    if (!body) return
    const id = ++reqId.current
    setLoading(true)
    const t = setTimeout(async () => {
      try {
        const r = await apiSend<OptimizeResult>('/optimize', body)
        if (id !== reqId.current) return
        if (r.ok) { setResult(r.data); setError(null) } else setError(errorText(r.data, `Erreur ${r.status}`))
      } catch {
        if (id === reqId.current) setError('Serveur injoignable')
      } finally {
        if (id === reqId.current) setLoading(false)
      }
    }, 300)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(body)])

  const apply = async () => {
    if (!body) return
    const r = await apiSend('/optimize?apply=true', body).catch(() => null)
    if (r?.ok) notify(true, 'Plan appliqué : la ferme le suit maintenant')
    else notify(false, `Plan non appliqué : ${r ? errorText(r.data, `Erreur ${r.status}`) : 'serveur injoignable'}`)
  }

  return (
    <div className="grid gap-5 lg:grid-cols-3">
      <div className="min-w-0 space-y-5">
        <Card title="Et si… ?" icon={<SlidersHorizontal size={16} className="text-water-600" />}>
          <div className="space-y-4">
            <p className="text-sm text-ink-500">Bougez les curseurs : le plan se recalcule tout seul. <b>Rien ne change à la ferme</b> tant que vous n'appliquez pas.</p>
            <div className="flex items-center justify-between">
              <span className="text-sm text-ink-700">Pays</span>
              <Segmented<Site> value={live.site} onChange={live.setSite} options={[{ value: 'tunisia', label: 'Tunisie' }, { value: 'japan', label: 'Japon' }]} />
            </div>
            <label className="block text-sm">
              <span className="text-ink-700">Journées de départ (météo réelle)</span>
              <select className="mt-1 w-full rounded-lg border border-ink-200 bg-white px-2 py-2" value={scenario} onChange={(e) => setScenario(e.target.value)}>
                {scenarios.filter((s) => s.site === live.site).map((s) => <option key={s.key} value={s.key}>{s.title}</option>)}
              </select>
            </label>
            <Slider label="Eau autorisée par jour" value={p.quota} display={`${fr(p.quota, 0)} m³`} min={0} max={live.site === 'japan' ? 250 : 120} step={5}
              onChange={(v) => setP({ ...p, quota: v })} help="Le quota : l'eau que vous avez le droit de pomper chaque jour. Baissez-le pour simuler une coupure d'eau." marks={['coupure', 'abondante']} />
            <Slider label="Soleil" value={p.pv} display={sunText(p.pv)} min={0} max={1.5} step={0.05}
              onChange={(v) => setP({ ...p, pv: v })} help="Moins de soleil = moins d'électricité gratuite pour la pompe : il faut acheter plus au réseau." marks={['très nuageux', 'très ensoleillé']} />
            <Slider label="Chaleur en plus" value={p.temp} display={`${p.temp > 0 ? '+' : ''}${fr(p.temp, 1)} °C`} min={-5} max={8} step={0.5}
              onChange={(v) => setP({ ...p, temp: v })} help="Plus il fait chaud, plus les plantes boivent." marks={['plus frais', 'plus chaud']} />
            <Slider label="Profondeur du puits" value={p.depth} display={`${fr(p.depth, 0)} m`} min={5} max={150} step={5}
              onChange={(v) => setP({ ...p, depth: v })} help="Plus le puits est profond, plus il faut d'électricité pour remonter l'eau." />
            <Slider label="Minimum garanti par culture" value={p.smin} display={`${fr(p.smin * 100, 0)} %`} min={0} max={0.8} step={0.05}
              onChange={(v) => setP({ ...p, smin: v })} help="Imposer qu'aucune culture ne reçoive moins que ce pourcentage de ses besoins. S'il n'y a pas assez d'eau, l'IA le signale." />
            <div className="flex items-center justify-between text-sm">
              <span className="text-ink-700">Règle du quota<InfoTip text="« Sur 2 jours » : on peut prendre un peu plus un jour et moins le lendemain (la cuve stocke). « Chaque jour » : jamais plus que le quota dans une même journée." /></span>
              <Segmented value={p.quotaMode} onChange={(v) => setP({ ...p, quotaMode: v })}
                options={[{ value: 'horizon', label: 'sur 2 jours' }, { value: 'daily', label: 'chaque jour' }]} />
            </div>
            <Button variant="ghost" size="sm" onClick={() => setP({ ...DEFAULTS[live.site], quota: DEFAULTS[live.site].quota * (sc?.quota_factor ?? 1) })}>Revenir aux valeurs normales</Button>
          </div>
        </Card>
        <Card title="Appliquer ce plan à la ferme" icon={<CheckCircle2 size={16} className="text-leaf-600" />}>
          <p className="text-sm text-ink-700">Pour l'instant, ce plan est seulement <b>simulé</b>.</p>
          <Button variant="success" className="mt-3 w-full" disabled={!result || loading} onClick={() => setConfirm(true)}>Appliquer ce plan…</Button>
        </Card>
      </div>

      <div className="min-w-0 space-y-5 lg:col-span-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-lg font-semibold text-ink-900"><FlaskConical size={18} className="text-sun-600" /> Résultat simulé</div>
          <div className="flex items-center gap-2">{loading && <Loader2 size={16} className="animate-spin text-ink-400" />}<Badge tone="sun">non appliqué</Badge><SimLabel /></div>
        </div>
        {error ? <Empty>{error}</Empty> : result ? (
          <>
            <PlanSituation plan={result} />
            <CropCards plan={result} crops={{}} />
            <Card title="Quand pompe-t-on et quand arrose-t-on ?" icon={<CalendarClock size={16} className="text-water-600" />}><ScheduleGrid plan={result} crops={{}} /></Card>
            <Card title="À l'ancienne ou avec AquaMind ?" icon={<Scale size={16} className="text-leaf-600" />}><OldVsNew plan={result} /></Card>
            <RawExplanations plan={result} />
          </>
        ) : <Empty>Calcul en cours…</Empty>}
      </div>
      <ConfirmDialog open={confirm} onClose={() => setConfirm(false)} title="Appliquer ce plan ?"
        message={<>La ferme suivra ce plan simulé ({sc?.title}, {fr(p.quota, 0)} m³ par jour, soleil {sunText(p.pv)}) à la place du plan automatique.
          Pour revenir en arrière : page « Plan d'arrosage » → « Revenir à la vraie météo ».</>}
        confirmLabel="Appliquer" variant="success" onConfirm={apply} />
    </div>
  )
}
