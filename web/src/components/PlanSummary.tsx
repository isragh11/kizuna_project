// Le plan expliqué simplement, à partir des chiffres du plan (pas du texte) : situation, une carte par
// culture, le partage de l'eau en 3 étapes, et la comparaison « à l'ancienne / avec AquaMind ».
import { emoji, plotName, satisfactionText, stageText, LIMIT_TEXT, type Crops } from '../lib/labels'
import { PLOTS, type OptimizeResult, type PlotId } from '../lib/types'
import Explanations from './Explanations'
import { Bar, Details, InfoTip, fr, pct } from './ui'

const STEPS = [
  { name: 'Survie', range: '0 → 30 %', text: "Le minimum pour que la plante ne meure pas. Servi d'abord à TOUTES les cultures." },
  { name: 'Croissance', range: '30 → 70 %', text: 'Pour que la plante pousse correctement.' },
  { name: 'Confort', range: '70 → 100 %', text: 'Pour la meilleure récolte possible.' },
]

function whyText(p: OptimizeResult['plots'][string], limiting: string): string {
  if (p.need_m3 <= 0) return 'Rien à arroser : la parcelle est au repos ou la pluie suffit.'
  if (p.satisfaction >= 0.95) return 'Reçoit toute l\'eau dont elle a besoin.'
  const [s, m] = p.tranche_fill
  if (limiting !== 'water') return 'Reçoit moins à cause de la pompe ou de l\'électricité disponible.'
  if (s < 0.98) return "L'eau ne suffit même pas pour la survie de toutes les cultures : elle reçoit ce qu'il reste."
  if (m < 0.02) {
    return p.perennial
      ? "Reçoit le minimum vital (30 %) : les arbres sont protégés. Le reste de l'eau rapporte plus sur les autres cultures."
      : "Reçoit le minimum vital (30 %) : chaque m³ en plus rapporterait moins ici que sur les autres cultures."
  }
  return "Reçoit sa part de survie et une partie de la croissance : c'est là que l'eau disponible s'arrête."
}

export function PlanSituation({ plan }: { plan: OptimizeResult }) {
  const k = plan.kpis
  const missing = Math.max(0, k.need_m3 - k.quota_m3)
  const lim = LIMIT_TEXT[plan.limiting_factor] ?? LIMIT_TEXT.none
  return (
    <div className={`rounded-2xl border p-4 ${missing > 0.5 ? 'border-sun-400/50 bg-sun-50' : 'border-leaf-100 bg-leaf-50'}`}>
      <div className="text-base font-semibold text-ink-900">{missing > 0.5 ? `⚠️ ${lim.title}` : '✅ Il y a assez d\'eau'}</div>
      <p className="mt-1 text-sm text-ink-700">
        Sur 2 jours, vos cultures demandent <b>{fr(k.need_m3, 0)} m³</b> d'eau ({fr(k.need_m3, 0)} 000 litres) et vous avez droit à <b>{fr(k.quota_m3, 0)} m³</b>.
        {missing > 0.5
          ? <> Il manque <b>{fr(missing, 0)} m³</b> : l'IA partage l'eau pour sauver le plus de récolte possible.</>
          : <> Tout le monde est servi{k.water_pumped_m3 > k.need_m3 + 0.5 ? ', avec une petite réserve de sécurité' : ''}.</>}
      </p>
      {plan.limiting_factor === 'water' && plan.duals.water > 0.01 && (
        <p className="mt-1 text-sm text-ink-700">
          1 m³ d'eau en plus protégerait environ <b>{fr(plan.duals.water, 2)} TND</b> de récolte
          <InfoTip text="C'est la « valeur » d'un mètre cube supplémentaire. Si on vous propose de l'eau à un prix inférieur, cela vaut la peine de l'acheter." />.
        </p>
      )}
    </div>
  )
}

export function CropCards({ plan, crops }: { plan: OptimizeResult; crops: Crops }) {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {PLOTS.filter((id) => plan.plots[id]).map((id) => {
        const p = plan.plots[id]
        const crop = crops[id] ?? p.crop
        const st = satisfactionText(p.satisfaction)
        const tone = { ok: 'text-leaf-600', mid: 'text-lime-600', low: 'text-sun-600', bad: 'text-red-600' }[st.tone]
        return (
          <div key={id} className="rounded-2xl border border-ink-200/70 bg-white p-4 shadow-card">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="text-base font-semibold text-ink-900">{emoji(crop)} {plotName(id as PlotId, { [id]: crop })}</div>
                <div className="text-xs text-ink-500">{stageText(p.stage_note)}</div>
              </div>
              {p.need_m3 > 0 && <div className={`text-2xl font-bold tabular-nums ${tone}`}>{pct(p.satisfaction)}</div>}
            </div>
            {p.need_m3 > 0 ? (
              <>
                <div className="mt-3"><Bar value={p.satisfaction} /></div>
                <div className="mt-1.5 flex justify-between text-xs text-ink-500">
                  <span>reçoit <b className="text-ink-900">{fr(p.base_served_m3, 0)} m³</b></span>
                  <span>demande {fr(p.need_m3, 0)} m³</span>
                </div>
                <div className={`mt-2 text-xs font-semibold ${tone}`}>{st.label}</div>
                <p className="mt-1 text-sm leading-snug text-ink-700">{whyText(p, plan.limiting_factor)}</p>
                {p.value_lost_tnd > 0.5 && <p className="mt-1 text-xs text-ink-500">Perte de récolte estimée : {fr(p.value_lost_tnd, 0)} TND sur 2 jours</p>}
              </>
            ) : <p className="mt-3 text-sm text-ink-500">{whyText(p, plan.limiting_factor)}</p>}
          </div>
        )
      })}
    </div>
  )
}

