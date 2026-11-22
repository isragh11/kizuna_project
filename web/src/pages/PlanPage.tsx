import { useState } from 'react'
import { CalendarClock, RefreshCw, Sun, Warehouse, Layers, Scale, FlaskConical } from 'lucide-react'
import { EnergyDays, ScheduleGrid, TankPlanChart } from '../components/FarmCharts'
import { CropCards, OldVsNew, PlanSituation, RawExplanations, SharingSteps } from '../components/PlanSummary'
import { Button, Card, ConfirmDialog, Empty, InfoTip, SimLabel } from '../components/ui'
import { apiGet } from '../lib/api'
import { useActions, useNotify } from '../lib/actions'
import { useCrops } from '../lib/useCrops'
import { usePlan, useScenarios } from '../lib/usePlan'
import { useLive } from '../lib/live'

// Ce que chaque situation de démonstration montre, en mots simples (résultats vérifiés sur l'API).
const SCENARIO_TEXT: Record<string, { emoji: string; what: string; expect: string }> = {
  tunisia_heatwave: { emoji: '🔥', what: '2 jours de canicule réels (20–21 juillet 2025, jusqu\'à 44,8 °C).',
    expect: "Les cultures demandent plus du double du quota : tomates ≈ 48 %, oliviers 30 % (survie), piments ≈ 53 %. 100 % d'énergie solaire." },
  tunisia_cloudy: { emoji: '🌥️', what: '8–9 juin 2024 : le 2e jour est voilé (moitié moins de soleil).',
    expect: "L'IA remplit la cuve la veille au soleil et la nuit au tarif bas. Tomates ≈ 70 %, oliviers 30 %, piments ≈ 80 %." },
  water_cut_50: { emoji: '🚱', what: 'Juillet 2025 avec seulement 30 m³ d\'eau par jour au lieu de 60.',
    expect: "Chaque culture reçoit sa part de survie (≈ 30 %), pas plus : l'IA évite qu'une culture meure." },
  japan_rice: { emoji: '🗾', what: 'Rizières de Niigata (Japon), fin août 2025, eau abondante.',
    expect: "Rien ne manque (100 % partout) ; l'IA économise de l'eau (260 m³ pompés au lieu de 300)." },
}

/** « scénario tunisia_heatwave » → « Canicule à Sidi Bouzid » ; « POST /optimize (…) » → « plan « Et si ? » appliqué ». */
function sourceText(source: string | null | undefined, scenarios: { key: string; title: string }[]): string {
  if (!source) return ''
  const key = source.replace('scénario ', '').trim()
  const sc = scenarios.find((s) => s.key === key)
  if (sc) return sc.title
  if (source.startsWith('POST /optimize')) return 'plan « Et si ? » appliqué'
  return source
}

export default function PlanPage() {
  const { plan, error, fromCache } = usePlan()
  const crops = useCrops(plan)
  const act = useActions()
  const notify = useNotify()
  const live = useLive()
  const scenarios = useScenarios()
  const [choice, setChoice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const manual = live.health?.plan?.mode === 'manual'

  const applyScenario = async (key: string) => {
    setBusy(true)
    try {
      await apiGet(`/scenarios/${key}?apply=true`, { cache: false, timeoutMs: 60000 })
      notify(true, 'Situation appliquée : la ferme simulée passe à ces dates et suit ce plan')
    } catch (e: any) {
      notify(false, `Situation non appliquée : ${e?.message ?? e}`)
    } finally { setBusy(false) }
  }

  const start = plan?.inputs?.start ? new Date(plan.inputs.start) : null
  const dates = start ? `${start.toLocaleDateString('fr-FR', { day: 'numeric', month: 'long' })} et ${new Date(start.getTime() + 86400000).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' })}` : ''

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-ink-200/70 bg-white p-4 shadow-card">
        <div>
          <div className="text-lg font-semibold text-ink-900">📅 Plan d'arrosage du {dates || '…'}</div>
          <div className="text-sm text-ink-500">
            {manual ? `Situation choisie : ${sourceText(live.health?.plan?.source, scenarios)}` : 'Calculé automatiquement avec la prévision météo de demain'}
            {fromCache ? ' · (dernières données connues)' : ''}
          </div>
        </div>
        <div className="flex items-center gap-2"><SimLabel />
          <Button variant="primary" disabled={busy} onClick={async () => { setBusy(true); await act.refreshPlan(); setBusy(false) }}>
            <RefreshCw size={15} /> {manual ? 'Revenir à la vraie météo' : 'Recalculer'}
          </Button>
        </div>
      </div>

      {!plan ? <Empty>{error ?? 'Aucun plan pour l\'instant (calcul en cours…).'}</Empty> : (
        <>
          <PlanSituation plan={plan} />
          <CropCards plan={plan} crops={crops} />

          <Card title="Quand pompe-t-on et quand arrose-t-on ?" icon={<CalendarClock size={16} className="text-water-600" />}
            actions={<InfoTip wide text="Chaque case = une heure. La pompe remplit la cuve depuis le puits (de préférence quand il y a du soleil) ; les parcelles sont arrosées depuis la cuve, parfois le soir ou la nuit (moins d'évaporation)." />}>
            <ScheduleGrid plan={plan} crops={crops} />
          </Card>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card title="D'où vient l'électricité de la pompe ?" icon={<Sun size={16} className="text-sun-600" />}><EnergyDays plan={plan} /></Card>
            <Card title="Eau dans la cuve (m³)" icon={<Warehouse size={16} className="text-water-600" />}
              actions={<InfoTip text="La cuve sert de réserve : elle se remplit au soleil et se vide quand on arrose. Elle finit au moins aussi pleine qu'au début." />}>
              <TankPlanChart plan={plan} />
            </Card>
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card title="Comment l'eau est partagée" icon={<Layers size={16} className="text-water-600" />}><SharingSteps plan={plan} crops={crops} /></Card>
            <Card title="À l'ancienne ou avec AquaMind ?" icon={<Scale size={16} className="text-leaf-600" />}><OldVsNew plan={plan} /></Card>
          </div>
          <RawExplanations plan={plan} />
        </>
      )}

      <Card title="Essayer une situation de démonstration" icon={<FlaskConical size={16} className="text-sun-600" />}>
        <p className="mb-3 text-sm text-ink-700">Chaque situation rejoue 2 vraies journées de météo. En l'appliquant, la ferme simulée <b>passe à ces dates</b> (cultures, soleil, chaleur) et suit le plan. « Revenir à la vraie météo » annule.</p>
        <div className="grid gap-3 md:grid-cols-2">
          {scenarios.map((s) => {
            const t = SCENARIO_TEXT[s.key]
            return (
              <div key={s.key} className="rounded-xl border border-ink-200 p-3">
                <div className="font-semibold text-ink-900">{t?.emoji} {s.title}</div>
                <p className="mt-1 text-sm text-ink-700">{t?.what ?? s.description}</p>
                {t && <p className="mt-1 text-xs text-ink-500"><b>Ce que vous verrez :</b> {t.expect}</p>}
                <Button size="sm" variant="warn" className="mt-2" disabled={busy} onClick={() => setChoice(s.key)}>Appliquer</Button>
              </div>
            )
          })}
        </div>
      </Card>
      <ConfirmDialog open={choice !== null} onClose={() => setChoice(null)} title="Appliquer cette situation ?"
        message={<>La ferme simulée suivra « {scenarios.find((s) => s.key === choice)?.title} » à la place de la vraie météo.</>}
        confirmLabel="Appliquer" variant="warn" onConfirm={() => choice && applyScenario(choice)} />
    </div>
  )
}
