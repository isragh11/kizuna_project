// Encart « Modèles » : MAE ET0, gain vs persistance, règle adaptative (5 graines), sensibilité des prix.
import { useEffect, useState } from 'react'
import { FlaskConical } from 'lucide-react'
import { apiGet } from '../lib/api'
import { Card, SimLabel, fr } from './ui'

export default function ModelsCard({ compact = false }: { compact?: boolean }) {
  const [m, setM] = useState<any>(null)
  const [s, setS] = useState<any>(null)
  const [sErr, setSErr] = useState(false)
  useEffect(() => {
    apiGet<any>('/models/metrics').then((r) => setM(r.data.summary)).catch(() => {})
    apiGet<any>('/models/sensitivity', { timeoutMs: 60000 }).then((r) => setS(r.data)).catch(() => setSErr(true))
  }, [])
  if (!m) return <Card title="Modèles" icon={<FlaskConical size={16} />}><p className="text-sm text-ink-500">Chargement…</p></Card>
  const tn = m.et0?.tunisia, jp = m.et0?.japan
  const rule = m.live_detection
  const ra = rule?.normal?.['Règle adaptative'], rd = rule?.drift?.['Règle adaptative'], fixed = rule?.drift?.['Règle fixe']
  const farm = m.et0_farm
  return (
    <Card title="Modèles et validation" icon={<FlaskConical size={16} className="text-water-600" />}>
      <div className={`grid gap-4 ${compact ? 'md:grid-cols-2' : 'md:grid-cols-2 xl:grid-cols-4'}`}>
        <div>
          <div className="text-xs font-medium text-ink-500">Prévision ET0 J+1 (LightGBM, test 2024–2025)</div>
          <div className="mt-1 text-sm text-ink-900">MAE <b>{fr(tn?.mae_mm, 2)} mm/j</b> Tunisie · <b>{fr(jp?.mae_mm, 2)}</b> Japon</div>
          <div className="text-sm text-ink-700">Gain vs persistance : <b className="text-leaf-600">−{fr(tn?.gain_vs_persistence_pct, 1)} %</b> / <b className="text-leaf-600">−{fr(jp?.gain_vs_persistence_pct, 1)} %</b></div>
          {farm && <div className="mt-1 text-xs text-ink-500">≈ {fr(farm.mae_lightgbm_m3_day, 1)} m³/j d'erreur sur la ferme ({fr(farm.mae_lightgbm_pct_quota, 1)} % du quota)</div>}
        </div>
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-ink-500">Détection en direct : règle adaptative <SimLabel /></div>
          <div className="mt-1 text-sm text-ink-900">Fuites détectées : <b>{fr((ra?.['taux de détection_mean'] ?? 0) * 100, 0)} %</b> (normal) · <b>{fr((rd?.['taux de détection_mean'] ?? 0) * 100, 0)} %</b> (dérive)</div>
          <div className="text-sm text-ink-700">Fausses alertes sous dérive : <b>{fr(rd?.['fausses alertes / jour_mean'], 2)}/j</b> contre {fr(fixed?.['fausses alertes / jour_mean'], 2)}/j (règle fixe)</div>
          <div className="text-xs text-ink-500">5 graines × 2 scénarios ; River = indice « confirmé par l'IA »</div>
        </div>
        <div>
          <div className="text-xs font-medium text-ink-500">Fuites BattLeDIM (IsolationForest / CUSUM)</div>
          <div className="mt-1 text-sm text-ink-900">Ne bat pas des inspections régulières → <b>indicateur lent, sans alerte</b></div>
          <div className="text-xs text-ink-500">Résultat honnête : réseau urbain, 3 débitmètres seulement</div>
        </div>
        <div>
          <div className="flex items-center gap-2 text-xs font-medium text-ink-500">Sensibilité des prix (±30 %, canicule) <SimLabel /></div>
          {s ? (
            <>
              <div className="mt-1 text-sm text-ink-900">Gain vs traditionnel : <b className="text-leaf-600">{fr(Math.min(...s.rows.map((r: any) => r.gain_vs_trad_tnd)), 0)} à {fr(Math.max(...s.rows.map((r: any) => r.gain_vs_trad_tnd)), 0)} TND</b> / 48 h</div>
              <div className="text-xs text-ink-500">{s.summary}</div>
            </>
          ) : <div className="text-xs text-ink-500">{sErr ? 'indisponible (API à mettre à jour ?) — voir docs/SCENARIOS.md' : 'calcul…'}</div>}
        </div>
      </div>
      {!compact && s && (
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead className="text-ink-500"><tr><th className="py-1">Variation</th><th>Ordre des priorités</th><th>Satisfaction A / B / C</th><th>Part solaire</th><th>Gain vs trad.</th></tr></thead>
            <tbody className="text-ink-700">
              {s.rows.map((r: any) => (
                <tr key={r.variation} className="border-t border-ink-100"><td className="py-1 font-medium">{r.variation}</td><td>{r.ordre}</td><td>{r.satisfaction} %</td><td>{fr(r.part_solaire_pct, 0)} %</td><td>{r.gain_vs_trad_tnd >= 0 ? '+' : ''}{fr(r.gain_vs_trad_tnd, 0)} TND</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
