// Mes terres agricoles : types, appels à /api/parcels et calculs géométriques côté navigateur.
// Les règles (croisements, surface minimale/maximale, étendue) sont les MÊMES que le serveur
// (aquamind_core/parcel_geo.py) : ce que le navigateur accepte, le serveur l'accepte aussi.
import area from '@turf/area'
import { apiRequest, apiSend, apiGet, errorText } from './api'

export interface PolygonGeometry { type: 'Polygon'; coordinates: number[][][] }

export type InputMethod = 'map_drawing' | 'gps_tracking' | 'manual_dimensions' | 'cadastral_document'

export interface Parcel {
  parcel_id: string
  farm_id: string | null
  parcel_name: string
  input_method: InputMethod
  geometry: PolygonGeometry
  area_m2: number
  area_hectares: number
  perimeter_m: number
  validation_status: 'pending' | 'validated'
  source_document_id: string | null
  created_at: string
  updated_at: string
}

export interface PlanPoint { id: string; x: number; y: number }
export interface PlanExtraction {
  document_id: string
  extraction_status: 'pending_human_verification'
  boundary_points: PlanPoint[]
  candidates?: { boundary_points: PlanPoint[]; extraction_confidence: number }[]
  boundary_coordinates_are_pixel_coordinates: true
  image_width: number
  image_height: number
  extraction_confidence: number
  ocr_confidence: number
  ocr_status: string
  ocr_text: string[]
  detected: boolean
  warning: string
}

export const METHOD_LABEL: Record<InputMethod, string> = {
  map_drawing: 'dessiné sur la carte',
  gps_tracking: 'relevé en marchant (GPS)',
  manual_dimensions: 'construit avec des dimensions',
  cadastral_document: "d'après un plan cadastral",
}

// Mêmes garde-fous que le serveur
export const MIN_AREA_M2 = 10
export const MAX_AREA_M2 = 100_000_000      // 10 000 ha
export const MAX_SPAN_M = 20_000

/** Le serveur répond, mais ne connaît pas encore les routes /api/parcels : API lancée avant la mise à jour. */
export const OUTDATED_SERVER_TEXT =
  "Le serveur AquaMind n'est pas à jour : la fonction « Mes terres » n'y existe pas encore. Redémarrez l'API (Ctrl+C dans sa fenêtre, puis relancez-la), puis rechargez cette page."

/** 404 de FastAPI pour une route inconnue (« Not Found »), à distinguer de nos 404 en français
 *  (« Parcelle introuvable », « Document introuvable »). */
export function isUnknownRoute(r: { status: number; data: any } | null | undefined): boolean {
  return !!r && r.status === 404 && r.data?.detail === 'Not Found'
}

/** Erreur lisible à partir d'une réponse de l'API. */
export function failText(r: { status: number; data: any } | null, fallback: string): string {
  if (!r) return 'Serveur injoignable'
  if (isUnknownRoute(r)) return OUTDATED_SERVER_TEXT
  const t = errorText(r.data, `${fallback} (erreur ${r.status})`)
  return t.replace(/^Value error, /, '')
}

export const parcelsApi = {
  list: () => apiGet<Parcel[]>('/api/parcels', { cache: false }).then((r) => r.data),
  create: (body: { parcel_name: string; input_method: InputMethod; geometry: PolygonGeometry; source_document_id?: string | null; farm_id?: string | null }) =>
    apiSend<Parcel>('/api/parcels', body),
  update: (id: string, body: { parcel_name?: string; geometry?: PolygonGeometry; validation_status?: 'pending' | 'validated' }) =>
    apiRequest<Parcel>('PUT', `/api/parcels/${encodeURIComponent(id)}`, body),
  remove: (id: string) => apiRequest('DELETE', `/api/parcels/${encodeURIComponent(id)}`),
}

// ------------------------------------------------------------------------------- géométrie de base

type XY = [number, number]
/** Rayon terrestre moyen : le même que @turf/area et que le serveur (parcel_geo.py). */
export const EARTH_RADIUS_M = 6371008.8
export const M_PER_DEG = (EARTH_RADIUS_M * Math.PI) / 180     // 111 195 m par degré de latitude

