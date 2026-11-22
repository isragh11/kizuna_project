// Plan cadastral : envoi (JPG/PNG/PDF), détection du contour par OpenCV, puis placement VÉRIFIÉ PAR UNE PERSONNE.
// Intégré depuis la branche master. Les points détectés sont en pixels : ils ne deviennent un champ sur la carte
// qu'avec une échelle donnée par l'agriculteur (un côté ou la surface) et un placement qu'il ajuste lui-même.
import { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ChevronRight, FileUp, MapPinned } from 'lucide-react'
import { Badge, Button, InfoTip, Segmented } from './ui'
import { apiSend, apiUpload } from '../lib/api'
import { M_PER_DEG, closeRing, failText, parseNumber, type PlanExtraction, type PlanPoint, type PolygonGeometry } from '../lib/parcels'

const MAX_MB = 10
const ACCEPTED = ['image/jpeg', 'image/png', 'application/pdf']
type ScaleBy = 'side' | 'area'

function pixelArea(pts: PlanPoint[]): number {
  let s = 0
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length]
    s += a.x * b.y - b.x * a.y
  }
  return Math.abs(s) / 2
}

export function PlanUpload({ onExtraction, onGeometry, getCenter }: {
  onExtraction: (x: PlanExtraction | null) => void
  onGeometry: (g: PolygonGeometry) => void
  getCenter: () => [number, number] | null
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [plan, setPlan] = useState<PlanExtraction | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [candidate, setCandidate] = useState(0)
  const [side, setSide] = useState(0)                // côté de référence : points[side] → points[side+1]
  const [scaleBy, setScaleBy] = useState<ScaleBy>('side')
  const [realLength, setRealLength] = useState('')
  const [realArea, setRealArea] = useState('')
  const [dragOver, setDragOver] = useState(false)

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview) }, [preview])

  const upload = async (file: File) => {
    setError(''); setPlan(null); onExtraction(null)
    if (file.type && !ACCEPTED.includes(file.type) && !/\.(jpe?g|png|pdf)$/i.test(file.name)) { setError('Format non accepté : photo JPG ou PNG, ou PDF.'); return }
    if (file.size === 0) { setError('Ce fichier est vide.'); return }
    if (file.size > MAX_MB * 1024 * 1024) { setError(`Fichier trop lourd (${(file.size / 1048576).toFixed(1)} Mo) : ${MAX_MB} Mo maximum. Réduisez la photo ou exportez le PDF en qualité moyenne.`); return }
    setPreview(file.type.startsWith('image/') ? URL.createObjectURL(file) : null)
    setBusy(true)
    try {
      const up = await apiUpload('/api/parcels/upload-plan', file)
      if (!up.ok) { setError(failText(up, 'Envoi refusé')); return }
      const ex = await apiSend<PlanExtraction>('/api/parcels/extract-plan', { document_id: up.data.document_id }, 120000)
      if (!ex.ok) { setError(failText(ex, 'Lecture du plan impossible')); return }
      setPlan(ex.data); onExtraction(ex.data); setCandidate(0); setSide(0)
    } catch {
      setError('Serveur injoignable : le plan n\'a pas été envoyé.')
    } finally { setBusy(false) }
  }

  const candidates = plan?.candidates?.length ? plan.candidates : plan?.detected ? [{ boundary_points: plan.boundary_points, extraction_confidence: plan.extraction_confidence }] : []
  const current = candidates[candidate % Math.max(1, candidates.length)]
  const pts = current?.boundary_points ?? []
  const sidePx = useMemo(() => {
    if (pts.length < 2) return 0
    const a = pts[side % pts.length], b = pts[(side + 1) % pts.length]
    return Math.hypot(b.x - a.x, b.y - a.y)
  }, [pts, side])

  /** Pixels → mètres (échelle donnée par l'agriculteur, nord du plan vers le haut) → longitude/latitude. */
  const place = () => {
    setError('')
    let scale: number
    if (scaleBy === 'side') {
      const L = parseNumber(realLength)
      if (!(L > 0) || !sidePx) { setError('Indiquez la longueur réelle du côté en orange, en mètres (elle figure souvent sur le plan).'); return }
      scale = L / sidePx
    } else {
      const ha = parseNumber(realArea)
      const px = pixelArea(pts)
      if (!(ha > 0) || !px) { setError('Indiquez la surface du champ en hectares (souvent écrite sur le plan, ex. 4,2).'); return }
      scale = Math.sqrt((ha * 10_000) / px)
    }
    const c = getCenter()
    if (!c) { setError('Centrez d\'abord la carte sur votre champ.'); return }
    const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length
    const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length
    const mLon = M_PER_DEG * Math.cos((c[0] * Math.PI) / 180)
    const ring = pts.map((p) => [
      +(c[1] + ((p.x - cx) * scale) / mLon).toFixed(8),
      +(c[0] - ((p.y - cy) * scale) / M_PER_DEG).toFixed(8),
    ])
    onGeometry({ type: 'Polygon', coordinates: [closeRing(ring)] })
  }

  const W = plan?.image_width ?? 1, H = plan?.image_height ?? 1
  const label = (i: number) => pts[i % pts.length]?.id ?? '?'

  return (
    <div className="space-y-3 rounded-xl border border-ink-200 bg-white p-3 text-sm">
      <label
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }} onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files?.[0]; if (f && !busy) upload(f) }}
        className={`flex cursor-pointer flex-col items-center gap-1 rounded-xl border-2 border-dashed p-4 text-center transition ${busy ? 'border-ink-200 opacity-60' : dragOver ? 'border-water-500 bg-water-50' : 'border-water-200 hover:bg-water-50'}`}>
        <FileUp size={22} className="text-water-600" />
        <span className="font-medium text-ink-900">{busy ? 'Analyse du plan…' : plan ? 'Choisir un autre plan' : 'Choisir ou déposer un plan (photo ou PDF)'}</span>
        <span className="text-xs text-ink-500">JPG, PNG ou PDF — {MAX_MB} Mo maximum. Photo bien droite, nette, sans ombre, le plan à plat.</span>
        <input type="file" accept=".jpg,.jpeg,.png,.pdf,image/jpeg,image/png,application/pdf" className="sr-only" disabled={busy}
          onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) upload(f) }} />
      </label>

      {error && <p className="rounded-lg bg-red-50 p-2 text-red-700">{error}</p>}

      {plan && (
        <>
          <div className="flex gap-2 rounded-lg border border-sun-400/50 bg-sun-50 p-2.5 text-ink-900">
            <AlertTriangle size={18} className="mt-0.5 shrink-0 text-sun-600" />
            <div>
              <b>À vérifier par vous.</b> Le contour est lu automatiquement sur le dessin : ce ne sont <b>pas des coordonnées GPS</b>.
              Comparez avec le plan, donnez l'échelle, puis ajustez la forme sur la photo satellite avant d'enregistrer.
            </div>
          </div>

          {pts.length >= 3 ? (
            <>
              <div className="overflow-hidden rounded-lg border border-ink-200 bg-ink-50">
                <svg viewBox={`0 0 ${W} ${H}`} className="block max-h-64 w-full" role="img" aria-label="Contour détecté sur le plan">
                  {preview && <image href={preview} x={0} y={0} width={W} height={H} preserveAspectRatio="none" opacity={0.85} />}
                  <polygon points={pts.map((p) => `${p.x},${p.y}`).join(' ')} fill="rgba(34,167,85,0.18)" stroke="#16803f" strokeWidth={Math.max(2, W / 250)} />
                  {scaleBy === 'side' && (() => {
                    const a = pts[side % pts.length], b = pts[(side + 1) % pts.length]
                    return <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="#d97706" strokeWidth={Math.max(4, W / 120)} />
                  })()}
                  {pts.map((p) => (
                    <g key={p.id}>
                      <circle cx={p.x} cy={p.y} r={Math.max(8, W / 40)} fill="#fff" stroke="#16803f" strokeWidth={Math.max(2, W / 250)} />
                      <text x={p.x} y={p.y} dy="0.35em" textAnchor="middle" fontSize={Math.max(10, W / 32)} fontWeight={700} fill="#166534">{p.id}</text>
                    </g>
                  ))}
                </svg>
              </div>
              <div className="flex flex-wrap items-center gap-2 text-xs text-ink-500">
                <Badge tone={current.extraction_confidence >= 0.75 ? 'ok' : 'warn'}>contour : confiance {Math.round(current.extraction_confidence * 100)} %</Badge>
                <Badge tone="neutral">{pts.length} bornes</Badge>
                {candidates.length > 1 && (
                  <Button size="sm" onClick={() => { setCandidate((c) => (c + 1) % candidates.length); setSide(0) }}>
                    Ce n'est pas le bon ? Autre contour ({(candidate % candidates.length) + 1}/{candidates.length}) <ChevronRight size={13} />
                  </Button>
                )}
              </div>
              <p className="flex items-center text-xs text-ink-500">texte du plan : {plan.ocr_status === 'ok' ? `${plan.ocr_text.length} mots lus (${Math.round(plan.ocr_confidence * 100)} %)` : 'lecture automatique du texte non installée'}
                <InfoTip text="La lecture du texte (OCR) est optionnelle. Le contour fonctionne sans elle ; lisez les longueurs ou la surface sur le plan vous-même." /></p>

              <div className="space-y-2 rounded-lg bg-ink-50 p-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-ink-900">Donner l'échelle</span>
                  <Segmented<ScaleBy> value={scaleBy} onChange={setScaleBy} options={[{ value: 'side', label: 'Un côté' }, { value: 'area', label: 'La surface' }]} />
                </div>
                {scaleBy === 'side' ? (
                  <>
                    <div className="grid grid-cols-[auto_1fr] items-center gap-2">
                      <span className="text-ink-700">Côté</span>
                      <select className="h-10 rounded-lg border border-ink-200 bg-white px-2" value={side} onChange={(e) => setSide(Number(e.target.value))}>
                        {pts.map((_, i) => <option key={i} value={i}>{label(i)} → {label(i + 1)}</option>)}
                      </select>
                      <span className="text-ink-700">mesure</span>
                      <div className="flex items-center gap-1.5">
                        <input className="h-10 w-full rounded-lg border border-ink-200 bg-white px-2 tabular-nums" inputMode="decimal" placeholder="ex. 120"
                          value={realLength} onChange={(e) => setRealLength(e.target.value)} aria-label="Longueur réelle en mètres" />
                        <span className="text-ink-500">m</span>
                      </div>
                    </div>
                    <p className="text-xs text-ink-500">Le côté en orange sur le dessin. Sa longueur est souvent écrite sur le plan, ou mesurez-la sur le terrain.</p>
                  </>
                ) : (
                  <>
                    <div className="flex items-center gap-1.5">
                      <input className="h-10 w-full rounded-lg border border-ink-200 bg-white px-2 tabular-nums" inputMode="decimal" placeholder="ex. 4,2"
                        value={realArea} onChange={(e) => setRealArea(e.target.value)} aria-label="Surface réelle en hectares" />
                      <span className="text-ink-500">ha</span>
                    </div>
                    <p className="text-xs text-ink-500">La surface inscrite sur le plan ou sur l'acte (1 ha = 10 000 m²).</p>
                  </>
                )}
              </div>
              <Button variant="primary" className="w-full" onClick={place}><MapPinned size={15} /> Placer au centre de la carte</Button>
              <p className="text-xs text-ink-500">Ensuite, sur la carte : <b>Déplacer</b> pour le poser au bon endroit, <b>Tourner</b> pour l'aligner sur la photo satellite, <b>Modifier les coins</b> si besoin.</p>
            </>
          ) : (
            <p className="rounded-lg bg-ink-50 p-2.5 text-ink-700">Aucun contour net n'a été trouvé sur ce document. Essayez une photo plus nette et plus droite, ou dessinez le champ à la main sur la carte (méthode « Dessiner ») en vous aidant du plan.</p>
          )}
        </>
      )}
    </div>
  )
}
