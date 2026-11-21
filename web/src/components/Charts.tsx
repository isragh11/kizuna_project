import { Area, Bar, CartesianGrid, ComposedChart, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { FlowPoint, OptimizeResult } from '../lib/types'

const C = { A: '#e2584d', B: '#7a9a3a', C: '#d98c1f', water: '#1d74cc', sun: '#f59e0b', grid: '#64708a', tank: '#5fb4f8' }
const tick = { fontSize: 11, fill: '#64708a' }

/** Plan horaire : barres empilées par parcelle (m³), solaire disponible/utilisé et réseau (kWh). */
export function PlanChart({ plan, height = 300 }: { plan: OptimizeResult; height?: number }) {
  const start = new Date(plan.inputs?.start ?? Date.now())
  const data = plan.grid_kwh.map((_, t) => {
    const day = new Date(start.getTime() + t * 3600_000)
    return {
      h: `${t < 24 ? 'J1' : 'J2'} ${String(day.getHours()).padStart(2, '0')}h`,
      A: plan.allocation.A?.[t] ?? 0, B: plan.allocation.B?.[t] ?? 0, C: plan.allocation.C?.[t] ?? 0,
      pvDispo: plan.pv_available_kwh?.[t] ?? 0, pv: plan.pv_kwh[t], reseau: plan.grid_kwh[t], cuve: plan.tank_m3?.[t],
    }
  })
  const crop = (id: string) => plan.plots?.[id]?.crop ?? id
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid stroke="#eef0f4" vertical={false} />
        <XAxis dataKey="h" tick={tick} interval={5} />
        <YAxis yAxisId="m3" tick={tick} label={{ value: 'm³/h', angle: -90, position: 'insideLeft', offset: 20, style: tick }} />
        <YAxis yAxisId="kwh" orientation="right" tick={tick} label={{ value: 'kWh', angle: 90, position: 'insideRight', offset: 20, style: tick }} />
        <Tooltip formatter={(v: number) => v.toFixed(2)} contentStyle={{ borderRadius: 12, fontSize: 12 }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Area yAxisId="kwh" dataKey="pvDispo" name="Solaire disponible (kWh)" fill="#fef3c7" stroke="#fbbf24" strokeWidth={1} />
        <Bar yAxisId="m3" dataKey="A" stackId="x" name={`A · ${crop('A')}`} fill={C.A} />
        <Bar yAxisId="m3" dataKey="B" stackId="x" name={`B · ${crop('B')}`} fill={C.B} />
        <Bar yAxisId="m3" dataKey="C" stackId="x" name={`C · ${crop('C')}`} fill={C.C} radius={[3, 3, 0, 0]} />
        <Line yAxisId="kwh" dataKey="pv" name="Solaire utilisé (kWh)" stroke={C.sun} dot={false} strokeWidth={2} />
        <Line yAxisId="kwh" dataKey="reseau" name="Réseau (kWh)" stroke={C.grid} dot={false} strokeWidth={2} strokeDasharray="4 3" />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

export function TankChart({ plan, height = 160 }: { plan: OptimizeResult; height?: number }) {
  const data = (plan.tank_m3 ?? []).map((v, t) => ({ t: `${t < 24 ? 'J1' : 'J2'} ${t % 24}h`, cuve: v, pompage: plan.pump_m3?.[t] ?? 0 }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid stroke="#eef0f4" vertical={false} />
        <XAxis dataKey="t" tick={tick} interval={5} />
        <YAxis tick={tick} />
        <Tooltip formatter={(v: number) => v.toFixed(1)} contentStyle={{ borderRadius: 12, fontSize: 12 }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Area dataKey="cuve" name="Niveau de la cuve (m³)" stroke={C.water} fill="#dbeefe" />
        <Bar dataKey="pompage" name="Pompage puits → cuve (m³/h)" fill={C.tank} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/** Débits en direct : S1 mesuré, débit prévu par le plan, et somme des tronçons. */
export function LiveFlowChart({ series, height = 220 }: { series: FlowPoint[]; height?: number }) {
  const data = series.map((p) => ({ ...p, somme: p.S2 + p.S3 + p.S4 + p.S5 }))
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid stroke="#eef0f4" vertical={false} />
        <XAxis dataKey="t" tick={tick} minTickGap={30} />
        <YAxis tick={tick} />
        <Tooltip formatter={(v: number) => v.toFixed(2)} contentStyle={{ borderRadius: 12, fontSize: 12 }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line dataKey="S1" name="S1 mesuré (m³/h)" stroke={C.water} dot={false} strokeWidth={2} isAnimationActive={false} />
        <Line dataKey="somme" name="S2+S3+S4+S5 (m³/h)" stroke="#16803f" dot={false} strokeWidth={1.5} isAnimationActive={false} />
        <Line dataKey="plan" name="Plan S1 (m³/h)" stroke="#94a0b2" dot={false} strokeDasharray="4 3" isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  )
}

/** Déséquilibre du bilan de masse S1 − ΣS, avec le seuil de la règle (±0,5 m³/h). */
export function ImbalanceChart({ series, height = 200 }: { series: FlowPoint[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={series} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid stroke="#eef0f4" vertical={false} />
        <XAxis dataKey="t" tick={tick} minTickGap={30} />
        <YAxis tick={tick} domain={[-1, 'auto']} />
        <Tooltip formatter={(v: number) => v.toFixed(2)} contentStyle={{ borderRadius: 12, fontSize: 12 }} />
        <ReferenceLine y={0.5} stroke="#dc2626" strokeDasharray="5 4" label={{ value: 'seuil', fontSize: 10, fill: '#dc2626', position: 'right' }} />
        <ReferenceLine y={-0.5} stroke="#dc2626" strokeDasharray="5 4" />
        <Area dataKey="imbalance" name="Déséquilibre S1 − ΣS (m³/h)" stroke="#1d74cc" fill="#dbeefe" isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}

export function TankLiveChart({ series, height = 160 }: { series: FlowPoint[]; height?: number }) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={series} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid stroke="#eef0f4" vertical={false} />
        <XAxis dataKey="t" tick={tick} minTickGap={30} />
        <YAxis yAxisId="m3" tick={tick} />
        <YAxis yAxisId="kw" orientation="right" tick={tick} />
        <Tooltip formatter={(v: number) => v.toFixed(2)} contentStyle={{ borderRadius: 12, fontSize: 12 }} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Area yAxisId="m3" dataKey="tank" name="Cuve (m³)" stroke={C.water} fill="#dbeefe" isAnimationActive={false} />
        <Line yAxisId="kw" dataKey="pv" name="Solaire (kW)" stroke={C.sun} dot={false} strokeWidth={2} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  )
}
