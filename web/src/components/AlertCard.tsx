import { BrainCircuit, Droplet, Zap, Coins, Lock } from 'lucide-react'
import type { Alert } from '../lib/types'
import { Badge, fr } from './ui'

function when(a: Alert & { _time?: string }): string {
  const raw = a.received_at ?? a.ts ?? a._time
  const d = raw ? new Date(raw) : null
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleString('fr-FR') : '—'
}

export default function AlertCard({ a }: { a: Alert }) {
  const isolation = a.detector === 'isolation'
  return (
    <article className={`rounded-xl border p-3 ${a.active ? 'border-red-200 bg-red-50/60' : 'border-ink-200 bg-white'}`}>
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          {isolation ? <Lock size={16} className="text-ink-700" /> : <Droplet size={16} className={a.active ? 'text-red-600' : 'text-ink-400'} />}
          <span className="font-semibold text-ink-900">{isolation ? 'Isolement' : 'Fuite probable'} · {a.section}</span>
          {a.active ? <Badge tone="danger">en cours</Badge> : <Badge>terminée</Badge>}
          {!isolation && (a.confirmed_by_ai
            ? <Badge tone="water"><BrainCircuit size={12} /> confirmé par l'IA</Badge>
            : <Badge><BrainCircuit size={12} /> IA : non confirmé</Badge>)}
        </div>
        <time className="text-xs text-ink-500" title="heure de réception (temps réel)">{when(a)}</time>
      </header>
      {!isolation && (
        <div className="mt-2 grid grid-cols-3 gap-2 text-center">
          <div className="rounded-lg bg-white p-2 ring-1 ring-ink-100"><Droplet size={14} className="mx-auto text-water-600" /><div className="text-sm font-semibold tabular-nums">{fr(a.est_loss_m3_day, 1)}</div><div className="text-[11px] text-ink-500">m³ perdus / jour</div></div>
          <div className="rounded-lg bg-white p-2 ring-1 ring-ink-100"><Zap size={14} className="mx-auto text-sun-600" /><div className="text-sm font-semibold tabular-nums">{fr(a.est_loss_kwh_day, 1)}</div><div className="text-[11px] text-ink-500">kWh gaspillés / jour</div></div>
          <div className="rounded-lg bg-white p-2 ring-1 ring-ink-100"><Coins size={14} className="mx-auto text-leaf-600" /><div className="text-sm font-semibold tabular-nums">{fr(a.est_loss_tnd_day, 2)}</div><div className="text-[11px] text-ink-500">TND perdus / jour</div></div>
        </div>
      )}
      <p className="mt-2 text-xs leading-relaxed text-ink-700">{a.message}</p>
    </article>
  )
}