/** Distance en mètres entre deux points [lon, lat] (haversine). */
export function distanceM(a: number[], b: number[]): number {
  const rad = (v: number) => (v * Math.PI) / 180
  const dLat = rad(b[1] - a[1])
  const dLon = rad(b[0] - a[0])
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a[1])) * Math.cos(rad(b[1])) * Math.sin(dLon / 2) ** 2
  return EARTH_RADIUS_M * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1 - h))
}

/** Projection locale en mètres (x vers l'est, y vers le nord) autour de lat0, et retour. */
function projector(lat0: number) {
  const kx = M_PER_DEG * Math.cos((lat0 * Math.PI) / 180)
  return {
    to: (p: number[]): XY => [p[0] * kx, p[1] * M_PER_DEG],
    from: (q: XY): number[] => [+(q[0] / kx).toFixed(8), +(q[1] / M_PER_DEG).toFixed(8)],
  }
}
const meanLat = (pts: number[][]) => pts.reduce((s, p) => s + p[1], 0) / Math.max(1, pts.length)

/** Ferme un anneau [lon, lat] s'il ne l'est pas. */
export function closeRing(points: number[][]): number[][] {
  if (!points.length) return points
  const [f, l] = [points[0], points[points.length - 1]]
  return f[0] === l[0] && f[1] === l[1] ? points : [...points, [...f]]
}

/** Anneau OUVERT sans sommets répétés consécutifs (double clic, GPS immobile : < 5 cm). */
export function cleanRing(points: number[][]): number[][] {
  const out: number[][] = []
  for (const p of points) if (!out.length || distanceM(out[out.length - 1], p) > 0.05) out.push(p)
  if (out.length > 1 && distanceM(out[0], out[out.length - 1]) <= 0.05) out.pop()
  return out
}

const orient = (a: XY, b: XY, c: XY) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
const onSeg = (a: XY, b: XY, p: XY) =>
  Math.min(a[0], b[0]) - 1e-9 <= p[0] && p[0] <= Math.max(a[0], b[0]) + 1e-9 &&
  Math.min(a[1], b[1]) - 1e-9 <= p[1] && p[1] <= Math.max(a[1], b[1]) + 1e-9

/** Même règle que le serveur : croisement, contact ou recouvrement. */
function segmentsTouch(p1: XY, p2: XY, p3: XY, p4: XY): boolean {
  const e = 1e-9
  const d1 = orient(p3, p4, p1), d2 = orient(p3, p4, p2), d3 = orient(p1, p2, p3), d4 = orient(p1, p2, p4)
  if (((d1 > e && d2 < -e) || (d1 < -e && d2 > e)) && ((d3 > e && d4 < -e) || (d3 < -e && d4 > e))) return true
  return (Math.abs(d1) <= e && onSeg(p3, p4, p1)) || (Math.abs(d2) <= e && onSeg(p3, p4, p2)) ||
    (Math.abs(d3) <= e && onSeg(p1, p2, p3)) || (Math.abs(d4) <= e && onSeg(p1, p2, p4))
}

/** Indices (i, j) de deux bords non voisins qui se touchent, ou null. `xy` est un anneau FERMÉ. */
function findCrossing(xy: XY[]): [number, number] | null {
  const n = xy.length - 1
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue
      if (segmentsTouch(xy[i], xy[i + 1], xy[j], xy[j + 1])) return [i, j]
    }
  }
  return null
}

function intersection(p1: XY, p2: XY, p3: XY, p4: XY): XY | null {
  const d = (p1[0] - p2[0]) * (p3[1] - p4[1]) - (p1[1] - p2[1]) * (p3[0] - p4[0])
  if (Math.abs(d) < 1e-12) return null
  const t = ((p1[0] - p3[0]) * (p3[1] - p4[1]) - (p1[1] - p3[1]) * (p3[0] - p4[0])) / d
  return [p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1])]
}

// ------------------------------------------------------------------------------- mesures et contrôle

