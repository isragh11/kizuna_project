// Champ construit avec des dimensions : rectangle, carré, surface connue, ou côté par côté (longueur + direction).
// Intégré depuis la branche master, rendu robuste : virgule décimale acceptée, position lue dans tous les
// formats courants ou choisie d'un toucher sur la carte, angles normalisés, limites de bon sens.
import { useEffect, useState } from 'react'
import { Crosshair, LocateFixed, MousePointerClick, Plus, Trash2, Wand2 } from 'lucide-react'
import { Button, InfoTip, Segmented } from './ui'
import { apiSend } from '../lib/api'
import { failText, fmtLatLon, parseCoords, parseNumber, type PolygonGeometry } from '../lib/parcels'

type Shape = 'rectangle' | 'square' | 'area' | 'custom'
interface Side { distance: string; bearing: string; precise: boolean }

const DIRECTIONS = [
  { deg: 0, label: '↑ Nord' }, { deg: 45, label: '↗ Nord-Est' }, { deg: 90, label: '→ Est' }, { deg: 135, label: '↘ Sud-Est' },
  { deg: 180, label: '↓ Sud' }, { deg: 225, label: '↙ Sud-Ouest' }, { deg: 270, label: '← Ouest' }, { deg: 315, label: '↖ Nord-Ouest' },
]
const MAX_SIDE_M = 10_000
const inputCls = 'h-10 w-full rounded-lg border border-ink-200 bg-white px-2.5 text-sm tabular-nums focus:border-water-400 focus:outline-none'
const norm = (deg: number) => ((deg % 360) + 360) % 360

