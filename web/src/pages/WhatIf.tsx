// « Et si ? » : les curseurs recalculent un plan SIMULÉ (non appliqué) ; « Appliquer ce plan » est séparé
// et demande une confirmation.
import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, FlaskConical, Loader2, SlidersHorizontal } from 'lucide-react'
import { PlanChart } from '../components/Charts'
import Explanations from '../components/Explanations'
import { Badge, Button, Card, ConfirmDialog, Empty, Segmented, SimLabel, fr, pct } from '../components/ui'
import { apiSend, errorText } from '../lib/api'
import { useNotify } from '../lib/actions'
import { useLive } from '../lib/live'
import { useScenarios, type ScenarioInfo } from '../lib/usePlan'
import type { OptimizeResult, Site } from '../lib/types'
import { Comparison } from './PlanPage'

interface Params { quota: number; pv: number; temp: number; depth: number; smin: number; quotaMode: 'horizon' | 'daily' }
const DEFAULTS: Record<Site, Params> = {
  tunisia: { quota: 60, pv: 1, temp: 0, depth: 60, smin: 0, quotaMode: 'horizon' },
  japan: { quota: 150, pv: 1, temp: 0, depth: 15, smin: 0, quotaMode: 'horizon' },
}

function Slider({ label, value, min, max, step, unit, onChange, hint }: {
  label: string; value: number; min: number; max: number; step: number; unit: string; onChange: (v: number) => void; hint?: string
}) {
  return (
    <label className="block">
      <div className="flex items-baseline justify-between text-sm"><span className="text-ink-700">{label}</span><b className="tabular-nums text-ink-900">{fr(value, step < 1 ? (step < 0.1 ? 2 : 1) : 0)} {unit}</b></div>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} className="mt-1 w-full accent-water-600" />
      {hint && <div className="text-[11px] text-ink-400">{hint}</div>}
    </label>
  )
}

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
  // bascule Tunisie / Japon : scénario et paramètres par défaut du site
  useEffect(() => {
    const first = scenarios.find((s) => s.site === live.site)
    if (sc && sc.site !== live.site && first) setScenario(first.key)
    setP((cur) => ({ ...DEFAULTS[live.site], quotaMode: cur.quotaMode }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.site, scenarios.length])
  // choix d'un scénario : quota du scénario (ex. ÷ 2)
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
        if (r.ok) { setResult(r.data); setError(null) } else setError(errorText(r.data, `HTTP ${r.status}`))
      } catch {
        if (id === reqId.current) setError('API injoignable')
      } finally {
        if (id === reqId.current) setLoading(false)
      }
    }, 300) // anti-rebond de 300 ms
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(body)])

  const apply = async () => {
    if (!body) return
    const r = await apiSend('/optimize?apply=true', body).catch(() => null)
    if (r?.ok) notify(true, 'Plan appliqué : la ferme (simulateur) le suit maintenant')
    else notify(false, `Plan non appliqué : ${r ? errorText(r.data, `HTTP ${r.status}`) : 'API injoignable'}`)
  }

  return (
    <div className="grid gap-5 lg:grid-cols-3">
      <div className="min-w-0 space-y-5">
        <Card title="Paramètres" icon={<SlidersHorizontal size={16} className="text-water-600" />}>
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-sm text-ink-700">Site</span>
              <Segmented<Site> value={live.site} onChange={live.setSite} options={[{ value: 'tunisia', label: 'Tunisie' }, { value: 'japan', label: 'Japon' }]} />
            </div>
            <label className="block text-sm">
              <span className="text-ink-700">Scénario (journées réelles)</span>
              <select className="mt-1 w-full rounded-lg border border-ink-200 bg-white px-2 py-2" value={scenario} onChange={(e) => setScenario(e.target.value)}>
                {scenarios.filter((s) => s.site === live.site).map((s) => <option key={s.key} value={s.key}>{s.title}</option>)}
              </select>
              {sc && <span className="mt-1 block text-xs text-ink-500">{sc.description}</span>}
            </label>
            <Slider label="Quota d'eau" value={p.quota} min={0} max={live.site === 'japan' ? 250 : 120} step={5} unit="m³/j" onChange={(v) => setP({ ...p, quota: v })} />
            <Slider label="Ensoleillement" value={p.pv} min={0} max={1.5} step={0.05} unit="×" onChange={(v) => setP({ ...p, pv: v })} hint="1 = journée normale du mois ; 0,5 = très nuageux" />
            <Slider label="Température" value={p.temp} min={-5} max={8} step={0.5} unit="°C" onChange={(v) => setP({ ...p, temp: v })} hint="écart appliqué à l'ET0 (sensibilité de Hargreaves)" />
            <Slider label="Profondeur du puits" value={p.depth} min={5} max={150} step={5} unit="m" onChange={(v) => setP({ ...p, depth: v })} />
            <Slider label="Minimum garanti par parcelle (s_min)" value={p.smin} min={0} max={0.8} step={0.05} unit="" onChange={(v) => setP({ ...p, smin: v })} hint="relâché et signalé s'il est impossible" />
            <div className="flex items-center justify-between text-sm">
              <span className="text-ink-700">Quota</span>
              <Segmented value={p.quotaMode} onChange={(v) => setP({ ...p, quotaMode: v })}
                options={[{ value: 'horizon', label: 'cumulé 48 h' }, { value: 'daily', label: 'strict par jour' }]} />
            </div>
            <Button variant="ghost" size="sm" onClick={() => setP({ ...DEFAULTS[live.site], quota: DEFAULTS[live.site].quota * (sc?.quota_factor ?? 1) })}>Réinitialiser</Button>
          </div>
        </Card>
        <Card title="Appliquer" icon={<CheckCircle2 size={16} className="text-leaf-600" />}>
          <p className="text-sm text-ink-700">Ce plan est <b>simulé</b> : il ne change rien à la ferme tant qu'il n'est pas appliqué.</p>
          <Button variant="success" className="mt-3 w-full" disabled={!result || loading} onClick={() => setConfirm(true)}>Appliquer ce plan…</Button>
        </Card>
      </div>

      <div className="min-w-0 space-y-5 lg:col-span-2">
        <Card title="Plan simulé" icon={<FlaskConical size={16} className="text-sun-600" />}
          actions={<>{loading && <Loader2 size={16} className="animate-spin text-ink-400" />}<Badge tone="sun">non appliqué</Badge><SimLabel /></>}>
          {error ? <Empty>{error}</Empty> : result ? (
            <>
              <div className="mb-3 grid grid-cols-2 gap-2 text-center sm:grid-cols-4">
                {Object.entries(result.plots).map(([id, pl]) => (
                  <div key={id} className="rounded-xl bg-ink-50 p-2">
                    <div className="text-xs text-ink-500">{id} · {pl.crop}</div>
                    <div className={`text-lg font-semibold ${pl.satisfaction >= 0.95 ? 'text-leaf-600' : pl.satisfaction >= 0.5 ? 'text-sun-600' : 'text-red-600'}`}>{pct(pl.satisfaction)}</div>
                  </div>
                ))}
                <div className="rounded-xl bg-water-50 p-2">
                  <div className="text-xs text-ink-500">Facteur limitant</div>
                  <div className="text-lg font-semibold text-water-700">{{ water: 'eau', energy: 'énergie', pump: 'pompe', none: 'aucun' }[result.limiting_factor]}</div>
                </div>
              </div>
              <PlanChart plan={result} height={260} />
            </>
          ) : <Empty>Calcul en cours…</Empty>}
        </Card>
        {result && (
          <div className="grid gap-5 md:grid-cols-2">
            <Card title="Optimisé vs traditionnel"><Comparison plan={result} /></Card>
            <Card title="Explications"><Explanations items={result.explanations} max={6} /></Card>
          </div>
        )}
      </div>
      <ConfirmDialog open={confirm} onClose={() => setConfirm(false)} title="Appliquer ce plan ?"
        message={<>La ferme suivra ce plan simulé ({sc?.title}, quota {fr(p.quota, 0)} m³/j, ensoleillement ×{fr(p.pv, 2)}) à la place du plan automatique.
          Le plan automatique reprend avec « Recalculer » sur la page Plan IA.</>}
        confirmLabel="Appliquer" variant="success" onConfirm={apply} />
    </div>
  )
}
