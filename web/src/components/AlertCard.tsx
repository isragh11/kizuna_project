import { BrainCircuit, Lock } from 'lucide-react'
import { pipeName, type Crops } from '../lib/labels'
import type { Alert } from '../lib/types'
import { Badge, Details, InfoTip, fr } from './ui'

function when(a: Alert & { _time?: string }): string {
  const raw = a.received_at ?? a._time ?? a.ts
  const d = raw !== undefined ? new Date(raw) : null
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'
}

export default function AlertCard({ a, crops, onIsolate }: { a: Alert; crops: Crops; onIsolate?: () => void }) {
  const isolation = a.detector === 'isolation'
  const where = pipeName(a.section, crops)
  const unsure = a.section === 'S1'
  return (
    <article className={`rounded-2xl border p-4 ${a.active ? 'border-red-200 bg-red-50' : 'border-ink-200 bg-white'}`}>
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {isolation ? <Lock size={16} className="text-ink-700" /> : <span>💦</span>}
          <span className="font-semibold text-ink-900">{isolation ? `Parcelle coupée — ${where}` : `Fuite — ${where}`}</span>
          {a.active ? <Badge tone="danger">en cours</Badge> : <Badge>terminée</Badge>}
          {!isolation && a.confirmed_by_ai && <Badge tone="water"><BrainCircuit size={12} /> confirmée par l'IA</Badge>}
        </div>
        <time className="text-xs text-ink-500" title="heure réelle">{when(a)}</time>
      </header>
      {!isolation && (
        <>
          {unsure && <p className="mt-1 text-xs text-ink-500">L'eau manque sur le tuyau principal, mais aucun tuyau de parcelle ne baisse nettement : fuite sur le tuyau principal, <b>ou</b> eau prélevée volontairement (nettoyage des filtres, nouveau robinet…).</p>}
          <div className="mt-3 grid grid-cols-3 gap-2 text-center">
            <div className="rounded-xl bg-white p-2 ring-1 ring-ink-100"><div className="text-lg font-bold tabular-nums text-water-700">{fr(a.est_loss_m3_day * 1000, 0)}</div><div className="text-[11px] text-ink-500">litres perdus / jour</div></div>
            <div className="rounded-xl bg-white p-2 ring-1 ring-ink-100"><div className="text-lg font-bold tabular-nums text-sun-600">{fr(a.est_loss_kwh_day, 1)}</div><div className="text-[11px] text-ink-500">kWh d'électricité / jour<InfoTip text="Électricité dépensée pour pomper l'eau qui se perd (≈ 0,4 kWh par m³ remonté du puits de 60 m)." /></div></div>
            <div className="rounded-xl bg-white p-2 ring-1 ring-ink-100"><div className="text-lg font-bold tabular-nums text-red-600">{fr(a.est_loss_tnd_day, 2)}</div><div className="text-[11px] text-ink-500">TND perdus / jour<InfoTip text="Eau perdue (0,20 TND/m³) + électricité (selon l'heure). Prix supposés : estimation." /></div></div>
          </div>
          {a.active && onIsolate && !unsure && (
            <button onClick={onIsolate} className="mt-3 w-full rounded-lg bg-red-600 py-2 text-sm font-semibold text-white hover:bg-red-700">Couper cette parcelle</button>
          )}
          <div className="mt-2"><Details summary="Détails">
            <p className="text-xs leading-relaxed text-ink-700">{a.message}</p>
          </Details></div>
        </>
      )}
      {isolation && <p className="mt-2 text-sm text-ink-700">La pompe a été arrêtée puis le robinet fermé. Rendez la main à l'IA quand la fuite est réparée.</p>}
    </article>
  )
}
