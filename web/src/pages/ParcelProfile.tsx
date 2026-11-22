// Profil d'un champ : localisation, sol, soleil, eau, ce qui est planté où — puis les panneaux solaires et
// les capteurs recommandés (combien, où, combien ça coûte). Les coûts sont des ESTIMATIONS (equipment.json).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { MapContainer, Marker, Polygon, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import '@geoman-io/leaflet-geoman-free'
import 'leaflet/dist/leaflet.css'
import '@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css'
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts'
import {
  AlertTriangle, ArrowLeft, Columns3, Cpu, Droplets, Layers, MapPin, Pencil, PenLine, RefreshCw, Rows3, Sprout,
  Sun, SunMedium, Trash2, Wallet, Mountain, CheckCircle2, LandPlot,
} from 'lucide-react'
import { Badge, Button, Card, ConfirmDialog, Details, Empty, InfoTip, Segmented } from '../components/ui'
import { useNotify } from '../lib/actions'
import { areaText, failText, type PolygonGeometry } from '../lib/parcels'
import {
  IRRIGATION_LABEL, MONTHS, SHADING_LABEL, clipZone, cropColor, newZoneId, profileApi, splitStrips, tnd, zoneArea,
  type CropChoice, type Irrigation, type ProfileView, type Recommendations, type SensorItem, type Shading, type Zone,
} from '../lib/landProfile'

type Mode = 'view' | 'well' | 'draw'
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
const SENSOR_STYLE: Record<SensorItem['key'], { letter: string; color: string; name: string }> = {
  soil_moisture: { letter: 'H', color: '#16a34a', name: 'Humidité du sol' },
  flow_meter: { letter: 'C', color: '#2563eb', name: 'Compteur d\'eau' },
  pressure: { letter: 'P', color: '#7c3aed', name: 'Pression' },
  weather_station: { letter: 'M', color: '#0891b2', name: 'Météo' },
  gateway: { letter: 'R', color: '#475569', name: 'Radio' },
  tank_level: { letter: 'N', color: '#0d9488', name: 'Niveau de cuve' },
}
const toLatLngs = (g: PolygonGeometry) => g.coordinates[0].slice(0, -1).map(([lon, lat]) => [lat, lon] as [number, number])
/** Pastille lettre ; `dx`/`dy` (px) écartent les repères posés au même endroit (pompe : compteur, pression, radio). */
const badgeIcon = (letter: string, color: string, dx = 0, dy = 0) => L.divIcon({
  className: '', iconSize: [0, 0],
  html: `<span style="background:${color};transform:translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))" class="inline-flex h-6 w-6 items-center justify-center rounded-full border-2 border-white text-[11px] font-bold text-white shadow">${letter}</span>`,
})
/** Écart en cercle (px) pour le i-ème de n repères superposés. */
const fanOut = (i: number, n: number): [number, number] => n <= 1 ? [0, 0] : [Math.round(16 * Math.cos((2 * Math.PI * i) / n - Math.PI / 2)), Math.round(16 * Math.sin((2 * Math.PI * i) / n - Math.PI / 2))]
const WELL_ICON = L.divIcon({ className: '', iconSize: [0, 0], html: '<span class="-translate-x-1/2 -translate-y-1/2 inline-flex h-7 w-7 items-center justify-center rounded-full border-2 border-white bg-water-600 text-sm shadow">💧</span>' })

/** Carte du profil : zones, puits, panneaux, capteurs ; clic pour placer le puits ; dessin d'une zone. */
function MapTools({ mode, onPickWell, onDrawn, fit }: { mode: Mode; onPickWell: (p: { lon: number; lat: number }) => void; onDrawn: (g: PolygonGeometry) => void; fit: PolygonGeometry }) {
  const map = useMap()
  useEffect(() => { map.fitBounds(L.polygon(toLatLngs(fit)).getBounds(), { padding: [30, 30], maxZoom: 19 }) }, [map, fit])
  useMapEvents({ click: (e) => { if (mode === 'well') onPickWell({ lon: +e.latlng.lng.toFixed(7), lat: +e.latlng.lat.toFixed(7) }) } })
  useEffect(() => {
    const pm = (map as any).pm
    pm.setLang('fr')
    const onCreate = (e: any) => {
      const gj = e.layer.toGeoJSON()
      e.layer.remove()
      if (gj.geometry?.type === 'Polygon') onDrawn(gj.geometry)
    }
    map.on('pm:create', onCreate)
    if (mode === 'draw') pm.enableDraw('Polygon', { allowSelfIntersection: false, snappable: true, snapDistance: 15, pathOptions: { color: '#8b5cf6' } })
    else pm.disableDraw()
    map.getContainer().style.cursor = mode === 'well' ? 'crosshair' : ''
    return () => { map.off('pm:create', onCreate); pm.disableDraw() }
  }, [map, mode, onDrawn])
  return null
}

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: React.ReactNode }) {
  return (
    <div className="rounded-xl bg-ink-50 p-3">
      <div className="text-xs text-ink-500">{label}</div>
      <div className="text-lg font-bold tabular-nums text-ink-900">{value}</div>
      {sub && <div className="text-xs text-ink-500">{sub}</div>}
    </div>
  )
}