export function ManualDimensions({ onGeometry, getCenter, requestPick, setPin }: {
  onGeometry: (g: PolygonGeometry) => void
  getCenter: () => [number, number] | null           // [lat, lon] du centre de la carte
  requestPick: (cb: (p: [number, number]) => void) => void
  setPin: (p: [number, number] | null) => void
}) {
  const [shape, setShape] = useState<Shape>('rectangle')
  const [pos, setPos] = useState('')
  const [length, setLength] = useState('100')
  const [width, setWidth] = useState('50')
  const [hectares, setHectares] = useState('1')
  const [orientation, setOrientation] = useState('0')
  const [sides, setSides] = useState<Side[]>([
    { distance: '100', bearing: '0', precise: false }, { distance: '80', bearing: '90', precise: false },
    { distance: '100', bearing: '180', precise: false }, { distance: '80', bearing: '270', precise: false },
  ])
  const [error, setError] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [picking, setPicking] = useState(false)

  const parsed = parseCoords(pos)
  useEffect(() => { setPin(parsed) }, [pos])            // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => setPin(null), [setPin])

  const pick = () => {
    setPicking(true)
    requestPick((p) => { setPos(fmtLatLon(p)); setPicking(false) })
  }
  const fromCenter = () => { const c = getCenter(); if (c) setPos(fmtLatLon(c)) }
  const fromGps = () => {
    if (!('geolocation' in navigator)) { setError('Cet appareil ne donne pas sa position.'); return }
    navigator.geolocation.getCurrentPosition(
      (p) => setPos(fmtLatLon([p.coords.latitude, p.coords.longitude])),
      (e) => setError(e.code === 1 ? 'Localisation refusée : autorisez-la dans le navigateur.' : 'Position indisponible pour le moment.'),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 10000 },
    )
  }

  const generate = async () => {
    setError(''); setNote('')
    let at = parsed
    if (pos.trim() && !at) { setError('Position illisible. Exemples : « 34.98, 9.49 » ou « 34°58\'48"N 9°29\'24"E ».'); return }
    if (!at) {
      at = getCenter()
      if (!at) { setError('Choisissez une position ou centrez la carte sur votre champ.'); return }
      setPos(fmtLatLon(at))
    }
    const [lat, lon] = at

    let path: string, body: unknown
    if (shape === 'custom') {
      const segs = sides.map((s) => ({ distance_m: parseNumber(s.distance), bearing_deg: norm(parseNumber(s.bearing)) }))
      const bad = segs.findIndex((s) => !(s.distance_m > 0) || !Number.isFinite(s.bearing_deg))
      if (segs.length < 3) { setError('Il faut au moins 3 côtés.'); return }
      if (bad >= 0) { setError(`Côté ${bad + 1} : indiquez une longueur positive (en mètres) et une direction.`); return }
      if (segs.some((s) => s.distance_m > MAX_SIDE_M)) { setError('Un côté dépasse 10 km : vérifiez l\'unité (mètres).'); return }
      path = '/api/parcels/polygon-segments'
      body = { longitude: lon, latitude: lat, segments: segs }
    } else {
      let l: number, w: number
      if (shape === 'area') {
        const ha = parseNumber(hectares)
        if (!(ha > 0)) { setError('Indiquez la surface en hectares (ex. 2,5).'); return }
        if (ha > 10_000) { setError('Plus de 10 000 ha : vérifiez l\'unité (hectares).'); return }
        l = w = Math.sqrt(ha * 10_000)
      } else {
        w = parseNumber(width)
        l = shape === 'square' ? w : parseNumber(length)
        if (!(l > 0) || !(w > 0)) { setError(shape === 'square' ? 'Indiquez la longueur du côté, en mètres.' : 'Indiquez la longueur et la largeur, en mètres.'); return }
        if (l > MAX_SIDE_M || w > MAX_SIDE_M) { setError('Un côté dépasse 10 km : vérifiez l\'unité (mètres).'); return }
      }
      path = '/api/parcels/rectangle'
      body = { longitude: lon, latitude: lat, length_m: l, width_m: w, orientation_deg: parseNumber(orientation) || 0 }
    }

    setBusy(true)
    try {
      const r = await apiSend(path, body)
      if (!r.ok) { setError(failText(r, 'Impossible de construire ce champ')); return }
      onGeometry(r.data.geometry)
      if (shape === 'custom' && r.data.closing_gap_m >= 1) {
        setNote(`Le dernier côté s'arrête à ${Math.round(r.data.closing_gap_m)} m du départ : le contour a été refermé en ligne droite. Vérifiez vos mesures.`)
      } else if (shape === 'area') {
        setNote('Un carré de cette surface a été posé : ajustez sa forme avec « Modifier les coins » pour suivre les vraies limites.')
      }
    } catch {
      setError('Serveur injoignable : le champ n\'a pas été construit.')
    } finally { setBusy(false) }
  }

  const setSide = (i: number, patch: Partial<Side>) => setSides((cur) => cur.map((s, j) => (j === i ? { ...s, ...patch } : s)))
  const total = sides.reduce((s, x) => s + (parseNumber(x.distance) || 0), 0)

  return (
    <div className="space-y-3 rounded-xl border border-ink-200 bg-white p-3 text-sm">
      <Segmented<Shape> value={shape} onChange={(v) => { setShape(v); setError(''); setNote('') }}
        options={[{ value: 'rectangle', label: 'Rectangle' }, { value: 'square', label: 'Carré' }, { value: 'area', label: 'Surface' }, { value: 'custom', label: 'Côté par côté' }]} />

      <div>
        <label className="mb-1 flex items-center text-ink-700" htmlFor="dim-pos">{shape === 'custom' ? 'Coin de départ' : 'Centre du champ'}
          <InfoTip wide text="Touchez la carte, ou collez des coordonnées (« 34.98, 9.49 », degrés-minutes-secondes, lien Google Maps). Vide = centre de la carte." /></label>
        <input id="dim-pos" className={`${inputCls} ${pos.trim() && !parsed ? 'border-red-300' : ''}`} placeholder="vide = centre de la carte"
          value={pos} onChange={(e) => setPos(e.target.value)} autoComplete="off" />
        {pos.trim() && !parsed && <p className="mt-1 text-xs text-red-600">Position illisible : « 34.98, 9.49 » par exemple.</p>}
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          <Button size="sm" variant={picking ? 'primary' : 'secondary'} onClick={pick}><MousePointerClick size={13} /> {picking ? 'Touchez la carte…' : 'Choisir sur la carte'}</Button>
          <Button size="sm" onClick={fromCenter}><Crosshair size={13} /> Centre de la carte</Button>
          <Button size="sm" onClick={fromGps}><LocateFixed size={13} /> Ma position</Button>
        </div>
      </div>

      {shape === 'area' && (
        <label className="block text-ink-700">Surface du champ (hectares)
          <input className={inputCls} inputMode="decimal" value={hectares} onChange={(e) => setHectares(e.target.value)} placeholder="ex. 2,5" />
          <span className="mt-1 block text-xs text-ink-500">1 ha = 10 000 m² = un carré de 100 m × 100 m. {parseNumber(hectares) > 0 && `Carré de ${Math.round(Math.sqrt(parseNumber(hectares) * 10000))} m de côté.`}</span>
        </label>
      )}

      {(shape === 'rectangle' || shape === 'square') && (
        <div className="grid grid-cols-2 gap-2">
          {shape === 'rectangle' && (
            <label className="text-ink-700">Longueur (m)<input className={inputCls} inputMode="decimal" value={length} onChange={(e) => setLength(e.target.value)} /></label>
          )}
          <label className="text-ink-700">{shape === 'square' ? 'Côté (m)' : 'Largeur (m)'}<input className={inputCls} inputMode="decimal" value={width} onChange={(e) => setWidth(e.target.value)} /></label>
        </div>
      )}

      {shape !== 'custom' && (
        <label className="block text-ink-700">
          <span className="flex items-center">Rotation : {parseNumber(orientation) || 0}°
            <InfoTip text="0° : la longueur suit l'axe ouest–est. Tournez pour aligner le champ sur la photo ; vous pourrez aussi le faire pivoter sur la carte." /></span>
          <input type="range" min={-90} max={90} value={orientation} onChange={(e) => setOrientation(e.target.value)} className="w-full accent-water-600" aria-label="Rotation en degrés" />
        </label>
      )}

      {shape === 'custom' && (
        <div>
          <p className="mb-2 text-xs text-ink-500">Depuis le coin de départ, notez chaque côté dans l'ordre du tour : sa longueur et la direction dans laquelle vous marchez (boussole du téléphone).</p>
          <div className="space-y-1.5">
            {sides.map((s, i) => (
              <div key={i} className="space-y-1">
                <div className="grid grid-cols-[1.5rem_minmax(0,1fr)_minmax(0,1.4fr)_2rem] items-center gap-1.5">
                  <span className="text-center text-xs font-semibold text-ink-500">{i + 1}</span>
                  <div className="relative">
                    <input className={`${inputCls} pr-6`} inputMode="decimal" value={s.distance} onChange={(e) => setSide(i, { distance: e.target.value })} aria-label={`Longueur du côté ${i + 1} en mètres`} />
                    <span className="pointer-events-none absolute right-2 top-2.5 text-xs text-ink-400">m</span>
                  </div>
                  <select className={inputCls} value={s.precise ? 'precise' : s.bearing}
                    onChange={(e) => e.target.value === 'precise' ? setSide(i, { precise: true }) : setSide(i, { bearing: e.target.value, precise: false })}
                    aria-label={`Direction du côté ${i + 1}`}>
                    {DIRECTIONS.map((d) => <option key={d.deg} value={String(d.deg)}>{d.label}</option>)}
                    <option value="precise">Angle précis…</option>
                  </select>
                  <button type="button" onClick={() => setSides((cur) => cur.filter((_, j) => j !== i))} disabled={sides.length <= 3}
                    className="rounded p-1.5 text-ink-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-30" aria-label={`Supprimer le côté ${i + 1}`}><Trash2 size={14} /></button>
                </div>
                {s.precise && (
                  <label className="ml-8 flex items-center gap-1.5 text-xs text-ink-500">angle (0° = nord, 90° = est)
                    <input className={`${inputCls} h-8 w-20`} inputMode="decimal" value={s.bearing} onChange={(e) => setSide(i, { bearing: e.target.value })} aria-label={`Angle exact du côté ${i + 1}`} />°
                  </label>
                )}
              </div>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap items-center justify-between gap-2 text-xs">
            <Button size="sm" onClick={() => setSides((cur) => [...cur, { distance: '50', bearing: '0', precise: false }])}><Plus size={13} /> Ajouter un côté</Button>
            <span className="text-ink-500">tour total : {Math.round(total).toLocaleString('fr-FR')} m</span>
          </div>
        </div>
      )}

      {error && <p className="rounded-lg bg-red-50 p-2 text-red-700">{error}</p>}
      {note && <p className="rounded-lg bg-sun-50 p-2 text-sun-600">{note}</p>}
      <Button variant="primary" onClick={generate} disabled={busy} className="w-full"><Wand2 size={15} /> {busy ? 'Construction…' : 'Placer sur la carte'}</Button>
    </div>
  )
}
