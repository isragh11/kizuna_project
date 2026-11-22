// Mes terres agricoles : définir le contour de ses champs sur la photo satellite (dessin, marche GPS,
// dimensions, plan cadastral), le corriger, puis l'enregistrer (GeoJSON, /api/parcels).
// Intégré depuis la branche master (MapPage), adapté au dashboard et rendu le plus simple possible :
// guide en 3 étapes, recherche de lieu, longueurs des côtés sur la carte, annuler, démêler, brouillon
// conservé, contrôles identiques au serveur (croisements, surfaces, chevauchements, noms en double).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { Circle, CircleMarker, MapContainer, Marker, Polygon, Polyline, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import '@geoman-io/leaflet-geoman-free'
import 'leaflet/dist/leaflet.css'
import '@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css'
import {
  AlertTriangle, Check, CheckCircle2, ClipboardList, Download, Eraser, Footprints, LandPlot, Layers, LocateFixed, Move, PenLine,
  Pencil, RotateCw, Ruler, ScrollText, Search, Spline, Trash2, Undo2, Wand2, X,
} from 'lucide-react'
import ParcelProfile from './ParcelProfile'
import { GpsRecorder, type GpsPoint } from '../components/GpsRecorder'
import { ManualDimensions } from '../components/ManualDimensions'
import { PlanUpload } from '../components/PlanUpload'
import { Badge, Button, Card, ConfirmDialog, Empty, InfoTip } from '../components/ui'
import { apiGet } from '../lib/api'
import { useNotify } from '../lib/actions'
import { useLive } from '../lib/live'
import {
  METHOD_LABEL, areaText, assess, edgeLabels, failText, nameTaken, parcelsApi, parseCoords, uniqueName, untangle,
  type InputMethod, type Parcel, type PlanExtraction, type PolygonGeometry,
} from '../lib/parcels'

type EditMode = 'none' | 'edit' | 'drag' | 'rotate'
type Basemap = 'satellite' | 'plan'

const METHODS: { id: InputMethod; label: string; icon: typeof PenLine; help: string }[] = [
  { id: 'map_drawing', label: 'Dessiner', icon: PenLine, help: "Touchez l'outil ⬠ en haut à gauche de la carte, puis chaque coin du champ. Touchez le premier coin pour fermer. L'outil ▭ trace un rectangle." },
  { id: 'gps_tracking', label: 'Marcher', icon: Footprints, help: 'Faites le tour du champ avec votre téléphone : sa position GPS trace le contour.' },
  { id: 'manual_dimensions', label: 'Dimensions', icon: Ruler, help: 'Vous connaissez les mesures ou la surface ? Entrez-les : le champ est posé sur la carte.' },
  { id: 'cadastral_document', label: 'Plan cadastral', icon: ScrollText, help: 'Envoyez une photo ou un PDF du plan : le contour est détecté, puis vous le placez et le vérifiez.' },
]
const CROPS = ['Tomates', 'Oliviers', 'Piments', 'Blé', 'Orge', 'Pommes de terre', 'Agrumes', 'Amandiers', 'Pastèques', 'Fourrage']

const DEFAULT_CENTER: [number, number] = [34.98, 9.5]   // Sidi Bouzid, si la ferme n'est pas encore connue
const EDIT_STYLE = { color: '#22c55e', weight: 3, fillOpacity: 0.15 }
const ESRI_IMAGERY = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
const ESRI_LABELS = 'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}'
const OSM = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const DRAFT_KEY = 'aquamind:terres:brouillon'
const MAX_LABELLED_EDGES = 16

const toLatLngs = (g: PolygonGeometry) => g.coordinates[0].slice(0, -1).map(([lon, lat]) => [lat, lon] as [number, number])
const same = (a: PolygonGeometry | null, b: PolygonGeometry | null) => JSON.stringify(a) === JSON.stringify(b)

interface Draft { geometry: PolygonGeometry; origin: InputMethod; name: string; editingId: string | null; savedAt: string }
function readDraft(): Draft | null {
  try { const d = JSON.parse(localStorage.getItem(DRAFT_KEY) || 'null'); return d?.geometry?.type === 'Polygon' ? d : null } catch { return null }
}
function writeDraft(d: Draft | null) {
  try { if (d) localStorage.setItem(DRAFT_KEY, JSON.stringify(d)); else localStorage.removeItem(DRAFT_KEY) } catch { /* stockage indisponible */ }
}

/** Longueur des côtés affichée au milieu de chaque bord (mise à jour pendant qu'on déplace un coin). */
function drawLabels(group: L.LayerGroup, g: PolygonGeometry | null) {
  group.clearLayers()
  if (!g) return
  const edges = edgeLabels(g)
  if (edges.length > MAX_LABELLED_EDGES) return       // tour GPS : trop de côtés, on n'encombre pas la carte
  for (const e of edges) {
    if (e.m < 3) continue
    L.marker(e.at, {
      interactive: false, keyboard: false, pmIgnore: true, snapIgnore: true,
      icon: L.divIcon({ className: '', iconSize: [0, 0], html: `<span class="pointer-events-none -translate-x-1/2 -translate-y-1/2 inline-block whitespace-nowrap rounded bg-ink-900/75 px-1 py-px text-[11px] font-semibold text-white">${Math.round(e.m).toLocaleString('fr-FR')} m</span>` }),
    } as any).addTo(group)
  }
}