/** Surface, périmètre, nombre de coins et croisements — calcul instantané pendant le tracé. */
export function measure(g: PolygonGeometry) {
  const open = cleanRing(g.coordinates[0] ?? [])
  const ring = closeRing(open)
  let perimeterM = 0
  for (let i = 0; i + 1 < ring.length; i++) perimeterM += distanceM(ring[i], ring[i + 1])
  const P = projector(meanLat(open))
  const xy = ring.map(P.to)
  const xs = xy.map((p) => p[0]), ys = xy.map((p) => p[1])
  const spanM = xy.length ? Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) : 0
  const selfIntersecting = open.length >= 3 && findCrossing(xy) !== null
  let areaM2 = 0
  try { areaM2 = open.length >= 3 ? area({ type: 'Polygon', coordinates: [ring] } as any) : 0 } catch { areaM2 = 0 }
  return { areaM2, perimeterM, selfIntersecting, vertices: open.length, spanM }
}

export interface Assessment {
  m: ReturnType<typeof measure>
  errors: string[]        // bloquent l'enregistrement (le serveur refuserait aussi)
  warnings: string[]      // à vérifier, n'empêchent pas d'enregistrer
}

/** Contrôle complet d'un contour avant enregistrement, en mots simples. */
export function assess(g: PolygonGeometry, others: Parcel[] = []): Assessment {
  const m = measure(g)
  const errors: string[] = []
  const warnings: string[] = []
  if (m.vertices < 3) errors.push('Il faut au moins 3 coins pour former un champ.')
  else if (m.selfIntersecting) errors.push('Les bords du champ se croisent : utilisez « Démêler » ou « Modifier les coins ».')
  if (m.spanM > MAX_SPAN_M) errors.push(`Contour trop étendu (${Math.round(m.spanM / 1000)} km) : il a sans doute été tracé en dézoomant.`)
  else if (m.vertices >= 3 && !m.selfIntersecting && m.areaM2 < MIN_AREA_M2) errors.push(`Champ trop petit (${m.areaM2.toFixed(1)} m²) : les coins sont presque alignés ou confondus.`)
  else if (m.areaM2 > MAX_AREA_M2) errors.push(`Contour démesuré (${Math.round(m.areaM2 / 10000)} ha) : vérifiez le zoom.`)
  if (!errors.length) {
    if (m.areaM2 > 1_000 * 10_000) warnings.push(`Très grande surface (${areaText(m.areaM2)}) : est-ce bien un seul champ ?`)
    const compact = (4 * Math.PI * m.areaM2) / (m.perimeterM ** 2 || 1)
    if (compact < 0.04) warnings.push('Forme très allongée : vérifiez qu\'aucun coin n\'est mal placé.')
    for (const o of others) if (overlaps(g, o.geometry)) warnings.push(`Ce contour empiète sur « ${o.parcel_name} ».`)
  }
  return { m, errors, warnings }
}

/** Lisible pour un agriculteur : « 1,25 ha » ou « 850 m² ». */
export function areaText(m2: number): string {
  return m2 >= 5000
    ? `${(m2 / 10000).toLocaleString('fr-FR', { maximumFractionDigits: 2 })} ha`
    : `${Math.round(m2).toLocaleString('fr-FR')} m²`
}

/** Longueur de chaque côté et son milieu [lat, lon] (étiquettes sur la carte). */
export function edgeLabels(g: PolygonGeometry): { at: [number, number]; m: number }[] {
  const ring = closeRing(cleanRing(g.coordinates[0] ?? []))
  const out: { at: [number, number]; m: number }[] = []
  for (let i = 0; i + 1 < ring.length; i++) {
    const [a, b] = [ring[i], ring[i + 1]]
    out.push({ at: [(a[1] + b[1]) / 2, (a[0] + b[0]) / 2], m: distanceM(a, b) })
  }
  return out
}

// ----------------------------------------------------------------------------- chevauchement

function pointInPolygon(p: XY, poly: XY[]): boolean {
  let inside = false
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j]
    if ((yi > p[1]) !== (yj > p[1]) && p[0] < ((xj - xi) * (p[1] - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}
function distToSegment(p: XY, a: XY, b: XY): number {
  const dx = b[0] - a[0], dy = b[1] - a[1]
  const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)))
  return Math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))
}
/** Vrai si p est franchement à l'intérieur (à plus de `tol` m du bord) : deux champs voisins qui partagent un bord ne « chevauchent » pas. */
function deepInside(p: XY, poly: XY[], tol = 0.5): boolean {
  if (!pointInPolygon(p, poly)) return false
  for (let i = 0; i + 1 < poly.length; i++) if (distToSegment(p, poly[i], poly[i + 1]) <= tol) return false
  return true
}

