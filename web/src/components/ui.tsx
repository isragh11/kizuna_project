import { useEffect, useState, type ReactNode } from 'react'
import { X } from 'lucide-react'

export const fr = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined || Number.isNaN(v)
    ? '—'
    : v.toLocaleString('fr-FR', { minimumFractionDigits: digits, maximumFractionDigits: digits })

export const pct = (v: number | null | undefined, digits = 0) => (v === null || v === undefined ? '—' : `${fr(v * 100, digits)} %`)

export function Card({ title, icon, actions, children, className = '', tone }: {
  title?: ReactNode; icon?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; tone?: 'danger' | 'warn'
}) {
  const ring = tone === 'danger' ? 'ring-1 ring-red-200' : tone === 'warn' ? 'ring-1 ring-sun-400/40' : ''
  return (
    <section className={`min-w-0 rounded-2xl border border-ink-200/70 bg-white shadow-card ${ring} ${className}`}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-ink-100 px-4 py-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-ink-900">{icon}{title}</h2>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  )
}

type Variant = 'primary' | 'secondary' | 'danger' | 'ghost' | 'success' | 'warn'
const VARIANTS: Record<Variant, string> = {
  primary: 'bg-water-600 text-white hover:bg-water-700 focus-visible:ring-water-400',
  secondary: 'bg-white text-ink-700 ring-1 ring-ink-200 hover:bg-ink-50 focus-visible:ring-water-400',
  danger: 'bg-red-600 text-white hover:bg-red-700 focus-visible:ring-red-400',
  ghost: 'text-ink-700 hover:bg-ink-100',
  success: 'bg-leaf-600 text-white hover:bg-leaf-700 focus-visible:ring-leaf-400',
  warn: 'bg-sun-500 text-white hover:bg-sun-600 focus-visible:ring-sun-400',
}

export function Button({ children, variant = 'secondary', size = 'md', className = '', ...rest }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant; size?: 'sm' | 'md'
}) {
  const sz = size === 'sm' ? 'px-2.5 py-1.5 text-xs' : 'px-3.5 py-2 text-sm'
  return (
    <button
      className={`inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition focus-visible:outline-none focus-visible:ring-2 disabled:cursor-not-allowed disabled:opacity-50 ${sz} ${VARIANTS[variant]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  )
}

export function Badge({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'ok' | 'warn' | 'danger' | 'water' | 'sun' }) {
  const t = {
    neutral: 'bg-ink-100 text-ink-700', ok: 'bg-leaf-100 text-leaf-700', warn: 'bg-sun-100 text-sun-600',
    danger: 'bg-red-100 text-red-700', water: 'bg-water-100 text-water-700', sun: 'bg-sun-50 text-sun-600',
  }[tone]
  return <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${t}`}>{children}</span>
}

export function SimLabel({ text = 'estimation simulée' }: { text?: string }) {
  return <span className="rounded border border-dashed border-ink-200 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-ink-500">{text}</span>
}

export function Stat({ label, value, unit, sub, icon, tone = 'water' }: {
  label: string; value: ReactNode; unit?: string; sub?: ReactNode; icon?: ReactNode; tone?: 'water' | 'sun' | 'leaf' | 'danger' | 'ink'
}) {
  const c = { water: 'text-water-600 bg-water-50', sun: 'text-sun-600 bg-sun-50', leaf: 'text-leaf-600 bg-leaf-50', danger: 'text-red-600 bg-red-50', ink: 'text-ink-700 bg-ink-100' }[tone]
  return (
    <div className="flex items-start gap-3 rounded-2xl border border-ink-200/70 bg-white p-4 shadow-card">
      {icon && <div className={`rounded-xl p-2 ${c}`}>{icon}</div>}
      <div className="min-w-0">
        <div className="text-xs font-medium text-ink-500">{label}</div>
        <div className="mt-0.5 text-xl font-semibold tabular-nums text-ink-900">
          {value}{unit && <span className="ml-1 text-sm font-medium text-ink-500">{unit}</span>}
        </div>
        {sub && <div className="mt-0.5 text-xs text-ink-500">{sub}</div>}
      </div>
    </div>
  )
}

export function Modal({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    if (open) window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[2000] flex items-end justify-center bg-ink-900/40 p-4 sm:items-center" onClick={onClose}>
      <div role="dialog" aria-modal="true" className="w-full max-w-md rounded-2xl bg-white shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between border-b border-ink-100 px-5 py-3">
          <h3 className="font-semibold text-ink-900">{title}</h3>
          <button className="rounded p-1 text-ink-500 hover:bg-ink-100" onClick={onClose} aria-label="Fermer"><X size={18} /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  )
}

export function ConfirmDialog({ open, title, message, confirmLabel, variant = 'primary', onConfirm, onClose, requireReason }: {
  open: boolean; title: string; message: ReactNode; confirmLabel: string; variant?: Variant
  onConfirm: (reason: string) => void; onClose: () => void; requireReason?: string
}) {
  const [reason, setReason] = useState('')
  useEffect(() => { if (open) setReason('') }, [open])
  const blocked = !!requireReason && reason.trim().length < 3
  return (
    <Modal open={open} title={title} onClose={onClose}>
      <div className="space-y-4 text-sm text-ink-700">
        <div>{message}</div>
        {requireReason && (
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-ink-500">{requireReason}</span>
            <textarea className="w-full rounded-lg border border-ink-200 p-2 text-sm focus:border-water-400 focus:outline-none" rows={2}
              value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Motif (obligatoire)" autoFocus />
          </label>
        )}
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Annuler</Button>
          <Button variant={variant} disabled={blocked} onClick={() => { onConfirm(reason.trim()); onClose() }}>{confirmLabel}</Button>
        </div>
      </div>
    </Modal>
  )
}

export function Toast({ toast, onClose }: { toast: { ok: boolean; text: string } | null; onClose: () => void }) {
  useEffect(() => { if (toast) { const t = setTimeout(onClose, 6000); return () => clearTimeout(t) } }, [toast, onClose])
  if (!toast) return null
  return (
    <div className={`fixed bottom-4 left-1/2 z-[2100] w-[min(92vw,560px)] -translate-x-1/2 rounded-xl px-4 py-3 text-sm shadow-lg ${toast.ok ? 'bg-ink-900 text-white' : 'bg-red-600 text-white'}`}
      role="status" onClick={onClose}>
      {toast.text}
    </div>
  )
}

export function Segmented<T extends string>({ value, options, onChange }: { value: T; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex rounded-lg bg-ink-100 p-0.5">
      {options.map((o) => (
        <button key={o.value} onClick={() => onChange(o.value)}
          className={`rounded-md px-3 py-1.5 text-xs font-medium transition ${value === o.value ? 'bg-white text-ink-900 shadow' : 'text-ink-500 hover:text-ink-700'}`}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/** Petite bulle « ? » qui explique un mot ou un chiffre (survol ou clic). */
export function InfoTip({ text, wide = false }: { text: ReactNode; wide?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <span className="relative inline-flex align-middle" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" aria-label="Explication" onClick={(e) => { e.stopPropagation(); setOpen((o) => !o) }}
        className="ml-1 inline-flex h-4 w-4 items-center justify-center rounded-full bg-ink-200 text-[10px] font-bold text-ink-700 hover:bg-water-100 hover:text-water-700">?</button>
      {open && (
        <span className={`absolute left-1/2 top-5 z-[1200] -translate-x-1/2 rounded-lg bg-ink-900 px-3 py-2 text-xs font-normal leading-snug text-white shadow-lg ${wide ? 'w-72' : 'w-56'}`}>
          {text}
        </span>
      )}
    </span>
  )
}

/** Barre de progression simple, avec couleur selon le niveau. */
export function Bar({ value, tone }: { value: number; tone?: 'auto' | 'water' | 'sun' }) {
  const v = Math.max(0, Math.min(1, value))
  const color = tone === 'water' ? 'bg-water-500' : tone === 'sun' ? 'bg-sun-500'
    : v >= 0.95 ? 'bg-leaf-500' : v >= 0.6 ? 'bg-lime-500' : v >= 0.35 ? 'bg-sun-500' : 'bg-red-500'
  return (
    <div className="h-2.5 w-full overflow-hidden rounded-full bg-ink-100">
      <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${v * 100}%` }} />
    </div>
  )
}

export function Details({ summary, children }: { summary: ReactNode; children: ReactNode }) {
  return (
    <details className="group rounded-2xl border border-ink-200/70 bg-white shadow-card">
      <summary className="cursor-pointer list-none px-4 py-3 text-sm font-medium text-ink-700 hover:bg-ink-50">
        <span className="mr-1 inline-block transition group-open:rotate-90">▸</span>{summary}
      </summary>
      <div className="border-t border-ink-100 p-4">{children}</div>
    </details>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-xl border border-dashed border-ink-200 p-6 text-center text-sm text-ink-500">{children}</div>
}