/** Éditeur Geoman : outils de dessin + une seule forme modifiable (coins, déplacement, rotation). */
function Editor({ loadKey, geometryRef, mode, onDraw, onEdit }: {
  loadKey: number
  geometryRef: React.MutableRefObject<PolygonGeometry | null>
  mode: EditMode
  onDraw: (g: PolygonGeometry) => void
  onEdit: (g: PolygonGeometry) => void
}) {
  const map = useMap()
  const layerRef = useRef<L.Polygon | null>(null)
  const labelsRef = useRef<L.LayerGroup | null>(null)
  const cb = useRef({ onDraw, onEdit })
  cb.current = { onDraw, onEdit }

  const read = (layer: L.Polygon) => {
    const gj = layer.toGeoJSON()
    return gj.geometry.type === 'Polygon' ? (gj.geometry as unknown as PolygonGeometry) : null
  }
  const attach = (layer: L.Polygon) => {
    const emit = () => { const g = read(layer); if (g) { cb.current.onEdit(g); drawLabels(labelsRef.current!, g) } }
    layer.on('pm:edit pm:rotateend pm:dragend', emit)
    layer.on('pm:markerdrag pm:rotate pm:drag', () => drawLabels(labelsRef.current!, read(layer)))
  }
  const drop = () => {
    const old = layerRef.current
    if (old) { (old as any).pm?.disable?.(); (old as any).pm?.disableRotate?.(); old.remove() }
    layerRef.current = null
    labelsRef.current?.clearLayers()
  }

  useEffect(() => {
    labelsRef.current = L.layerGroup().addTo(map)
    const pm = (map as any).pm
    pm.setLang('fr')
    pm.addControls({
      position: 'topleft', drawPolygon: true, drawRectangle: true, drawMarker: false, drawCircleMarker: false,
      drawCircle: false, drawPolyline: false, drawText: false, editMode: false, dragMode: false, cutPolygon: false,
      removalMode: false, rotateMode: false,
    })
    pm.setGlobalOptions({ allowSelfIntersection: false, snappable: true, snapDistance: 15, pathOptions: EDIT_STYLE, templineStyle: { color: '#22c55e' }, hintlineStyle: { color: '#22c55e', dashArray: '5,5' } })
    const onCreate = (e: any) => {
      const layer = e.layer as L.Polygon
      const g = read(layer)
      if (!g) { layer.remove(); return }
      drop()                                   // un seul champ à la fois dans l'éditeur
      layerRef.current = layer
      attach(layer)
      drawLabels(labelsRef.current!, g)
      cb.current.onDraw(g)
    }
    map.on('pm:create', onCreate)
    return () => { map.off('pm:create', onCreate); pm.disableDraw?.(); pm.removeControls(); drop(); labelsRef.current?.remove() }
  }, [map])

  // Forme chargée depuis l'extérieur (GPS, dimensions, plan, champ enregistré, annuler) ou effacée
  useEffect(() => {
    if (loadKey === 0) return
    drop()
    const g = geometryRef.current
    if (!g) return
    const layer = L.polygon(toLatLngs(g), EDIT_STYLE)
    layer.addTo(map)
    layerRef.current = layer
    attach(layer)
    drawLabels(labelsRef.current!, g)
    map.fitBounds(layer.getBounds(), { padding: [50, 50], maxZoom: 18 })
  }, [loadKey, map, geometryRef])

  // Mode d'édition : coins / déplacer / tourner (un seul à la fois)
  useEffect(() => {
    const pm = (layerRef.current as any)?.pm
    if (!pm) return
    pm.disable(); pm.disableLayerDrag?.(); pm.disableRotate?.()
    // contour déjà croisé (GPS) : on autorise l'édition pour pouvoir le démêler, sinon Geoman bloque tout
    const kinked = geometryRef.current ? assess(geometryRef.current).m.selfIntersecting : false
    if (mode === 'edit') pm.enable({ allowSelfIntersection: kinked, snappable: true, preventMarkerRemoval: false })
    if (mode === 'drag') pm.enableLayerDrag()
    if (mode === 'rotate') pm.enableRotate()
  }, [mode, loadKey, geometryRef])

  return null
}

/** Clic sur la carte pour choisir un point (méthode « Dimensions »). */
function PickClick({ active, onPick }: { active: boolean; onPick: (p: [number, number]) => void }) {
  const map = useMapEvents({
    click: (e) => {
      if (!active || (map as any).pm?.globalDrawModeEnabled?.()) return
      onPick([e.latlng.lat, e.latlng.lng])
    },
  })
  useEffect(() => {
    map.getContainer().style.cursor = active ? 'crosshair' : ''
  }, [active, map])
  return null
}

function MapHandle({ onMap }: { onMap: (m: L.Map) => void }) {
  const map = useMap()
  useEffect(() => { onMap(map) }, [map, onMap])
  return null
}

/** Recherche d'un village, d'une adresse ou de coordonnées. Les coordonnées sont lues sans réseau ;
 *  un nom de lieu est cherché dans OpenStreetMap (Nominatim) seulement quand on valide. */