/** Les deux champs se recouvrent-ils (au-delà d'un bord commun) ? */
export function overlaps(a: PolygonGeometry, b: PolygonGeometry): boolean {
  const ra = closeRing(cleanRing(a.coordinates[0] ?? [])), rb = closeRing(cleanRing(b.coordinates[0] ?? []))
  if (ra.length < 4 || rb.length < 4) return false
  const P = projector(meanLat([...ra, ...rb]))
  const A = ra.map(P.to), B = rb.map(P.to)
  // boîtes englobantes disjointes : rien à faire
  const box = (Q: XY[]) => [Math.min(...Q.map((q) => q[0])), Math.min(...Q.map((q) => q[1])), Math.max(...Q.map((q) => q[0])), Math.max(...Q.map((q) => q[1]))]
  const [a0, a1, a2, a3] = box(A), [b0, b1, b2, b3] = box(B)
  if (a2 < b0 || b2 < a0 || a3 < b1 || b3 < a1) return false
  // croisement franc de deux bords (pas un simple contact)
  const e = 1e-6
  for (let i = 0; i + 1 < A.length; i++) for (let j = 0; j + 1 < B.length; j++) {
    const d1 = orient(B[j], B[j + 1], A[i]), d2 = orient(B[j], B[j + 1], A[i + 1])
    const d3 = orient(A[i], A[i + 1], B[j]), d4 = orient(A[i], A[i + 1], B[j + 1])
    if (((d1 > e && d2 < -e) || (d1 < -e && d2 > e)) && ((d3 > e && d4 < -e) || (d3 < -e && d4 > e))) {
      const x = intersection(A[i], A[i + 1], B[j], B[j + 1])
      // un croisement au ras d'un sommet (bords communs accrochés) ne compte pas
      if (x && [A[i], A[i + 1], B[j], B[j + 1]].every((v) => Math.hypot(v[0] - x[0], v[1] - x[1]) > 0.5)) return true
    }
  }
  const centroid = (Q: XY[]): XY => [Q.slice(0, -1).reduce((s, q) => s + q[0], 0) / (Q.length - 1), Q.slice(0, -1).reduce((s, q) => s + q[1], 0) / (Q.length - 1)]
  return A.some((p) => deepInside(p, B)) || B.some((p) => deepInside(p, A)) ||
    (deepInside(centroid(A), A) && deepInside(centroid(A), B)) || (deepInside(centroid(B), B) && deepInside(centroid(B), A))
}

// ------------------------------------------------------------------------- nettoyage du relevé GPS

/** Douglas-Peucker : retire les zigzags plus petits que `tolM` mètres (bruit du GPS). Chemin OUVERT. */
export function simplify(points: number[][], tolM: number): number[][] {
  if (points.length < 4) return points
  const P = projector(meanLat(points))
  const xy = points.map(P.to)
  const keep = new Array(points.length).fill(false)
  keep[0] = keep[points.length - 1] = true
  const stack: [number, number][] = [[0, points.length - 1]]
  while (stack.length) {
    const [s, t] = stack.pop()!
    let best = -1, idx = -1
    for (let i = s + 1; i < t; i++) {
      const d = distToSegment(xy[i], xy[s], xy[t])
      if (d > best) { best = d; idx = i }
    }
    if (best > tolM && idx > 0) { keep[idx] = true; stack.push([s, idx], [idx, t]) }
  }
  return points.filter((_, i) => keep[i])
}

/** En revenant au départ on le dépasse souvent de quelques mètres : on coupe la fin au point le plus
 *  proche du départ (dans le dernier quart du tour). Renvoie aussi l'écart final au départ. */
