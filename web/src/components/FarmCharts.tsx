// Graphiques simples pour l'agriculteur : peu de courbes, grandes étiquettes, une phrase d'aide.
import { Area, AreaChart, CartesianGrid, Line, ComposedChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { FlowPoint, OptimizeResult } from '../lib/types'
import { PLOT_COLOR, emoji, plotName, type Crops } from '../lib/labels'
import { PLOTS } from '../lib/types'
import { fr } from './ui'

const tick = { fontSize: 12, fill: '#64708a' }
const tip = { borderRadius: 12, fontSize: 13 }

/** Eau qui part de la cuve vers les cultures vs eau qui arrive aux cultures (m³/h). Sans fuite : superposées. */
export function WaterFlowChart({ series, height = 230 }: { series: FlowPoint[]; height?: number }) {
  const data = series.map((p) => ({
    t: p.t,
    partie: Math.max(0, p.S1 - p.S5),
    arrivee: p.S2 + p.S3 + p.S4,
  }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 10, right: 12, left: -6, bottom: 0 }}>
        <CartesianGrid stroke="#eef0f4" vertical={false} />
        <XAxis dataKey="t" tick={tick} minTickGap={50} />
        <YAxis tick={tick} width={42} allowDecimals={false} unit="" />
        <Tooltip contentStyle={tip} formatter={(v: number, n: string) => [`${fr(v, 1)} m³/h (${fr(v * 1000 / 60, 0)} L/min)`, n]} labelFormatter={(l) => `Heure de la ferme : ${l}`} />
        <Area dataKey="partie" name="Eau partie de la cuve" stroke="#1d74cc" strokeWidth={2.5} fill="#dbeefe" isAnimationActive={false} />
        <Line dataKey="arrivee" name="Eau arrivée aux cultures" stroke="#16803f" strokeWidth={2.5} dot={false} strokeDasharray="6 4" isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/** Eau perdue en ce moment (m³/h), avec le seuil d'alerte. Le bruit négatif est ramené à 0. */
export function LossChart({ series, height = 200 }: { series: FlowPoint[]; height?: number }) {
  const data = series.map((p) => ({ t: p.t, perte: Math.max(0, p.imbalance) }))
  const max = Math.max(1, ...data.map((d) => d.perte))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 10, right: 12, left: -6, bottom: 0 }}>
        <CartesianGrid stroke="#eef0f4" vertical={false} />
        <XAxis dataKey="t" tick={tick} minTickGap={50} />
        <YAxis tick={tick} width={42} domain={[0, Math.ceil(max * 1.2 * 2) / 2]} />
        <Tooltip contentStyle={tip} formatter={(v: number) => [`${fr(v, 2)} m³/h perdus (${fr(v * 1000 / 60, 0)} L/min)`, 'Perte']} labelFormatter={(l) => `Heure de la ferme : ${l}`} />
        <ReferenceLine y={0.5} stroke="#dc2626" strokeDasharray="6 4" strokeWidth={2}
          label={{ value: "seuil d'alerte", position: 'insideTopRight', fill: '#dc2626', fontSize: 12 }} />
        <Area dataKey="perte" name="Eau perdue" stroke="#dc2626" strokeWidth={2} fill="#fee2e2" isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

/** Niveau de la cuve dans le plan (m³). */
export function TankPlanChart({ plan, height = 170 }: { plan: OptimizeResult; height?: number }) {
  const data = (plan.tank_m3 ?? []).map((v, t) => ({ h: `${t < 24 ? 'J1' : 'J2'} ${t % 24}h`, cuve: v }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 12, left: -6, bottom: 0 }}>
        <CartesianGrid stroke="#eef0f4" vertical={false} />
        <XAxis dataKey="h" tick={tick} interval={11} />
        <YAxis tick={tick} width={42} />
        <Tooltip contentStyle={tip} formatter={(v: number) => [`${fr(v, 0)} m³`, 'Eau dans la cuve']} />
        <Area dataKey="cuve" stroke="#1d74cc" strokeWidth={2.5} fill="#dbeefe" isAnimationActive={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

/** Planning façon « emploi du temps » : quelles heures on pompe et on arrose, sur 2 jours. */
export function ScheduleGrid({ plan, crops }: { plan: OptimizeResult; crops: Crops }) {
  const H = plan.horizon_h
  const days = Array.from({ length: H / 24 }, (_, d) => d)
  const start = plan.inputs?.start ? new Date(plan.inputs.start) : null
  const dayLabel = (d: number) =>
    start ? new Date(start.getTime() + d * 86400000).toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' }) : `Jour ${d + 1}`
  const rows: { key: string; label: string; values: number[]; color: (t: number, v: number) => string; unit: string }[] = [
    {
      key: 'pump', label: '☀️ Pompe du puits → cuve', values: plan.pump_m3 ?? [], unit: 'm³ pompés',
      color: (t, v) => (v < 0.05 ? '' : plan.grid_kwh[t] > (plan.pv_kwh[t] ?? 0) ? '#94a0b2' : '#f5b301'),
    },
    ...PLOTS.filter((id) => plan.allocation[id]).map((id) => ({
      key: id, label: `${emoji(crops[id] ?? plan.plots?.[id]?.crop)} ${plotName(id, { [id]: crops[id] ?? plan.plots?.[id]?.crop })}`,
      values: plan.allocation[id], unit: 'm³ arrosés', color: (_t: number, v: number) => (v < 0.05 ? '' : PLOT_COLOR[id]),
    })),
  ]
  return (
    <div className="space-y-4">
      {days.map((d) => (
        <div key={d}>
          <div className="mb-1.5 text-sm font-semibold capitalize text-ink-900">{dayLabel(d)}</div>
          <div className="overflow-x-auto">
            <div className="min-w-[560px]">
              <div className="ml-[150px] grid grid-cols-24 text-[10px] text-ink-400" style={{ gridTemplateColumns: 'repeat(24, minmax(0, 1fr))' }}>
                {Array.from({ length: 24 }, (_, h) => <div key={h} className="text-center">{h % 3 === 0 ? `${h}h` : ''}</div>)}
              </div>
              {rows.map((r) => (
                <div key={r.key} className="flex items-center">
                  <div className="w-[150px] shrink-0 truncate pr-2 text-xs text-ink-700">{r.label}</div>
                  <div className="grid flex-1 gap-px" style={{ gridTemplateColumns: 'repeat(24, minmax(0, 1fr))' }}>
                    {Array.from({ length: 24 }, (_, h) => {
                      const t = d * 24 + h
                      const v = r.values[t] ?? 0
                      const c = r.color(t, v)
                      return (
                        <div key={h} title={`${h}h : ${fr(v, 1)} ${r.unit}`}
                          className={`h-6 ${h === 0 ? 'rounded-l' : ''} ${h === 23 ? 'rounded-r' : ''}`}
                          style={{ background: c || (h >= 6 && h < 19 ? '#fffbeb' : '#f1f3f7'), opacity: c ? Math.min(1, 0.35 + v / 8) : 1 }} />
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      ))}
      <div className="flex flex-wrap gap-4 text-xs text-ink-500">
        <span className="flex items-center gap-1"><span className="h-3 w-5 rounded bg-[#f5b301]" /> pompe à l'énergie solaire</span>
        <span className="flex items-center gap-1"><span className="h-3 w-5 rounded bg-[#94a0b2]" /> pompe sur le réseau électrique</span>
        <span className="flex items-center gap-1"><span className="h-3 w-5 rounded bg-[#fffbeb] ring-1 ring-ink-200" /> heures de jour</span>
        <span>Plus la case est foncée, plus il y a d'eau.</span>
      </div>
    </div>
  )
}

/** D'où vient l'électricité de la pompe, jour par jour (soleil / réseau) et ce qu'elle coûte. */
export function EnergyDays({ plan }: { plan: OptimizeResult }) {
  const days = Array.from({ length: plan.horizon_h / 24 }, (_, d) => d)
  const price = (t: number) => { const h = t % 24; return h < 6 || h >= 22 ? 0.15 : h < 18 ? 0.22 : 0.35 }
  return (
    <div className="space-y-3">
      {days.map((d) => {
        const sl = (a: number[]) => a.slice(d * 24, (d + 1) * 24).reduce((s, v) => s + v, 0)
        const sun = sl(plan.pv_kwh), grid = sl(plan.grid_kwh)
        const cost = plan.grid_kwh.slice(d * 24, (d + 1) * 24).reduce((s, v, h) => s + v * price(d * 24 + h), 0)
        const total = sun + grid || 1
        return (
          <div key={d}>
            <div className="mb-1 flex justify-between text-sm"><span className="font-medium text-ink-900">Jour {d + 1}</span>
              <span className="text-ink-500">{fr(sun + grid, 1)} kWh · réseau payé : <b className="text-ink-900">{fr(cost, 2)} TND</b></span></div>
            <div className="flex h-5 overflow-hidden rounded-full bg-ink-100 text-[11px] font-semibold text-white">
              {sun > 0.01 && <div className="flex items-center justify-center bg-sun-500" style={{ width: `${(sun / total) * 100}%` }}>☀️ {fr((sun / total) * 100, 0)} %</div>}
              {grid > 0.01 && <div className="flex items-center justify-center bg-ink-400" style={{ width: `${(grid / total) * 100}%` }}>🔌 {fr((grid / total) * 100, 0)} %</div>}
            </div>
          </div>
        )
      })}
      <p className="text-xs text-ink-500">☀️ soleil (gratuit) · 🔌 réseau électrique (payant : 0,15 TND/kWh la nuit, 0,22 le jour, 0,35 entre 18h et 22h — hypothèses)</p>
    </div>
  )
}