/** Schéma du partage de l'eau en 3 étapes, avec où l'eau s'est arrêtée. */
export function SharingSteps({ plan, crops }: { plan: OptimizeResult; crops: Crops }) {
  const active = PLOTS.filter((id) => plan.plots[id] && plan.plots[id].need_m3 > 0)
  if (!active.length) return null
  return (
    <div>
      <p className="mb-3 text-sm text-ink-700">
        Quand l'eau manque, l'IA ne donne pas tout à la culture qui rapporte le plus. Elle remplit les besoins <b>dans cet ordre</b>,
        pour toutes les cultures : <InfoTip wide text="Les premiers litres sont les plus précieux : sans eau, la plante meurt. Les derniers litres améliorent seulement la récolte. Pour l'olivier, la survie compte 5 fois plus, car un arbre mort met des années à repousser." />
      </p>
      <div className="grid gap-2 sm:grid-cols-3">
        {STEPS.map((s, k) => (
          <div key={s.name} className="rounded-xl border border-ink-200 bg-white p-3">
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-water-600 text-xs font-bold text-white">{k + 1}</span>
              <span className="font-semibold text-ink-900">{s.name}</span>
              <span className="text-xs text-ink-500">{s.range}</span>
            </div>
            <p className="mt-1 text-xs text-ink-500">{s.text}</p>
            <div className="mt-2 space-y-1">
              {active.map((id) => {
                const f = plan.plots[id].tranche_fill[k] ?? 0
                return (
                  <div key={id} className="flex items-center gap-2 text-xs">
                    <span className="w-24 truncate">{emoji(crops[id] ?? plan.plots[id].crop)} {crops[id] ?? plan.plots[id].crop}</span>
                    <div className="flex-1"><Bar value={f} tone="water" /></div>
                    <span className="w-9 text-right tabular-nums">{fr(f * 100, 0)}%</span>
                  </div>
                )
              })}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export function OldVsNew({ plan }: { plan: OptimizeResult }) {
  const k = plan.kpis, b = plan.baseline_kpis
  const rows: { label: string; help: string; old: string; neu: string; better: boolean | null }[] = [
    { label: 'Électricité payée', help: 'Ce que coûte le réseau électrique pour pomper, sur 2 jours.',
      old: `${fr(b.grid_cost_tnd, 2)} TND`, neu: `${fr(k.grid_cost_tnd, 2)} TND`, better: k.grid_cost_tnd < b.grid_cost_tnd - 0.005 },
    { label: 'Énergie du soleil', help: 'Part de l\'électricité de la pompe venue des panneaux solaires.',
      old: `${fr(b.solar_share_pct, 0)} %`, neu: `${fr(k.solar_share_pct, 0)} %`, better: k.solar_share_pct > b.solar_share_pct + 0.5 },
    { label: 'Eau pompée', help: 'Eau tirée du puits sur 2 jours.',
      old: `${fr(b.water_pumped_m3, 0)} m³`, neu: `${fr(k.water_pumped_m3, 0)} m³`, better: k.water_pumped_m3 < b.water_pumped_m3 - 0.5 ? true : null },
    { label: 'Récolte sauvée', help: 'Valeur de la récolte protégée grâce à l\'eau donnée (estimation simulée, prix supposés).',
      old: `${fr(b.value_protected_tnd, 0)} TND`, neu: `${fr(k.value_protected_tnd, 0)} TND`, better: k.value_protected_tnd > b.value_protected_tnd + 0.5 },
  ]
  return (
    <div className="overflow-hidden rounded-xl border border-ink-200">
      <table className="w-full text-sm">
        <thead className="bg-ink-50 text-xs text-ink-500">
          <tr><th className="px-3 py-2 text-left">Sur 2 jours</th><th className="px-3 py-2 text-right">À l'ancienne<InfoTip text="Pompe allumée dès 7h à fond, même dose pour chaque parcelle jusqu'au quota." /></th><th className="px-3 py-2 text-right">Avec AquaMind</th></tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.label} className="border-t border-ink-100">
              <td className="px-3 py-2 text-ink-700">{r.label}<InfoTip text={r.help} /></td>
              <td className="px-3 py-2 text-right tabular-nums text-ink-500">{r.old}</td>
              <td className={`px-3 py-2 text-right font-semibold tabular-nums ${r.better ? 'text-leaf-600' : 'text-ink-900'}`}>{r.neu} {r.better ? '✅' : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function RawExplanations({ plan }: { plan: OptimizeResult }) {
  return (
    <Details summary="Voir le raisonnement complet de l'IA (texte détaillé)">
      <Explanations items={plan.explanations} />
    </Details>
  )
}