export function trimClosure(points: number[][]): { points: number[][]; gapM: number } {
  const lastGap = points.length ? distanceM(points[0], points[points.length - 1]) : 0
  if (points.length < 5) return { points, gapM: lastGap }
  const from = Math.max(3, Math.floor(points.length * 0.75))
  let best = points.length - 1, bestD = lastGap
  for (let i = from; i < points.length; i++) {
    const d = distanceM(points[0], points[i])
    if (d < bestD) { bestD = d; best = i }
  }
  // On ne coupe que si on est vraiment repassé près du départ (dépassement de quelques mètres).
  // Sinon (départ pris loin du champ, tour inachevé) couper retirerait de vrais côtés.
  if (bestD > CLOSE_ENOUGH_M) return { points, gapM: lastGap }
  return { points: points.slice(0, best + 1), gapM: bestD }
}
const CLOSE_ENOUGH_M = 15

/** On a souvent lancé l'enregistrement en marchant VERS le champ : le tour ne revient pas au tout premier
 *  point, mais à un point du début. Si la fin du tour passe à moins de 15 m d'un point du premier quart,
 *  tout ce qui précède ce point était le chemin d'accès et est retiré. */
export function trimApproach(points: number[][]): { points: number[][]; gapM: number } {
  const last = points[points.length - 1]
  const gap0 = points.length ? distanceM(points[0], last) : 0
  if (points.length < 5 || gap0 <= CLOSE_ENOUGH_M) return { points, gapM: gap0 }
  const upto = Math.max(1, Math.floor(points.length * 0.25))
  let best = 0, bestD = gap0
  for (let i = 1; i <= upto; i++) {
    const d = distanceM(points[i], last)
    if (d < bestD) { bestD = d; best = i }
  }
  return best > 0 && bestD <= CLOSE_ENOUGH_M ? { points: points.slice(best), gapM: bestD } : { points, gapM: gap0 }
}

/** Surface algébrique (m²) d'un anneau ouvert en mètres. */
function xyArea(ring: XY[]): number {
  let s = 0
  for (let i = 0; i < ring.length; i++) {
    const a = ring[i], b = ring[(i + 1) % ring.length]
    s += a[0] * b[1] - b[0] * a[1]
  }
  return Math.abs(s) / 2
}

/** Retire les petites boucles (le contour repasse sur lui-même) en coupant au croisement.
 *  Renvoie un anneau FERMÉ sans croisement, ou null si impossible. */
export function untangle(ring: number[][]): number[][] | null {
  let open = cleanRing(ring)
  if (open.length < 3) return null
  const P = projector(meanLat(open))
  let xy: XY[] = open.map(P.to)
  for (let guard = 0; guard < 200; guard++) {
    const closed = [...xy, xy[0]]
    const hit = findCrossing(closed)
    if (!hit) return closeRing(xy.map(P.from))
    const [i, j] = hit
    const x = intersection(closed[i], closed[i + 1], closed[j], closed[j + 1])
    // Le croisement coupe le contour en deux morceaux : la boucle (sommets i+1..j) et le reste.
    // On garde le morceau de plus GRANDE SURFACE (le champ), pas celui qui a le plus de sommets :
    // après simplification, une petite boucle autour d'un arbre peut avoir plus de coins que le champ.
    const outside = [...xy.slice(0, i + 1), ...(x ? [x] : []), ...xy.slice(j + 1)]
    const loop = [...(x ? [x] : []), ...xy.slice(i + 1, j + 1)]
    xy = xyArea(outside) >= xyArea(loop) ? outside : loop
    // retire les doublons créés par le point d'intersection
    xy = xy.filter((p, k) => k === 0 || Math.hypot(p[0] - xy[k - 1][0], p[1] - xy[k - 1][1]) > 0.05)
    if (xy.length < 3) return null
  }
  return null
}

/** Chaîne complète pour un tour GPS : doublons, dépassement du départ, chemin d'accès, bruit, petites boucles. */
export function cleanGpsTrack(points: number[][], noiseM: number): { ring: number[][] | null; gapM: number; untangled: boolean } {
  const { points: trimmed, gapM } = trimApproach(trimClosure(cleanRing(points)).points)
  const simple = simplify(trimmed, noiseM)
  if (simple.length < 3) return { ring: null, gapM, untangled: false }
  const ring = closeRing(simple)
  const P = projector(meanLat(simple))
  if (!findCrossing(ring.map(P.to))) return { ring, gapM, untangled: false }
  return { ring: untangle(ring), gapM, untangled: true }
}