function PlaceSearch({ onGo }: { onGo: (lat: number, lon: number, bbox?: [[number, number], [number, number]]) => void }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [results, setResults] = useState<{ name: string; lat: number; lon: number; bbox?: [[number, number], [number, number]] }[]>([])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setMsg(''); setResults([])
    const text = q.trim()
    if (!text) return
    const c = parseCoords(text)
    if (c) { onGo(c[0], c[1]); return }
    if (/^[\d\s.,°'"NSEWO-]+$/i.test(text)) { setMsg('Coordonnées illisibles. Exemple : 34.98, 9.49'); return }
    setBusy(true)
    const ctrl = new AbortController()
    const t = setTimeout(() => ctrl.abort(), 10000)
    try {
      const r = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&accept-language=fr&q=${encodeURIComponent(text)}`, { signal: ctrl.signal })
      if (!r.ok) throw new Error(String(r.status))
      const data: any[] = await r.json()
      const list = data.map((d) => ({
        name: String(d.display_name), lat: Number(d.lat), lon: Number(d.lon),
        bbox: d.boundingbox ? [[Number(d.boundingbox[0]), Number(d.boundingbox[2])], [Number(d.boundingbox[1]), Number(d.boundingbox[3])]] as [[number, number], [number, number]] : undefined,
      })).filter((d) => Number.isFinite(d.lat) && Number.isFinite(d.lon))
      if (!list.length) setMsg('Aucun lieu trouvé. Essayez le nom du village ou de la délégation.')
      else if (list.length === 1) onGo(list[0].lat, list[0].lon, list[0].bbox)
      else setResults(list)
    } catch {
      setMsg('Recherche indisponible (pas d\'internet ?). Vous pouvez saisir des coordonnées.')
    } finally { clearTimeout(t); setBusy(false) }
  }

  return (
    <div className="pointer-events-auto w-full max-w-sm">
      <form onSubmit={submit} className="flex overflow-hidden rounded-lg bg-white shadow ring-1 ring-ink-200">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Village, lieu ou coordonnées…" aria-label="Rechercher un lieu"
          className="min-w-0 flex-1 px-3 py-2 text-sm focus:outline-none" />
        {q && <button type="button" onClick={() => { setQ(''); setResults([]); setMsg('') }} className="px-1.5 text-ink-400 hover:text-ink-700" aria-label="Effacer la recherche"><X size={14} /></button>}
        <button type="submit" disabled={busy} className="flex items-center gap-1 bg-water-600 px-3 text-sm font-medium text-white hover:bg-water-700 disabled:opacity-60">
          <Search size={14} /><span className="hidden sm:inline">{busy ? '…' : 'Chercher'}</span>
        </button>
      </form>
      {(results.length > 0 || msg) && (
        <div className="mt-1 overflow-hidden rounded-lg bg-white text-sm shadow ring-1 ring-ink-200">
          {msg && <div className="p-2 text-ink-700">{msg}</div>}
          {results.map((r, i) => (
            <button key={i} onClick={() => { onGo(r.lat, r.lon, r.bbox); setResults([]) }} className="block w-full truncate border-t border-ink-100 px-3 py-2 text-left first:border-0 hover:bg-water-50">{r.name}</button>
          ))}
          <div className="border-t border-ink-100 px-3 py-1 text-[10px] text-ink-400">Recherche : © OpenStreetMap</div>
        </div>
      )}
    </div>
  )
}

function Step({ n, label, done, active }: { n: number; label: string; done: boolean; active: boolean }) {
  return (
    <div className={`flex min-w-0 flex-1 items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs ${active ? 'bg-water-50 font-semibold text-water-700' : done ? 'text-leaf-700' : 'text-ink-400'}`}>
      <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] ${done ? 'bg-leaf-500 text-white' : active ? 'bg-water-600 text-white' : 'bg-ink-100'}`}>{done ? <Check size={12} /> : n}</span>
      <span className="truncate">{label}</span>
    </div>
  )
}

/** Paramètre de l'adresse après « # » : #terres?champ=<id> (profil) ou #terres?modifier=<id> (contour). */
function useHashParam(name: string): string | null {
  const read = () => new URLSearchParams(window.location.hash.split('?')[1] ?? '').get(name)
  const [value, setValue] = useState(read)
  useEffect(() => {
    const on = () => setValue(read())
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])                                                     // eslint-disable-line react-hooks/exhaustive-deps
  return value
}

/** « Mes terres » : la liste et l'éditeur de contours, ou le profil d'un champ. */
export default function TerresPage() {
  const champ = useHashParam('champ')
  return champ
    ? <ParcelProfile key={champ} id={champ} onBack={() => { window.location.hash = 'terres' }} />
    : <MapPage />
}

function MapPage() {
  const live = useLive()
  const notify = useNotify()
  const mapRef = useRef<L.Map | null>(null)
  const [method, setMethod] = useState<InputMethod>('map_drawing')
  const [origin, setOrigin] = useState<InputMethod>('map_drawing')
  const [geometry, setGeometry] = useState<PolygonGeometry | null>(null)
  const geometryRef = useRef<PolygonGeometry | null>(null)
  const originRef = useRef<InputMethod>('map_drawing')
  const history = useRef<{ g: PolygonGeometry | null; origin: InputMethod }[]>([])
  const [historyLen, setHistoryLen] = useState(0)
  const [loadKey, setLoadKey] = useState(0)
  const [panelKey, setPanelKey] = useState(0)
  const [mode, setMode] = useState<EditMode>('none')
  const [basemap, setBasemap] = useState<Basemap>('satellite')
  const [name, setName] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [plan, setPlan] = useState<PlanExtraction | null>(null)
  const [planChecked, setPlanChecked] = useState(false)
  const [track, setTrack] = useState<GpsPoint[]>([])
  const [gpsRecording, setGpsRecording] = useState(false)
  const [me, setMe] = useState<{ pos: [number, number]; acc: number } | null>(null)
  const [pin, setPin] = useState<[number, number] | null>(null)
  const [pickCb, setPickCb] = useState<((p: [number, number]) => void) | null>(null)
  const [parcels, setParcels] = useState<Parcel[] | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [outdated, setOutdated] = useState(false)
  const [saving, setSaving] = useState(false)
  const [toDelete, setToDelete] = useState<Parcel | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)
  const [restored, setRestored] = useState<string | null>(null)
  const [farmCenter, setFarmCenter] = useState<[number, number] | null>(null)

  const others = useMemo(() => (parcels ?? []).filter((p) => p.parcel_id !== editingId), [parcels, editingId])
  const check = useMemo(() => (geometry ? assess(geometry, others) : null), [geometry, others])
  const names = (parcels ?? []).map((p) => p.parcel_name)
  const defaultName = uniqueName('Champ', names)
  const dupe = name.trim() ? nameTaken(name, parcels ?? [], editingId) : undefined
  const needsPlanCheck = origin === 'cadastral_document' && !planChecked && !editingId
  const canSave = !!geometry && !!check && !check.errors.length && !needsPlanCheck && !dupe && !saving

  const refresh = useCallback(async () => {
    try {
      setParcels(await parcelsApi.list()); setListError(null); setOutdated(false)
    } catch (e: any) {
      // 404 « Not Found » : l'API tourne mais a été lancée avant l'ajout de « Mes terres »
      if (e?.status === 404 && e?.message === 'Not Found') { setOutdated(true); setListError('serveur pas à jour') }
      else setListError(e?.status ? e.message : 'Serveur injoignable')
    }
  }, [])
  useEffect(() => { refresh() }, [refresh])

  // ---------------------------------------------------------------- forme courante et historique
  const pushHistory = () => {
    history.current.push({ g: geometryRef.current, origin: originRef.current })
    if (history.current.length > 40) history.current.shift()
    setHistoryLen(history.current.length)
  }
  /** Forme venant d'un formulaire, d'un champ enregistré ou d'« annuler » : chargée dans l'éditeur. */
  const loadShape = useCallback((g: PolygonGeometry | null, from: InputMethod, record = true) => {
    if (record && (geometryRef.current || g) && !same(geometryRef.current, g)) pushHistory()
    geometryRef.current = g; originRef.current = from
    setGeometry(g); setOrigin(from); setMode('none'); setLoadKey((k) => k + 1)
  }, [])
  const onDraw = useCallback((g: PolygonGeometry) => {
    pushHistory()
    geometryRef.current = g; originRef.current = 'map_drawing'
    setGeometry(g); setOrigin('map_drawing'); setMode('none')
  }, [])
  const onEdit = useCallback((g: PolygonGeometry) => {
    if (same(geometryRef.current, g)) return
    pushHistory()
    geometryRef.current = g
    setGeometry(g)
  }, [])
  const undo = useCallback(() => {
    const prev = history.current.pop()
    setHistoryLen(history.current.length)
    if (prev) loadShape(prev.g, prev.origin, false)
  }, [loadShape])
  const onTrack = useCallback((pts: GpsPoint[]) => setTrack(pts), [])

  // Ctrl+Z (hors champs de saisie)
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z' && tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') { e.preventDefault(); undo() }
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [undo])

  // ---------------------------------------------------------------- brouillon : rien ne se perd
  useEffect(() => {
    const d = readDraft()
    if (!d) return
    setName(d.name ?? ''); setEditingId(d.editingId ?? null); setMethod(d.origin)
    loadShape(d.geometry, d.origin, false)
    setRestored(new Date(d.savedAt).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' }))
  }, [loadShape])
  useEffect(() => {
    writeDraft(geometry ? { geometry, origin, name, editingId, savedAt: new Date().toISOString() } : null)
  }, [geometry, origin, name, editingId])
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (geometryRef.current || gpsRecording) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [gpsRecording])
  // Le champ en cours de modification a été supprimé ailleurs : on le recréera comme nouveau
  useEffect(() => {
    if (editingId && parcels && !parcels.some((p) => p.parcel_id === editingId)) {
      setEditingId(null)
      notify(false, 'Ce champ a été supprimé entre-temps : il sera enregistré comme un nouveau champ.')
    }
  }, [parcels, editingId, notify])

  // Centre de la ferme (parcelles A/B/C du site choisi), pour ouvrir la carte au bon endroit
  useEffect(() => {
    apiGet<any>(`/farm?site=${live.site}`).then(({ data }) => {
      const plots = (data?.farm?.plots ?? []).filter((p: any) => Number.isFinite(p.lat) && Number.isFinite(p.lon))
      if (!plots.length) return
      const c: [number, number] = [plots.reduce((s: number, p: any) => s + p.lat, 0) / plots.length, plots.reduce((s: number, p: any) => s + p.lon, 0) / plots.length]
      setFarmCenter(c)
      if (!geometryRef.current) mapRef.current?.setView(c, 16)
    }).catch(() => { /* hors-ligne : on garde le centre par défaut */ })
  }, [live.site])

  const getCenter = useCallback((): [number, number] | null => {
    const c = mapRef.current?.getCenter()
    return c ? [c.lat, c.lng] : null
  }, [])
  const onMap = useCallback((map: L.Map) => { mapRef.current = map }, [])
  const requestPick = useCallback((cb: (p: [number, number]) => void) => setPickCb(() => cb), [])
  const goTo = (lat: number, lon: number, bbox?: [[number, number], [number, number]]) => {
    if (bbox) mapRef.current?.fitBounds(bbox, { maxZoom: 17 })
    else mapRef.current?.setView([lat, lon], 17)
  }

  const reset = () => {
    history.current = []; setHistoryLen(0)
    loadShape(null, method, false); setEditingId(null); setName(''); setTrack([]); setPlanChecked(false); setPlan(null)
    setPanelKey((k) => k + 1); setRestored(null); setPin(null); setPickCb(null)
  }

  const locate = () => {
    if (!('geolocation' in navigator)) { notify(false, "Cet appareil ne donne pas sa position."); return }
    navigator.geolocation.getCurrentPosition(
      (p) => {
        const pos: [number, number] = [p.coords.latitude, p.coords.longitude]
        setMe({ pos, acc: p.coords.accuracy })
        mapRef.current?.setView(pos, 18)
      },
      (e) => notify(false, e.code === 1 ? 'Localisation refusée : autorisez-la dans les réglages du navigateur.'
        : e.code === 2 ? 'Position indisponible : activez le GPS.' : 'Le GPS ne répond pas. Réessayez à découvert.'),
      { enableHighAccuracy: true, maximumAge: 10000, timeout: 20000 },
    )
  }

  const fixCrossing = () => {
    if (!geometry) return
    const ring = untangle(geometry.coordinates[0])
    if (!ring) { notify(false, 'Impossible de démêler automatiquement : utilisez « Modifier les coins ».'); return }
    loadShape({ type: 'Polygon', coordinates: [ring] }, origin)
    notify(true, 'Contour démêlé : vérifiez-le sur la photo.')
  }

  const save = async () => {
    if (!geometry || !check || !canSave) return
    const parcelName = name.trim().replace(/\s+/g, ' ') || defaultName
    setSaving(true)
    try {
      const r = editingId
        ? await parcelsApi.update(editingId, { parcel_name: parcelName, geometry })
        : await parcelsApi.create({
          parcel_name: parcelName, input_method: origin, geometry,
          source_document_id: origin === 'cadastral_document' ? plan?.document_id ?? null : null,
        })
      if (r.status === 404 && editingId) { setEditingId(null); notify(false, 'Ce champ n\'existe plus : appuyez de nouveau sur Enregistrer pour le créer.'); refresh(); return }
      if (!r.ok) { notify(false, failText(r, 'Enregistrement refusé')); return }
      notify(true, `« ${r.data.parcel_name} » enregistré : ${areaText(r.data.area_m2)}`)
      writeDraft(null); reset(); refresh()
    } catch {
      notify(false, 'Serveur injoignable : le champ n\'a pas été enregistré. Votre tracé est gardé, réessayez plus tard.')
    } finally { setSaving(false) }
  }

  /** Clic sur un champ : jamais pendant un dessin (le clic sert à poser un coin), ni par-dessus un contour en cours. */
  /** Profil du champ : seulement si aucun tracé n'est en cours (on quitterait l'éditeur). */
  const openProfile = (p: Parcel) => {
    if (geometryRef.current || gpsRecording) { notify(false, 'Enregistrez ou abandonnez le tracé en cours avant d\'ouvrir un profil.'); return }
    window.location.hash = `terres?champ=${encodeURIComponent(p.parcel_id)}`
  }

  // « Modifier le contour » depuis le profil : #terres?modifier=<id> ouvre ce champ dans l'éditeur
  const toEdit = useHashParam('modifier')
  useEffect(() => {
    if (!toEdit || !parcels) return
    const p = parcels.find((x) => x.parcel_id === toEdit)
    window.history.replaceState(null, '', '#terres')
    if (p && !geometryRef.current) openParcel(p)
  }, [toEdit, parcels])                                     // eslint-disable-line react-hooks/exhaustive-deps

  const openParcel = (p: Parcel) => {
    if ((mapRef.current as any)?.pm?.globalDrawModeEnabled?.() || mode !== 'none' || pickCb) return
    if (p.parcel_id === editingId) return
    if (geometryRef.current || gpsRecording) { notify(false, 'Enregistrez ou annulez le champ en cours avant d\'en ouvrir un autre.'); return }
    history.current = []; setHistoryLen(0)
    setEditingId(p.parcel_id); setName(p.parcel_name); setTrack([]); setPlanChecked(true); setRestored(null)
    loadShape(p.geometry, p.input_method, false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const setStatus = async (p: Parcel, status: 'pending' | 'validated') => {
    try {
      const r = await parcelsApi.update(p.parcel_id, { validation_status: status })
      notify(r.ok, r.ok ? (status === 'validated' ? `« ${p.parcel_name} » marqué comme vérifié` : `« ${p.parcel_name} » à revérifier`) : failText(r, 'Modification refusée'))
      refresh()
    } catch { notify(false, 'Serveur injoignable') }
  }

  const remove = async (p: Parcel) => {
    try {
      const r = await parcelsApi.remove(p.parcel_id)
      notify(r.ok || r.status === 404, r.ok || r.status === 404 ? `« ${p.parcel_name} » supprimé` : failText(r, 'Suppression refusée'))
      if (editingId === p.parcel_id) reset()
      refresh()
    } catch { notify(false, 'Serveur injoignable') }
  }

  const exportGeoJson = () => {
    const fc = { type: 'FeatureCollection', features: (parcels ?? []).map(({ geometry: g, ...props }) => ({ type: 'Feature', id: props.parcel_id, geometry: g, properties: props })) }
    const url = URL.createObjectURL(new Blob([JSON.stringify(fc, null, 2)], { type: 'application/geo+json' }))
    const a = Object.assign(document.createElement('a'), { href: url, download: 'mes-terres.geojson' })
    a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const current = METHODS.find((x) => x.id === method)!
  const shown = (parcels ?? []).filter((p) => p.parcel_id !== editingId)
  const totalM2 = (parcels ?? []).reduce((s, p) => s + p.area_m2, 0)
  const m = check?.m
  const verified = !!check && !check.errors.length && !needsPlanCheck

  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-700">
        Tracez le contour de chacun de vos champs sur la photo satellite. AquaMind connaîtra ainsi leur surface exacte
        (en hectares), utile pour calculer l'eau dont ils ont besoin.
      </p>

      {outdated && (
        <div className="flex flex-wrap items-start gap-3 rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert">
          <AlertTriangle size={18} className="mt-0.5 shrink-0" />
          <div className="min-w-0 flex-1">
            <b>Le serveur AquaMind n'est pas à jour.</b> Il a été lancé avant l'ajout de « Mes terres » : l'envoi de plans
            et l'enregistrement des champs ne fonctionneront pas. Arrêtez l'API (Ctrl+C dans sa fenêtre) puis relancez-la :
            <code className="mt-1 block overflow-x-auto whitespace-nowrap rounded bg-white/70 px-2 py-1 text-xs text-ink-900">.\.venv\Scripts\python -m uvicorn api.main:app --host 127.0.0.1 --port 8000</code>
          </div>
          <Button size="sm" onClick={refresh}>J'ai redémarré, vérifier</Button>
        </div>
      )}

      {restored && (
        <div className="flex flex-wrap items-center gap-2 rounded-xl border border-water-200 bg-water-50 p-3 text-sm text-water-700">
          <span className="flex-1">Votre tracé non enregistré de {restored} a été repris.</span>
          <Button size="sm" onClick={() => setRestored(null)}>Continuer</Button>
          <Button size="sm" variant="ghost" onClick={() => { writeDraft(null); reset() }}>L'abandonner</Button>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,370px)_minmax(0,1fr)]">
        {/* ---------------------------------------------------------------- panneau */}
        <div className="order-2 space-y-4 lg:order-1">
          <Card title={editingId ? 'Modifier le champ' : 'Nouveau champ'} icon={<LandPlot size={16} className="text-leaf-600" />}
            actions={(geometry || editingId || gpsRecording) && <Button size="sm" variant="ghost" onClick={() => setConfirmReset(true)}>Abandonner</Button>}>
            <div className="mb-3 flex gap-1 rounded-xl bg-ink-50 p-1" aria-label="Étapes">
              <Step n={1} label="Tracer" done={!!geometry} active={!geometry} />
              <Step n={2} label="Vérifier" done={verified} active={!!geometry && !verified} />
              <Step n={3} label="Enregistrer" done={false} active={verified} />
            </div>

            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-ink-500">Comment tracer le contour ?</div>
            <div className="grid grid-cols-2 gap-1.5" role="tablist">
              {METHODS.map((x, i) => (
                <button key={x.id} role="tab" aria-selected={method === x.id} onClick={() => { setMethod(x.id); setPickCb(null) }}
                  className={`relative flex min-h-[44px] items-center gap-1.5 rounded-lg border px-2.5 py-2 text-left text-sm transition ${method === x.id ? 'border-water-400 bg-water-50 font-semibold text-water-700' : 'border-ink-200 text-ink-700 hover:bg-ink-50'}`}>
                  <span className="text-xs text-ink-400">{i + 1}</span><x.icon size={15} />{x.label}
                  {x.id === 'gps_tracking' && gpsRecording && <span className="absolute right-2 top-2 h-2 w-2 animate-pulse rounded-full bg-red-500" title="Relevé GPS en cours" />}
                </button>
              ))}
            </div>
            <p className="my-3 text-sm text-ink-500">{current.help}</p>

            {/* Les 4 panneaux restent montés : changer d'onglet ne perd ni un tour GPS en cours, ni un plan analysé */}
            <div key={panelKey}>
              <div className={method === 'gps_tracking' ? '' : 'hidden'}>
                <GpsRecorder onGeometry={(g) => loadShape(g, 'gps_tracking')} onTrack={onTrack} onRecording={setGpsRecording} />
              </div>
              <div className={method === 'manual_dimensions' ? '' : 'hidden'}>
                <ManualDimensions onGeometry={(g) => loadShape(g, 'manual_dimensions')} getCenter={getCenter} requestPick={requestPick} setPin={setPin} />
              </div>
              <div className={method === 'cadastral_document' ? '' : 'hidden'}>
                <PlanUpload onExtraction={(x) => { setPlan(x); setPlanChecked(false) }} onGeometry={(g) => loadShape(g, 'cadastral_document')} getCenter={getCenter} />
              </div>
            </div>

            <div className="mt-4 space-y-3 border-t border-ink-100 pt-4">
              {m ? (
                <>
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div className="rounded-lg bg-leaf-50 p-2"><div className="text-lg font-bold tabular-nums text-leaf-700">{(m.areaM2 / 10000).toLocaleString('fr-FR', { maximumFractionDigits: 3 })}</div><div className="text-[11px] text-ink-500">hectares</div></div>
                    <div className="rounded-lg bg-ink-50 p-2"><div className="text-lg font-bold tabular-nums">{Math.round(m.areaM2).toLocaleString('fr-FR')}</div><div className="text-[11px] text-ink-500">m²</div></div>
                    <div className="rounded-lg bg-ink-50 p-2"><div className="text-lg font-bold tabular-nums">{Math.round(m.perimeterM).toLocaleString('fr-FR')}</div><div className="text-[11px] text-ink-500">m de tour</div></div>
                  </div>
                  <p className="text-xs text-ink-500">Contour {METHOD_LABEL[origin]} · {m.vertices} coins · 1 ha = 10 000 m² (un carré de 100 m × 100 m).</p>
                  {check!.errors.map((t) => (
                    <div key={t} className="flex flex-wrap items-start gap-2 rounded-lg bg-red-50 p-2 text-sm text-red-700">
                      <AlertTriangle size={16} className="mt-0.5 shrink-0" /><span className="min-w-0 flex-1">{t}</span>
                      {m.selfIntersecting && t.includes('croisent') && <Button size="sm" variant="danger" onClick={fixCrossing}><Wand2 size={13} /> Démêler</Button>}
                    </div>
                  ))}
                  {check!.warnings.map((t) => (
                    <p key={t} className="flex gap-2 rounded-lg bg-sun-50 p-2 text-sm text-sun-600"><AlertTriangle size={16} className="mt-0.5 shrink-0" />{t}</p>
                  ))}
                </>
              ) : (
                <Empty>Aucun contour pour l'instant. Choisissez une des 4 méthodes ci-dessus.</Empty>
              )}

              {origin === 'cadastral_document' && geometry && !editingId && (
                <label className="flex gap-2 rounded-lg border border-sun-400/50 bg-sun-50 p-2.5 text-sm text-ink-900">
                  <input type="checkbox" className="mt-0.5 h-4 w-4 shrink-0 accent-leaf-600" checked={planChecked} onChange={(e) => setPlanChecked(e.target.checked)} />
                  <span>J'ai vérifié que la forme, l'échelle et la position correspondent au plan et au terrain.</span>
                </label>
              )}

              <div>
                <label className="block text-sm text-ink-700" htmlFor="parcel-name">Nom du champ</label>
                <input id="parcel-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} placeholder={defaultName}
                  onKeyDown={(e) => { if (e.key === 'Enter') save() }}
                  className={`mt-1 h-11 w-full rounded-lg border px-3 focus:border-water-400 focus:outline-none ${dupe ? 'border-red-300' : 'border-ink-200'}`} />
                {dupe && <p className="mt-1 text-xs text-red-600">« {dupe.parcel_name} » existe déjà : choisissez un autre nom.</p>}
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {CROPS.map((c) => (
                    <button key={c} type="button" onClick={() => setName(uniqueName(c, names.filter((n) => n !== (editingId ? parcels?.find((p) => p.parcel_id === editingId)?.parcel_name : ''))))}
                      className="rounded-full border border-ink-200 px-2 py-0.5 text-xs text-ink-700 hover:border-water-400 hover:bg-water-50">{c}</button>
                  ))}
                </div>
              </div>
              <Button variant="success" className="h-11 w-full" onClick={save} disabled={!canSave}>
                <CheckCircle2 size={16} /> {saving ? 'Enregistrement…' : editingId ? 'Enregistrer les modifications' : 'Enregistrer ce champ'}
              </Button>
              {geometry && !canSave && !saving && (
                <p className="text-center text-xs text-ink-500">
                  {check?.errors.length ? 'Corrigez le contour (en rouge) pour pouvoir enregistrer.' : needsPlanCheck ? 'Cochez la case de vérification du plan.' : dupe ? 'Changez le nom du champ.' : ''}
                </p>
              )}
            </div>
          </Card>
        </div>

        {/* ---------------------------------------------------------------- carte */}
        <div className="order-1 lg:order-2">
          <div className="relative isolate h-[62vh] min-h-[420px] overflow-hidden rounded-2xl border border-ink-200/70 shadow-card lg:h-[calc(100vh-230px)]">
            <MapContainer center={farmCenter ?? DEFAULT_CENTER} zoom={16} maxZoom={19} scrollWheelZoom className="h-full w-full bg-ink-200">
              <MapHandle onMap={onMap} />
              {basemap === 'satellite' ? (
                <>
                  <TileLayer key="sat" url={ESRI_IMAGERY} maxNativeZoom={19} maxZoom={19} attribution="Imagerie &copy; Esri, Maxar, Earthstar Geographics" />
                  <TileLayer key="lbl" url={ESRI_LABELS} maxNativeZoom={19} maxZoom={19} opacity={0.85} />
                </>
              ) : (
                <TileLayer key="osm" url={OSM} maxNativeZoom={19} maxZoom={19} attribution="&copy; contributeurs OpenStreetMap" />
              )}

              {shown.map((p) => (
                <Polygon key={p.parcel_id} positions={toLatLngs(p.geometry)} eventHandlers={{ click: () => openParcel(p) }}
                  pathOptions={{ color: p.validation_status === 'validated' ? '#facc15' : '#fb923c', weight: 2, fillOpacity: 0.12, dashArray: p.validation_status === 'validated' ? undefined : '6 4' }}>
                  <Tooltip direction="center" permanent className="!border-0 !bg-white/85 !px-1.5 !py-0.5 !text-[11px] !font-semibold !shadow">
                    {p.parcel_name} · {areaText(p.area_m2)}
                  </Tooltip>
                </Polygon>
              ))}

              {track.length > 0 && (
                <>
                  <Polyline positions={track.map((p) => [p.latitude, p.longitude] as [number, number])} pathOptions={{ color: '#f59e0b', weight: 4 }} />
                  {track.length > 1 && <CircleMarker center={[track[0].latitude, track[0].longitude]} radius={8} pathOptions={{ color: '#16803f', fillColor: '#22c55e', fillOpacity: 1, weight: 2 }}><Tooltip>Départ</Tooltip></CircleMarker>}
                  <CircleMarker center={[track[track.length - 1].latitude, track[track.length - 1].longitude]} radius={7} pathOptions={{ color: '#f59e0b', fillColor: '#fff', fillOpacity: 1, weight: 3 }} />
                </>
              )}

              {pin && <Marker position={pin} icon={L.divIcon({ className: '', iconSize: [0, 0], html: '<div class="-translate-x-1/2 -translate-y-full text-2xl leading-none drop-shadow">📍</div>' })} />}

              {me && (
                <>
                  <Circle center={me.pos} radius={me.acc} pathOptions={{ color: '#1d74cc', fillColor: '#1d74cc', fillOpacity: 0.12, weight: 1 }} />
                  <CircleMarker center={me.pos} radius={7} pathOptions={{ color: '#fff', fillColor: '#1d74cc', fillOpacity: 1, weight: 2 }}>
                    <Tooltip>Vous êtes ici (± {Math.round(me.acc)} m)</Tooltip>
                  </CircleMarker>
                </>
              )}

              <PickClick active={!!pickCb} onPick={(p) => { pickCb?.(p); setPickCb(null) }} />
              <Editor loadKey={loadKey} geometryRef={geometryRef} mode={mode} onDraw={onDraw} onEdit={onEdit} />
            </MapContainer>

            {/* Recherche, fond de carte, position */}
            <div className="pointer-events-none absolute inset-x-2 top-2 z-[600] flex items-start justify-end gap-1.5 pl-12">
              <PlaceSearch onGo={goTo} />
              <div className="pointer-events-auto flex shrink-0 flex-col gap-1.5">
                <Button size="sm" onClick={locate} className="shadow" aria-label="Ma position"><LocateFixed size={14} /><span className="hidden sm:inline">Ma position</span></Button>
                <Button size="sm" onClick={() => setBasemap((b) => (b === 'satellite' ? 'plan' : 'satellite'))} className="shadow" aria-label="Changer de fond de carte">
                  <Layers size={14} /><span className="hidden sm:inline">{basemap === 'satellite' ? 'Plan' : 'Satellite'}</span>
                </Button>
              </div>
            </div>

            {pickCb && (
              <div className="absolute inset-x-2 bottom-2 z-[600] flex justify-center">
                <div className="flex items-center gap-2 rounded-lg bg-water-600 px-3 py-2 text-sm font-medium text-white shadow">
                  Touchez la carte pour choisir le point
                  <button onClick={() => setPickCb(null)} className="rounded bg-white/20 px-2 py-0.5 text-xs hover:bg-white/30">Annuler</button>
                </div>
              </div>
            )}
            {!pickCb && (geometry || historyLen > 0) && (
              <div className="absolute inset-x-2 bottom-2 z-[600] flex flex-wrap items-center justify-center gap-1.5">
                {geometry && ([['edit', 'Modifier les coins', Pencil], ['drag', 'Déplacer', Move], ['rotate', 'Tourner', RotateCw]] as const).map(([id, label, Icon]) => (
                  <Button key={id} size="sm" variant={mode === id ? 'primary' : 'secondary'} className="shadow" onClick={() => setMode(mode === id ? 'none' : id)}>
                    <Icon size={14} /> {mode === id ? 'Terminer' : label}
                  </Button>
                ))}
                {historyLen > 0 && <Button size="sm" className="shadow" onClick={undo} title="Annuler la dernière action (Ctrl+Z)"><Undo2 size={14} /> Annuler</Button>}
                {geometry && <Button size="sm" className="shadow" onClick={() => loadShape(null, origin)}><Eraser size={14} /> Effacer</Button>}
              </div>
            )}
            {!pickCb && !geometry && historyLen === 0 && method === 'map_drawing' && (
              <div className="pointer-events-none absolute bottom-2 left-1/2 z-[600] w-max max-w-[90%] -translate-x-1/2 rounded-lg bg-white/90 px-3 py-1.5 text-center text-xs text-ink-700 shadow">
                <Spline size={13} className="mr-1 inline" />Outil ⬠ (en haut à gauche), puis un toucher par coin du champ
              </div>
            )}
          </div>
          {mode !== 'none' && (
            <p className="mt-2 text-xs text-ink-500">
              {mode === 'edit' && 'Faites glisser un coin pour le déplacer ; touchez un petit point au milieu d\'un bord pour ajouter un coin ; clic droit (ou appui long) sur un coin pour le retirer.'}
              {mode === 'drag' && 'Faites glisser le champ entier jusqu\'à sa vraie place sur la photo.'}
              {mode === 'rotate' && 'Faites glisser une poignée d\'angle pour tourner le champ et l\'aligner sur la photo.'}
              {' '}Les longueurs des côtés s'affichent sur la carte. Touchez « Terminer » quand c'est bon.
            </p>
          )}
        </div>
      </div>

      {/* -------------------------------------------------------------------- liste */}
      <Card title={`Mes champs enregistrés${parcels?.length ? ` · ${parcels.length} · ${areaText(totalM2)} au total` : ''}`} icon={<LandPlot size={16} className="text-leaf-600" />}
        actions={parcels && parcels.length > 0 && <Button size="sm" onClick={exportGeoJson}><Download size={14} /> Télécharger (GeoJSON)</Button>}>
        {listError ? <Empty>Liste indisponible : {listError}. <button className="text-water-700 underline" onClick={refresh}>Réessayer</button></Empty>
          : !parcels ? <Empty>Chargement…</Empty>
            : !parcels.length ? <Empty>Aucun champ enregistré pour l'instant.</Empty> : (
              <ul className="divide-y divide-ink-100">
                {parcels.map((p) => (
                  <li key={p.parcel_id} className={`flex flex-wrap items-center gap-x-3 gap-y-2 py-2.5 ${editingId === p.parcel_id ? 'bg-water-50/60' : ''}`}>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-ink-900">{p.parcel_name}</span>
                        {p.validation_status === 'validated'
                          ? <Badge tone="ok"><CheckCircle2 size={12} /> vérifié</Badge>
                          : <Badge tone="warn">à vérifier</Badge>}
                        {editingId === p.parcel_id && <Badge tone="water">en cours de modification</Badge>}
                      </div>
                      <div className="text-xs text-ink-500">
                        {areaText(p.area_m2)} · {Math.round(p.perimeter_m).toLocaleString('fr-FR')} m de tour · {METHOD_LABEL[p.input_method]}
                        {' · '}{new Date(p.updated_at).toLocaleDateString('fr-FR')}
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <Button size="sm" variant="primary" onClick={() => openProfile(p)}><ClipboardList size={13} /> Profil et équipement</Button>
                      <Button size="sm" onClick={() => openParcel(p)} disabled={editingId === p.parcel_id}><Pencil size={13} /> Contour</Button>
                      {p.validation_status === 'validated'
                        ? <Button size="sm" variant="ghost" onClick={() => setStatus(p, 'pending')}>Revérifier</Button>
                        : <Button size="sm" variant="success" onClick={() => setStatus(p, 'validated')}><CheckCircle2 size={13} /> C'est correct</Button>}
                      <Button size="sm" variant="ghost" onClick={() => setToDelete(p)} aria-label={`Supprimer ${p.parcel_name}`}><Trash2 size={13} className="text-red-600" /></Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
        <p className="mt-3 flex items-center text-xs text-ink-500">« À vérifier » : vous n'avez pas encore confirmé que le contour colle au terrain. Un champ redessiné repasse « à vérifier ».
          <InfoTip text="Sur la carte, un champ à vérifier est en pointillés orange, un champ vérifié en trait jaune plein. Touchez un champ de la carte pour le modifier." /></p>
      </Card>

      <ConfirmDialog open={toDelete !== null} onClose={() => setToDelete(null)} title="Supprimer ce champ ?"
        message={<>« {toDelete?.parcel_name} » ({toDelete ? areaText(toDelete.area_m2) : ''}) sera supprimé définitivement.</>}
        confirmLabel="Supprimer" variant="danger" onConfirm={() => toDelete && remove(toDelete)} />
      <ConfirmDialog open={confirmReset} onClose={() => setConfirmReset(false)} title="Abandonner ce contour ?"
        message={<>{gpsRecording ? 'Le relevé GPS en cours sera arrêté et ' : 'Le '}tracé non enregistré sera perdu{editingId ? ' (le champ enregistré, lui, ne change pas)' : ''}.</>}
        confirmLabel="Abandonner" variant="danger" onConfirm={() => { writeDraft(null); reset() }} />
    </div>
  )
}
