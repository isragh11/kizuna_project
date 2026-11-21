// Explications de l'optimiseur, mises en forme : une icône et une couleur par type de phrase.
import { AlertTriangle, BarChart3, Droplets, Gauge, Leaf, Shield, Sun, Warehouse, Info } from 'lucide-react'
import type { ReactNode } from 'react'

function kind(text: string): { icon: ReactNode; tone: string; title?: string } {
  if (/^Facteur limitant|^Aucune ressource|^Les besoins prévus sont couverts/.test(text))
    return { icon: <Gauge size={16} />, tone: 'border-water-200 bg-water-50 text-water-900', title: 'Ressource limitante' }
  if (/RATIONNÉE/.test(text)) return { icon: <AlertTriangle size={16} />, tone: 'border-sun-400/40 bg-sun-50 text-ink-900' }
  if (/^Parcelle/.test(text)) return { icon: <Leaf size={16} />, tone: 'border-leaf-100 bg-leaf-50 text-ink-900' }
  if (/^Valeur de l'eau par paliers|^Le dernier m³/.test(text)) return { icon: <BarChart3 size={16} />, tone: 'border-ink-100 bg-white text-ink-900' }
  if (/^Marge/.test(text)) return { icon: <Shield size={16} />, tone: 'border-ink-100 bg-white text-ink-900' }
  if (/^Pompage|^Anticipation/.test(text)) return { icon: <Sun size={16} />, tone: 'border-sun-100 bg-white text-ink-900' }
  if (/^Réservoir/.test(text)) return { icon: <Warehouse size={16} />, tone: 'border-ink-100 bg-white text-ink-900' }
  if (/^Comparé/.test(text)) return { icon: <Droplets size={16} />, tone: 'border-leaf-100 bg-white text-ink-900' }
  return { icon: <Info size={16} />, tone: 'border-ink-100 bg-ink-50 text-ink-700' }
}

function emphasize(text: string): ReactNode {
  const parts = text.split(/(RATIONNÉE[^.:(]*|Facteur limitant : l'[A-ZÉ]+|Pourquoi :|\d+[,.]?\d* ?%|estimation simulée)/g)
  return parts.map((p, i) => (i % 2 === 1 ? <strong key={i} className="font-semibold">{p}</strong> : p))
}

export default function Explanations({ items, max }: { items: string[]; max?: number }) {
  const list = max ? items.slice(0, max) : items
  return (
    <ul className="space-y-2">
      {list.map((t, i) => {
        const k = kind(t)
        return (
          <li key={i} className={`flex gap-2.5 rounded-xl border px-3 py-2.5 text-sm leading-relaxed ${k.tone}`}>
            <span className="mt-0.5 shrink-0 opacity-80">{k.icon}</span>
            <span>{emphasize(t)}</span>
          </li>
        )
      })}
    </ul>
  )
}