// ------------------------------------------------------------------------------- saisie utilisateur

/** « 12,5 », « 12.5 », « 1 200 » → nombre ; NaN si vide ou illisible. */
export function parseNumber(s: string): number {
  const t = s.trim().replace(/[\s  ]/g, '').replace(',', '.')
  return t === '' ? NaN : Number(t)
}

/** Lit une position dans les formats courants ; renvoie [lat, lon] ou null.
 *  « 34.98, 9.49 » · « 34,98 9,49 » · « 34°58'48"N 9°29'24"E » · « 34.98N 9.49E » · lien Google Maps (…@34.98,9.49…) */
export function parseCoords(text: string): [number, number] | null {
  const s = text.trim()
  if (!s) return null
  const url = s.match(/@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/) ?? s.match(/[?&](?:q|query|ll)=(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)/)
  if (url) return checkLatLon(Number(url[1]), Number(url[2]))

  const u = s.toUpperCase().replace(/[’′‘]/g, "'").replace(/[″”“]/g, '"').replace(/''/g, '"')
  const num = '(\\d+(?:[.,]\\d+)?)'
  // degrés (minutes (secondes)) + hémisphère
  const dms = [...u.matchAll(new RegExp(`(-)?${num}\\s*°\\s*(?:${num}\\s*'\\s*)?(?:${num}\\s*"\\s*)?([NSEWO])?`, 'g'))]
  if (dms.length === 2) {
    const vals = dms.map((m) => {
      const v = Number(m[2].replace(',', '.')) + (m[3] ? Number(m[3].replace(',', '.')) / 60 : 0) + (m[4] ? Number(m[4].replace(',', '.')) / 3600 : 0)
      const neg = m[1] === '-' || m[5] === 'S' || m[5] === 'W' || m[5] === 'O'
      return { v: neg ? -v : v, h: m[5] }
    })
    return orderLatLon(vals)
  }
  // décimal (virgule décimale permise s'il n'y a pas de point dans le texte)
  const re = u.includes('.') ? /(-?\d+(?:\.\d+)?)\s*([NSEWO])?/g : /(-?\d+(?:,\d+)?)\s*([NSEWO])?/g
  const dec = [...u.matchAll(re)]
  if (dec.length === 2) {
    return orderLatLon(dec.map((m) => {
      const v = Number(m[1].replace(',', '.'))
      return { v: m[2] === 'S' || m[2] === 'W' || m[2] === 'O' ? -Math.abs(v) : v, h: m[2] }
    }))
  }
  return null
}
function orderLatLon(vals: { v: number; h?: string }[]): [number, number] | null {
  let [a, b] = vals
  if (a.h && 'EWO'.includes(a.h) && b.h && 'NS'.includes(b.h)) [a, b] = [b, a]     // « 9°E 34°N »
  else if (!a.h && !b.h && Math.abs(a.v) > 90 && Math.abs(b.v) <= 90) [a, b] = [b, a]  // longitude en premier
  return checkLatLon(a.v, b.v)
}
function checkLatLon(lat: number, lon: number): [number, number] | null {
  return Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? [lat, lon] : null
}

export const fmtLatLon = (p: [number, number]) => `${p[0].toFixed(6)}, ${p[1].toFixed(6)}`

/** « Champ 3 », ou « Oliviers 2 » si « Oliviers » existe déjà. */
export function uniqueName(base: string, taken: string[]): string {
  const used = new Set(taken.map((t) => t.trim().toLocaleLowerCase('fr')))
  if (base !== 'Champ' && !used.has(base.toLocaleLowerCase('fr'))) return base
  for (let n = base === 'Champ' ? 1 : 2; ; n++) if (!used.has(`${base} ${n}`.toLocaleLowerCase('fr'))) return `${base} ${n}`
}

/** Nom identique (sans casse) à un autre champ ? */
export function nameTaken(name: string, parcels: Parcel[], exceptId: string | null): Parcel | undefined {
  const n = name.trim().replace(/\s+/g, ' ').toLocaleLowerCase('fr')
  return parcels.find((p) => p.parcel_id !== exceptId && p.parcel_name.toLocaleLowerCase('fr') === n)
}