export default function ParcelProfile({ id, onBack }: { id: string; onBack: () => void }) {
  const notify = useNotify()
  const [view, setView] = useState<ProfileView | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [envBusy, setEnvBusy] = useState(false)
  const [crops, setCrops] = useState<CropChoice[]>([])
  const [zones, setZones] = useState<Zone[]>([])
  const [dirty, setDirty] = useState(false)
  const [zoneErr, setZoneErr] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [reco, setReco] = useState<Recommendations | null>(null)
  const [recoBusy, setRecoBusy] = useState(false)
  const [recoErr, setRecoErr] = useState<string | null>(null)
  const [mode, setModeRaw] = useState<Mode>('view')
  const mapBox = useRef<HTMLDivElement | null>(null)
  /** Dessiner une zone ou placer le puits se fait sur la carte : on la ramène à l'écran
   *  (les boutons sont plus bas dans la page, la carte pourrait être hors de vue). */
  const setMode = useCallback((m: Mode) => {
    setModeRaw(m)
    if (m !== 'view') mapBox.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [])
  const [layers, setLayers] = useState({ zones: true, solar: true, sensors: true })
  const [strips, setStrips] = useState(3)
  const [stripDir, setStripDir] = useState<'cote' | 'etage'>('cote')
  const [confirm, setConfirm] = useState<null | { title: string; message: string; label: string; run: () => void }>(null)
  const [depth, setDepth] = useState('')

  const loadReco = useCallback(async () => {
    setRecoBusy(true); setRecoErr(null)
    try { setReco(await profileApi.recommendations(id)) } catch (e: any) { setRecoErr(e?.message ?? 'Serveur injoignable') } finally { setRecoBusy(false) }
  }, [id])

  const apply = (v: ProfileView, keepZones = false) => {
    setView(v)
    if (!keepZones) { setZones(v.profile.zones); setDirty(false) }
    setDepth(v.profile.well_depth_m ? String(v.profile.well_depth_m) : '')
  }

  const load = useCallback(async () => {
    setLoadErr(null)
    try {
      const v = await profileApi.get(id)
      apply(v)
      if (!v.environment) {                          // première ouverture : sol, soleil, climat au centre du champ
        setEnvBusy(true)
        const r = await profileApi.refreshEnv(id)
        setEnvBusy(false)
        if (r.ok) apply(r.data); else notify(false, failText(r, 'Sol et ensoleillement indisponibles'))
      }
      loadReco()
    } catch (e: any) {
      setEnvBusy(false)
      setLoadErr(e?.status === 404 ? (e.message === 'Not Found' ? 'Le serveur AquaMind n\'est pas à jour : redémarrez l\'API.' : 'Ce champ n\'existe plus.') : e?.message ?? 'Serveur injoignable')
    }
  }, [id, loadReco, notify])

  useEffect(() => { load(); profileApi.crops().then(setCrops).catch(() => {}) }, [load])
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  /** Saisie simple (sol, ombre, puits, cuve) : enregistrée tout de suite, sans toucher aux zones en cours. */
  const savePart = async (body: Parameters<typeof profileApi.put>[1], okText: string) => {
    try {
      const r = await profileApi.put(id, body)
      if (!r.ok) { notify(false, failText(r, 'Modification refusée')); return false }
      apply(r.data, true); notify(true, okText); loadReco(); return true
    } catch { notify(false, 'Serveur injoignable'); return false }
  }

  const saveZones = async () => {
    setSaving(true); setZoneErr(null)
    try {
      const r = await profileApi.put(id, { zones })
      if (!r.ok) { setZoneErr(failText(r, 'Découpage refusé')); return }
      apply(r.data); notify(true, 'Cultures enregistrées : recommandations mises à jour'); loadReco()
    } catch { setZoneErr('Serveur injoignable : découpage non enregistré.') } finally { setSaving(false) }
  }

  const refreshEnv = async () => {
    setEnvBusy(true)
    try {
      const r = await profileApi.refreshEnv(id)
      if (r.ok) { apply(r.data, true); notify(true, 'Sol, soleil et climat mis à jour'); loadReco() } else notify(false, failText(r, 'Mise à jour impossible'))
    } catch { notify(false, 'Serveur injoignable') } finally { setEnvBusy(false) }
  }

  // ---------------------------------------------------------------- découpage
  const cropName = (key: string) => crops.find((c) => c.key === key)?.name ?? key
  const defaultCrop = crops[0]?.key ?? 'tomate'
  const editZones = (next: Zone[]) => { setZones(next); setDirty(true); setZoneErr(null) }
  const setZone = (zid: string, patch: Partial<Zone>) => editZones(zones.map((z) => (z.id === zid ? { ...z, ...patch } : z)))
  const replaceAll = (next: Zone[], title: string) => {
    if (zones.length) setConfirm({ title, message: 'Le découpage actuel sera remplacé.', label: 'Remplacer', run: () => editZones(next) })
    else editZones(next)
  }
  const wholeField = () => view && replaceAll([{ id: newZoneId(), name: cropName(defaultCrop), crop: defaultCrop, irrigation_method: 'drip', geometry: view.parcel.geometry }], 'Une seule culture sur tout le champ ?')
  const makeStrips = () => {
    if (!view) return
    const parts = splitStrips(view.parcel.geometry, strips, stripDir)
    replaceAll(parts.map((g, i) => ({ id: newZoneId(), name: `Bande ${i + 1}`, crop: defaultCrop, irrigation_method: 'drip', geometry: g })), `Partager en ${strips} bandes ?`)
  }
  const onDrawn = useCallback((g: PolygonGeometry) => {
    setMode('view')
    if (!view) return
    setZones((cur) => {
      const c = clipZone(g, view.parcel.geometry, cur)
      if (!c.geometry) { notify(false, 'Cette zone est en dehors du champ ou déjà cultivée.'); return cur }
      if (c.holes) { notify(false, 'Une zone ne peut pas entourer une autre zone : dessinez-la à côté.'); return cur }
      if (c.pieces > 1) notify(false, 'La zone a été coupée par une culture existante : seule la plus grande partie est gardée.')
      setDirty(true)
      return [...cur, { id: newZoneId(), name: `Zone ${cur.length + 1}`, crop: defaultCrop, irrigation_method: 'drip', geometry: c.geometry }]
    })
  }, [view, defaultCrop, notify])
  const pickWell = useCallback((p: { lon: number; lat: number }) => { setMode('view'); savePart({ well: p }, 'Puits placé') }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  const parcelM2 = view?.parcel.area_m2 ?? 0
  const zoneAreas = zones.map((z) => zoneArea(z.geometry))
  const plantedM2 = zoneAreas.reduce((s, a) => s + a, 0)
  const solar = reco?.solar
  const sensors = reco?.sensors
  const soil = view?.soil
  const env = view?.environment

  const sensorPoints = useMemo(() => {
    const pts = (sensors?.items ?? []).flatMap((i) => i.points.map((p) => ({ ...p, key: i.key })))
    const spot = (p: { lat: number; lon: number }) => `${p.lat.toFixed(6)},${p.lon.toFixed(6)}`
    const well = view?.profile.well
    const groups = new Map<string, number>()
    for (const p of pts) groups.set(spot(p), (groups.get(spot(p)) ?? 0) + 1)
    if (well) groups.set(spot(well), (groups.get(spot(well)) ?? 0) + 1)          // le puits garde le centre
    const seen = new Map<string, number>()
    return pts.map((p) => {
      const k = spot(p), n = groups.get(k) ?? 1, i = seen.get(k) ?? 0
      seen.set(k, i + 1)
      const shared = well && k === spot(well)
      const [dx, dy] = shared ? fanOut(i, n - 1) : fanOut(i, n)
      return { ...p, dx: shared ? Math.round(dx * 1.4) : dx, dy: shared ? Math.round(dy * 1.4) : dy }
    })
  }, [sensors, view])

  if (loadErr) return (
    <div className="space-y-3">
      <Button onClick={onBack}><ArrowLeft size={15} /> Mes terres</Button>
      <Empty>{loadErr} <button className="text-water-700 underline" onClick={load}>Réessayer</button></Empty>
    </div>
  )
  if (!view) return <Empty>Chargement du profil…</Empty>
  const p = view.parcel

  return (
    <div className="space-y-4">
      {/* ------------------------------------------------------------------ en-tête */}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <Button onClick={() => dirty ? setConfirm({ title: 'Quitter sans enregistrer ?', message: 'Le découpage en cultures n\'est pas enregistré.', label: 'Quitter', run: onBack }) : onBack()}>
            <ArrowLeft size={15} /> Mes terres
          </Button>
          <Button size="sm" variant="ghost" onClick={() => { window.location.hash = `terres?modifier=${encodeURIComponent(id)}` }}><Pencil size={14} /> Modifier le contour</Button>
        </div>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-xl font-bold text-ink-900">
            <LandPlot size={20} className="shrink-0 text-leaf-600" /><span className="min-w-0 break-words">{p.parcel_name}</span>
            <Badge tone="neutral">{areaText(p.area_m2)}</Badge>
            {p.validation_status === 'validated' ? <Badge tone="ok">contour vérifié</Badge> : <Badge tone="warn">contour à vérifier</Badge>}
          </div>
          <div className="text-sm text-ink-500">Profil du champ : ce qu'il y a dessous, au-dessus, et ce qui y pousse — puis l'équipement conseillé.</div>
        </div>
      </div>

      {envBusy && <div className="rounded-xl border border-water-200 bg-water-50 p-3 text-sm text-water-700">⏳ Récupération du sol, de l'ensoleillement et du climat à l'emplacement du champ (une dizaine de secondes la première fois)…</div>}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        {/* ---------------------------------------------------------------- fiches */}
        <div className="space-y-4">
          <Card title="1. Localisation" icon={<MapPin size={16} className="text-water-600" />}>
            <div className="grid grid-cols-3 gap-2">
              <Stat label="Surface" value={`${(p.area_m2 / 10000).toLocaleString('fr-FR', { maximumFractionDigits: 2 })} ha`} sub={`${Math.round(p.area_m2).toLocaleString('fr-FR')} m²`} />
              <Stat label="Tour" value={`${Math.round(p.perimeter_m).toLocaleString('fr-FR')} m`} />
              <Stat label="Centre" value={<span className="text-sm">{env ? `${env.centroid[1].toFixed(4)}, ${env.centroid[0].toFixed(4)}` : '—'}</span>} />
            </div>
          </Card>

          <Card title="2. Sol" icon={<Mountain size={16} className="text-sun-600" />}
            actions={<InfoTip wide text="La réserve utile est l'eau que le sol retient pour les racines (mm d'eau par mètre de sol). Un sol sableux en garde peu : il faut arroser plus souvent, moins longtemps." />}>
            {soil ? (
              <div className="space-y-3 text-sm">
                <div className="flex flex-wrap items-baseline gap-2"><span className="text-lg font-bold capitalize text-ink-900">{soil.texture}</span>
                  <span className="text-ink-500">· retient <b className="text-ink-900">{Math.round(soil.awc_mm_per_m)} mm</b> d'eau par mètre de sol</span></div>
                {soil.sand_pct !== null && (
                  <div>
                    <div className="flex h-3 overflow-hidden rounded-full">
                      <div style={{ width: `${soil.sand_pct}%` }} className="bg-sun-400" title="sable" />
                      <div style={{ width: `${soil.silt_pct}%` }} className="bg-amber-700/60" title="limon" />
                      <div style={{ width: `${soil.clay_pct}%` }} className="bg-orange-800" title="argile" />
                    </div>
                    <div className="mt-1 flex flex-wrap gap-3 text-xs text-ink-500">
                      <span>🟨 sable {Math.round(soil.sand_pct)} %</span><span>🟫 limon {Math.round(soil.silt_pct ?? 0)} %</span><span>🟧 argile {Math.round(soil.clay_pct ?? 0)} %</span>
                    </div>
                  </div>
                )}
                <p className="rounded-lg bg-leaf-50 p-2 text-leaf-700">{soil.advice}</p>
                <label className="flex flex-wrap items-center gap-2 text-xs text-ink-500">Source : {soil.source}{soil.assumption && <Badge tone="warn">estimation</Badge>}
                  <span className="ml-auto">Ce n'est pas votre sol ?</span>
                  <select className="h-8 rounded-lg border border-ink-200 bg-white px-2 text-xs" value={view.profile.soil_override ?? ''}
                    onChange={(e) => savePart({ soil_override: e.target.value || null }, e.target.value ? 'Type de sol corrigé' : 'Sol automatique (SoilGrids)')}>
                    <option value="">Automatique (carte des sols)</option>
                    {view.soil_classes.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                  </select>
                </label>
              </div>
            ) : <Empty>Sol pas encore connu.</Empty>}
          </Card>

          <Card title="3. Soleil" icon={<Sun size={16} className="text-sun-600" />}
            actions={<InfoTip wide text="kWh par kWc et par an : ce qu'un panneau de 1 000 W produit en un an à cet endroit, ombre du relief comprise. Au-dessus de 1 600, c'est excellent." />}>
            {env ? (
              <div className="space-y-3 text-sm">
                <div className="flex flex-wrap items-baseline gap-2"><span className="text-lg font-bold text-ink-900">{env.sun.label}</span>
                  <span className="text-ink-500">· {env.sun.kwh_per_kwc_year.toLocaleString('fr-FR')} kWh par kWc et par an · panneaux idéaux inclinés à {env.sun.optimal_tilt_deg}°</span></div>
                <div className="flex h-16 items-end gap-1" aria-label="Production solaire par mois">
                  {env.sun.kwh_per_kwc_month.map((v, i) => (
                    <div key={i} className="flex flex-1 flex-col items-center gap-0.5">
                      <div className="w-full rounded-t bg-sun-400" style={{ height: `${(v / Math.max(...env.sun.kwh_per_kwc_month)) * 48}px` }} title={`${Math.round(v)} kWh/kWc`} />
                      <span className="text-[10px] text-ink-400">{MONTHS[i]}</span>
                    </div>
                  ))}
                </div>
                <p className="text-xs text-ink-500">Climat : évaporation jusqu'à <b>{Math.max(...env.climate.et0_mm_day).toLocaleString('fr-FR', { maximumFractionDigits: 1 })} mm/jour</b> en été · pluie ≈ <b>{Math.round(env.climate.rain_mm_month.reduce((s, v) => s + v, 0))} mm/an</b> ({env.climate.source})</p>
                <label className="flex flex-wrap items-center gap-2 text-ink-700">Ombre sur l'endroit des panneaux
                  <select className="h-9 rounded-lg border border-ink-200 bg-white px-2 text-sm" value={view.profile.shading}
                    onChange={(e) => savePart({ shading: e.target.value as Shading }, 'Ombre enregistrée')}>
                    {(Object.keys(SHADING_LABEL) as Shading[]).map((k) => <option key={k} value={k}>{SHADING_LABEL[k]}</option>)}
                  </select>
                </label>
                <p className="flex flex-wrap items-center gap-2 text-xs text-ink-500">Source : {env.sun.source}{env.sun.assumption && <Badge tone="warn">estimation</Badge>}
                  <button className="ml-auto flex items-center gap-1 text-water-700 hover:underline disabled:opacity-50" onClick={refreshEnv} disabled={envBusy}><RefreshCw size={12} /> Mettre à jour sol, soleil et climat</button></p>
              </div>
            ) : <Empty>Ensoleillement pas encore connu.</Empty>}
          </Card>

          <Card title="4. Eau" icon={<Droplets size={16} className="text-water-600" />}>
            <div className="space-y-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-ink-700">Puits :</span>
                {view.profile.well ? <Badge tone="water">💧 placé sur la carte</Badge> : <Badge tone="warn">pas encore placé</Badge>}
                <Button size="sm" variant={mode === 'well' ? 'primary' : 'secondary'} onClick={() => setMode(mode === 'well' ? 'view' : 'well')}>
                  <MapPin size={13} /> {mode === 'well' ? 'Touchez la carte…' : view.profile.well ? 'Déplacer' : 'Placer sur la carte'}
                </Button>
                {view.profile.well && <Button size="sm" variant="ghost" onClick={() => savePart({ well: null }, 'Puits retiré')}>Retirer</Button>}
              </div>
              <label className="flex flex-wrap items-center gap-2 text-ink-700">Profondeur de l'eau dans le puits
                <input className="h-9 w-24 rounded-lg border border-ink-200 px-2 tabular-nums" inputMode="decimal" placeholder={String(view.default_well_depth_m)}
                  value={depth} onChange={(e) => setDepth(e.target.value)}
                  onBlur={() => { const v = Number(depth.replace(',', '.')); if (depth.trim() === '' ? view.profile.well_depth_m !== null : v !== view.profile.well_depth_m) savePart({ well_depth_m: depth.trim() === '' ? null : v }, 'Profondeur enregistrée') }}
                  onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }} /> m
                <InfoTip text="Plus l'eau est profonde, plus la pompe consomme d'énergie, et plus il faut de panneaux. Vide = 60 m supposés." />
              </label>
              <label className="flex items-center gap-2 text-ink-700">
                <input type="checkbox" className="h-4 w-4 accent-water-600" checked={view.profile.has_tank} onChange={(e) => savePart({ has_tank: e.target.checked }, e.target.checked ? 'Cuve ajoutée' : 'Cuve retirée')} />
                J'ai une cuve (ou un bassin) de stockage
              </label>
            </div>
          </Card>
        </div>

        {/* ---------------------------------------------------------------- carte */}
        <div className="lg:sticky lg:top-28 lg:self-start">
          <div ref={mapBox} className="relative isolate h-[55vh] min-h-[380px] scroll-mt-28 overflow-hidden rounded-2xl border border-ink-200/70 shadow-card">
            <MapContainer center={[p.geometry.coordinates[0][0][1], p.geometry.coordinates[0][0][0]]} zoom={17} maxZoom={19} scrollWheelZoom className="h-full w-full bg-ink-200">
              <TileLayer url={ESRI} maxNativeZoom={19} maxZoom={19} attribution="Imagerie &copy; Esri, Maxar, Earthstar Geographics" />
              <Polygon positions={toLatLngs(p.geometry)} pathOptions={{ color: '#fff', weight: 2, dashArray: '6 4', fillOpacity: 0.05 }} interactive={false} />
              {layers.zones && zones.map((z, i) => (
                <Polygon key={z.id} positions={toLatLngs(z.geometry)} pathOptions={{ color: cropColor(z.crop), weight: 2, fillOpacity: 0.35 }}>
                  <Tooltip direction="center" permanent className="!border-0 !bg-white/85 !px-1.5 !py-0.5 !text-[11px] !font-semibold !shadow">{z.name} · {areaText(zoneAreas[i])}</Tooltip>
                </Polygon>
              ))}
              {layers.solar && solar?.status === 'ok' && (
                <Polygon positions={toLatLngs(solar.footprint)} pathOptions={{ color: '#f59e0b', weight: 2, fillColor: '#fde047', fillOpacity: 0.6 }}>
                  <Tooltip direction="center" permanent className="!border-0 !bg-sun-50 !px-1.5 !py-0.5 !text-[11px] !font-semibold !shadow">☀️ {solar.panels} panneaux</Tooltip>
                </Polygon>
              )}
              {layers.sensors && sensorPoints.map((s, i) => (
                <Marker key={i} position={[s.lat, s.lon]} icon={badgeIcon(SENSOR_STYLE[s.key].letter, SENSOR_STYLE[s.key].color, s.dx, s.dy)}>
                  <Tooltip>{s.label}</Tooltip>
                </Marker>
              ))}
              {view.profile.well && (
                <Marker position={[view.profile.well.lat, view.profile.well.lon]} icon={WELL_ICON}><Tooltip>Puits</Tooltip></Marker>
              )}
              <MapTools mode={mode} onPickWell={pickWell} onDrawn={onDrawn} fit={p.geometry} />
            </MapContainer>
            {mode !== 'view' && (
              <div className="absolute inset-x-2 bottom-2 z-[600] flex justify-center">
                <div className="flex items-center gap-2 rounded-lg bg-water-600 px-3 py-2 text-sm font-medium text-white shadow">
                  {mode === 'well' ? 'Touchez la carte à l\'endroit du puits' : 'Touchez chaque coin de la zone, puis le premier coin pour fermer'}
                  <button onClick={() => setMode('view')} className="rounded bg-white/20 px-2 py-0.5 text-xs hover:bg-white/30">Annuler</button>
                </div>
              </div>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-700">
            <Layers size={13} className="text-ink-400" />
            {([['zones', 'Cultures'], ['solar', 'Panneaux'], ['sensors', 'Capteurs']] as const).map(([k, label]) => (
              <label key={k} className="flex items-center gap-1"><input type="checkbox" className="accent-water-600" checked={layers[k]} onChange={(e) => setLayers({ ...layers, [k]: e.target.checked })} />{label}</label>
            ))}
            <span className="ml-auto flex flex-wrap gap-2">
              {Object.values(SENSOR_STYLE).map((s) => <span key={s.letter} className="flex items-center gap-1"><span style={{ background: s.color }} className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-bold text-white">{s.letter}</span>{s.name}</span>)}
            </span>
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------------------ cultures */}
      <Card title="5. Qu'est-ce qui est planté où ?" icon={<Sprout size={16} className="text-leaf-600" />}
        actions={dirty && <Badge tone="warn">non enregistré</Badge>}>
        <div className="space-y-4 text-sm">
          {view.profile.zones_review && !dirty && (
            <p className="flex gap-2 rounded-lg bg-sun-50 p-2 text-sun-600"><AlertTriangle size={16} className="mt-0.5 shrink-0" />Le contour du champ a changé : vérifiez que les zones correspondent toujours. {view.zone_issues.join(' ; ')}</p>
          )}
          <div>
            <div className="flex h-4 overflow-hidden rounded-full bg-ink-100" aria-label="Répartition des cultures">
              {zones.map((z, i) => <div key={z.id} style={{ width: `${(zoneAreas[i] / (parcelM2 || 1)) * 100}%`, background: cropColor(z.crop) }} title={`${z.name} : ${areaText(zoneAreas[i])}`} />)}
            </div>
            <div className="mt-1 text-xs text-ink-500">Cultivé : <b>{areaText(plantedM2)}</b> · libre : <b>{areaText(Math.max(0, parcelM2 - plantedM2))}</b> (chemins, bâtiments, futur emplacement des panneaux…)</div>
          </div>

          <div className="flex flex-wrap items-center gap-2 rounded-xl bg-ink-50 p-2.5">
            <Button size="sm" onClick={wholeField}><LandPlot size={13} /> Tout le champ</Button>
            <span className="text-ink-300">|</span>
            <span className="flex items-center gap-1.5">Partager en
              <select className="h-8 rounded-lg border border-ink-200 bg-white px-1.5" value={strips} onChange={(e) => setStrips(Number(e.target.value))} aria-label="Nombre de bandes">
                {[2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
              </select> bandes
              <Segmented<'cote' | 'etage'> value={stripDir} onChange={setStripDir}
                options={[{ value: 'cote', label: <span className="flex items-center gap-1"><Columns3 size={12} /> côte à côte</span> }, { value: 'etage', label: <span className="flex items-center gap-1"><Rows3 size={12} /> l'une sur l'autre</span> }]} />
              <Button size="sm" onClick={makeStrips}>Partager</Button>
            </span>
            <span className="text-ink-300">|</span>
            <Button size="sm" variant={mode === 'draw' ? 'primary' : 'secondary'} onClick={() => setMode(mode === 'draw' ? 'view' : 'draw')}><PenLine size={13} /> {mode === 'draw' ? 'Dessin en cours…' : 'Dessiner une zone'}</Button>
          </div>
          <p className="text-xs text-ink-500">Les bandes ont toutes la même surface. Une zone dessinée est automatiquement ramenée à l'intérieur du champ, sans empiéter sur les autres.</p>

          {zones.length === 0 ? <Empty>Aucune culture indiquée. Choisissez « Tout le champ », « Partager en bandes » ou « Dessiner une zone ».</Empty> : (
            <ul className="divide-y divide-ink-100 rounded-xl border border-ink-200">
              {zones.map((z, i) => (
                <li key={z.id} className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-2 gap-y-2 p-2.5 sm:grid-cols-[auto_minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_auto_auto]">
                  <span className="h-4 w-4 rounded" style={{ background: cropColor(z.crop) }} />
                  <input className="h-9 min-w-0 rounded-lg border border-ink-200 px-2" value={z.name} maxLength={60} onChange={(e) => setZone(z.id, { name: e.target.value })} aria-label="Nom de la zone" />
                  <div className="col-span-2 flex gap-1.5 sm:col-span-1">
                    <select className="h-9 min-w-0 flex-1 rounded-lg border border-ink-200 bg-white px-2" value={z.crop} aria-label="Culture"
                      onChange={(e) => setZone(z.id, { crop: e.target.value, name: z.name.startsWith('Bande') || z.name.startsWith('Zone') || z.name === cropName(z.crop) ? cropName(e.target.value) : z.name })}>
                      {crops.map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}
                    </select>
                  </div>
                  <select className="col-span-2 h-9 min-w-0 rounded-lg border border-ink-200 bg-white px-2 sm:col-span-1" value={z.irrigation_method} aria-label="Arrosage"
                    onChange={(e) => setZone(z.id, { irrigation_method: e.target.value as Irrigation })}>
                    {(Object.keys(IRRIGATION_LABEL) as Irrigation[]).map((k) => <option key={k} value={k}>{IRRIGATION_LABEL[k]}</option>)}
                  </select>
                  <span className="text-xs tabular-nums text-ink-500">{areaText(zoneAreas[i])}</span>
                  <button className="justify-self-end rounded p-1.5 text-ink-400 hover:bg-red-50 hover:text-red-600" onClick={() => editZones(zones.filter((x) => x.id !== z.id))} aria-label={`Supprimer ${z.name}`}><Trash2 size={14} /></button>
                  {z.crop === 'autre' && (
                    <input className="col-span-2 h-8 rounded-lg border border-ink-200 px-2 text-xs sm:col-span-6" placeholder="Quelle culture ? (ex. luzerne)" maxLength={60}
                      value={z.crop_label ?? ''} onChange={(e) => setZone(z.id, { crop_label: e.target.value })} />
                  )}
                </li>
              ))}
            </ul>
          )}
          {zoneErr && <p className="rounded-lg bg-red-50 p-2 text-red-700">{zoneErr}</p>}
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="success" onClick={saveZones} disabled={!dirty || saving}><CheckCircle2 size={15} /> {saving ? 'Enregistrement…' : 'Enregistrer les cultures'}</Button>
            {dirty && <Button variant="ghost" onClick={() => { setZones(view.profile.zones); setDirty(false); setZoneErr(null) }}>Annuler les changements</Button>}
            {dirty && <span className="text-xs text-ink-500">Les recommandations seront recalculées après l'enregistrement.</span>}
          </div>
        </div>
      </Card>

      {/* ------------------------------------------------------------------ recommandations */}
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold text-ink-900">6. Équipement conseillé</h2>
        <Badge tone="warn">estimations</Badge>
        {recoBusy && <span className="text-sm text-ink-500">calcul…</span>}
      </div>
      {recoErr && <Empty>Recommandations indisponibles : {recoErr}. <button className="text-water-700 underline" onClick={loadReco}>Réessayer</button></Empty>}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Panneaux solaires pour la pompe" icon={<SunMedium size={16} className="text-sun-600" />}>
          {!solar ? <Empty>Calcul en cours…</Empty> : solar.status !== 'ok' ? <Empty>{solar.message}</Empty> : (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <Stat label="Panneaux" value={solar.panels} sub={`${solar.panel_wp} W chacun · ${solar.kwc.toLocaleString('fr-FR')} kWc`} />
                <Stat label="Place au sol" value={`${Math.round(solar.area_m2)} m²`} sub={`${solar.layout.width_m} m × ${solar.layout.depth_m} m`} />
                <Stat label="Coût estimé" value={tnd(solar.total_tnd)} sub="pompe existante non comptée" />
                <Stat label="Économie" value={`${tnd(solar.savings_tnd_year)}/an`} sub={solar.payback_years ? `rentabilisé en ~${solar.payback_years.toLocaleString('fr-FR')} ans` : '—'} />
              </div>
              <div className="rounded-xl border border-sun-400/40 bg-sun-50 p-3">
                <div className="font-semibold text-ink-900">📍 Où les mettre</div>
                <p className="text-ink-700">{solar.where.charAt(0).toUpperCase() + solar.where.slice(1)}.</p>
                <p className="mt-1 text-ink-700">{solar.layout.rows} rangée{solar.layout.rows > 1 ? 's' : ''} de {solar.layout.cols} panneaux, tournés <b>{solar.facing}</b>, inclinés à <b>{solar.tilt_deg}°</b>, rangées espacées de {solar.layout.pitch_m.toLocaleString('fr-FR')} m pour ne pas se faire d'ombre en hiver.
                  {solar.distance_to_well_m !== null && <> À {solar.distance_to_well_m} m du puits.</>}</p>
                {!solar.inside && <p className="mt-1 flex gap-1.5 text-sun-600"><AlertTriangle size={14} className="mt-0.5 shrink-0" />Gardez une bande libre dans le champ si vous préférez les avoir sur votre terrain.</p>}
              </div>
              <p className="text-ink-700"><b>Pourquoi {solar.panels} ?</b> Le mois le plus exigeant est <b>{solar.design_month}</b> : {solar.design_water_m3_day.toLocaleString('fr-FR')} m³ d'eau par jour à remonter de <b>{solar.head_m} m</b> (puits {solar.well_depth_m} m + pression d'arrosage), soit une pompe d'environ {solar.pump_kw.toLocaleString('fr-FR')} kW. Les panneaux couvrent ce mois-là avec 10 % de marge.
                {solar.well_assumed && <span className="text-sun-600"> Profondeur du puits supposée ({solar.well_depth_m} m) : indiquez la vraie dans « Eau ».</span>}</p>
              <div className="h-52">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={solar.monthly} margin={{ top: 5, right: 5, left: -15, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#eef0f4" />
                    <XAxis dataKey="month" tick={{ fontSize: 11 }} interval={0} />
                    <YAxis tick={{ fontSize: 11 }} />
                    <RTooltip formatter={(v: number, n: string) => [`${Math.round(v).toLocaleString('fr-FR')} kWh`, n]} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Bar dataKey="need_kwh" name="Énergie pour pomper" fill="#2f93ea" radius={[3, 3, 0, 0]} />
                    <Line dataKey="pv_kwh" name="Production des panneaux" stroke="#f59e0b" strokeWidth={2.5} dot={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <p className="text-xs text-ink-500">Quand la ligne orange passe au-dessus des barres bleues, le soleil suffit pour pomper toute l'eau du mois. Eau pompée sur l'année : {solar.water_m3_year.toLocaleString('fr-FR')} m³.</p>
              <Details summary="Détail du coût">
                <table className="w-full text-sm">
                  <thead><tr className="text-left text-xs text-ink-500"><th className="py-1">Élément</th><th className="py-1 text-right">Qté</th><th className="py-1 text-right">Prix</th><th className="py-1 text-right">Total</th></tr></thead>
                  <tbody>
                    {solar.cost_lines.map((l) => <tr key={l.item} className="border-t border-ink-100"><td className="py-1">{l.item}</td><td className="py-1 text-right tabular-nums">{l.qty}</td><td className="py-1 text-right tabular-nums">{tnd(l.unit_tnd)}</td><td className="py-1 text-right tabular-nums">{tnd(l.total_tnd)}</td></tr>)}
                    <tr className="border-t-2 border-ink-200 font-semibold"><td className="py-1" colSpan={3}>Total estimé</td><td className="py-1 text-right tabular-nums">{tnd(solar.total_tnd)}</td></tr>
                  </tbody>
                </table>
              </Details>
            </div>
          )}
        </Card>

        <Card title="Capteurs conseillés" icon={<Cpu size={16} className="text-water-600" />}>
          {!sensors ? <Empty>Calcul en cours…</Empty> : (
            <div className="space-y-3 text-sm">
              <div className="grid grid-cols-3 gap-2">
                <Stat label="Appareils" value={sensors.devices} />
                <Stat label="Matériel" value={tnd(sensors.equipment_tnd)} />
                <Stat label="Total posé" value={tnd(sensors.total_tnd)} sub={`dont pose ${tnd(sensors.installation_tnd)}`} />
              </div>
              {sensors.zones_assumed && <p className="flex gap-1.5 rounded-lg bg-sun-50 p-2 text-sun-600"><AlertTriangle size={14} className="mt-0.5 shrink-0" />Cultures pas encore indiquées : calcul fait comme si tout le champ avait une seule culture.</p>}
              <ul className="space-y-2">
                {sensors.items.map((it) => (
                  <li key={it.key} className="rounded-xl border border-ink-200 p-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span style={{ background: SENSOR_STYLE[it.key].color }} className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-bold text-white">{SENSOR_STYLE[it.key].letter}</span>
                      <span className="font-semibold text-ink-900">{it.count > 0 ? `${it.count} × ` : ''}{it.label}</span>
                      <InfoTip wide text={it.why} />
                      <span className="ml-auto tabular-nums text-ink-700">{it.count > 0 ? tnd(it.total_tnd) : <Badge tone="ok">déjà prévue</Badge>}</span>
                    </div>
                    <p className="mt-1 text-xs text-ink-500">📍 {it.where}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      </div>

      {reco && (
        <Details summary={<span className="inline-flex items-center gap-1.5"><Wallet size={14} /> D'où viennent ces chiffres ?</span>}>
          <div className="space-y-1.5 text-xs text-ink-700">
            <p><b>Prix :</b> {reco.note}</p>
            <p><b>Sol :</b> {reco.env_sources.soil} · <b>Soleil :</b> {reco.env_sources.sun} · <b>Climat :</b> {reco.env_sources.climate}{reco.env_assumption && ' — certaines valeurs sont des estimations (service indisponible).'}</p>
            <p><b>Besoin en eau :</b> évaporation du mois × coefficient de la culture au stade du mois (calendrier cultural FAO-56) − 80 % de la pluie, divisé par l'efficacité de l'arrosage (goutte-à-goutte 90 %, aspersion 75 %, gravitaire 60 %).</p>
            <p><b>Économie :</b> électricité solaire utilisée pour pomper, au prix du réseau de jour (0,22 TND/kWh).</p>
          </div>
        </Details>
      )}

      <ConfirmDialog open={confirm !== null} onClose={() => setConfirm(null)} title={confirm?.title ?? ''} message={confirm?.message ?? ''}
        confirmLabel={confirm?.label ?? 'OK'} variant="warn" onConfirm={() => confirm?.run()} />
    </div>
  )
}
