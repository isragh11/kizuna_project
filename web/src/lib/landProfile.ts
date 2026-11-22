// Profil d'un champ : sol, soleil, puits, cultures par zone ; recommandations panneaux solaires et capteurs.
import area from '@turf/area'
import { difference, intersection, type Geom } from 'polyclip-ts'
import { apiGet, apiRequest, apiSend } from './api'
import type { Parcel, PolygonGeometry } from './parcels'

export type Irrigation = 'drip' | 'sprinkler' | 'surface'
export type Shading = 'aucun' | 'partiel' | 'fort'

export interface Zone {
  id: string
  name: string
  crop: string
  crop_label?: string | null
  irrigation_method: Irrigation
  geometry: PolygonGeometry
  area_m2?: number
}

export interface SoilInfo {
  texture: string; awc_mm_per_m: number; advice: string; source: string; assumption: boolean
  sand_pct: number | null; silt_pct: number | null; clay_pct: number | null
}
export interface Environment {
  centroid: [number, number]
  soil: SoilInfo
  sun: { kwh_per_kwc_year: number; kwh_per_kwc_month: number[]; optimal_tilt_deg: number; optimal_azimuth_deg: number; label: string; source: string; assumption: boolean }
  climate: { et0_mm_day: number[]; rain_mm_month: number[]; source: string; assumption: boolean }
  fetched_at: string
}
export interface ProfileView {
  parcel: Parcel
  profile: { soil_override: string | null; shading: Shading; well: { lon: number; lat: number } | null; well_depth_m: number | null; has_tank: boolean; zones: Zone[]; zones_review: boolean }
  zone_issues: string[]
  planted_m2: number
  unplanted_m2: number
  environment: Environment | null
  soil: SoilInfo | null
  soil_classes: { key: string; label: string; awc_mm_per_m: number }[]
  default_well_depth_m: number
}
export interface CropChoice { key: string; name: string; perennial: boolean }

export interface SensorItem {
  key: 'soil_moisture' | 'flow_meter' | 'pressure' | 'weather_station' | 'gateway' | 'tank_level'
  label: string; count: number; unit_tnd: number; total_tnd: number; why: string; where: string
  points: { lon: number; lat: number; label: string }[]; shared_with: string | null
}
export interface SolarReco {
  status: 'ok' | 'no_zones' | 'no_need'; message?: string
  panels: number; panel_wp: number; kwc: number; pump_kw: number; inverter_kw: number; head_m: number; kwh_per_m3: number
  design_month: string; design_water_m3_day: number; water_m3_year: number; energy_kwh_year: number; solar_share_pct: number
  tilt_deg: number; facing: string; area_m2: number; inside: boolean; where: string; shades_crops: boolean; distance_to_well_m: number | null
  layout: { rows: number; cols: number; width_m: number; depth_m: number; pitch_m: number; shadow_m: number; winter_sun_deg: number }
  footprint: PolygonGeometry
  cost_lines: { item: string; qty: number; unit_tnd: number; total_tnd: number }[]
  total_tnd: number; savings_tnd_year: number; payback_years: number | null
  monthly: { month: string; need_kwh: number; pv_kwh: number; water_m3: number }[]
  well_depth_m: number; well_assumed: boolean; shading_factor: number
}
export interface Recommendations {
  solar: SolarReco
  sensors: { items: SensorItem[]; devices: number; equipment_tnd: number; installation_tnd: number; total_tnd: number; zones_assumed: boolean }
  assumption: boolean; note: string; env_sources: Record<'soil' | 'sun' | 'climate', string>; env_assumption: boolean
}

export const IRRIGATION_LABEL: Record<Irrigation, string> = { drip: 'Goutte-à-goutte', sprinkler: 'Aspersion', surface: 'Gravitaire (raies)' }
export const SHADING_LABEL: Record<Shading, string> = { aucun: 'Aucun obstacle', partiel: 'Quelques arbres ou bâtiments', fort: 'Beaucoup d\'ombre' }
export const MONTHS = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D']

/** Couleur stable par culture (cartes et listes). */
const PALETTE: Record<string, string> = { tomate: '#ef4444', olivier: '#65a30d', ble: '#eab308', piment: '#f97316', riz: '#06b6d4', autre: '#8b5cf6' }
export const cropColor = (key: string) => PALETTE[key] ?? '#8b5cf6'

const base = (id: string) => `/api/parcels/${encodeURIComponent(id)}`
export const profileApi = {
  crops: () => apiGet<CropChoice[]>('/api/parcels/crops').then((r) => r.data),
  get: (id: string) => apiGet<ProfileView>(`${base(id)}/profile`, { cache: false }).then((r) => r.data),
  put: (id: string, body: Partial<{ soil_override: string | null; shading: Shading; well: { lon: number; lat: number } | null; well_depth_m: number | null; has_tank: boolean; zones: Zone[] }>) =>
    apiRequest<ProfileView>('PUT', `${base(id)}/profile`, body),
  refreshEnv: (id: string) => apiSend<ProfileView>(`${base(id)}/environment`, undefined, 120000),
  recommendations: (id: string) => apiGet<Recommendations>(`${base(id)}/recommendations`, { cache: false, timeoutMs: 120000 }).then((r) => r.data),
}

// ------------------------------------------------------------------------- découpage du champ

type Ring = [number, number][]
const ringOf = (g: PolygonGeometry): Ring => g.coordinates[0].map((p) => [p[0], p[1]] as [number, number])
const polyArea = (rings: Ring[]) => area({ type: 'Polygon', coordinates: rings } as any)

/** Plus grand morceau (anneau extérieur seul) d'un résultat polyclip ; null s'il est vide. */
function largest(multi: Ring[][]): PolygonGeometry | null {
  let best: Ring[] | null = null, bestA = 0
  for (const poly of multi) {
    const a = polyArea(poly)
    if (a > bestA) { bestA = a; best = poly }
  }
  return best && bestA > 1 ? { type: 'Polygon', coordinates: [best[0].map(([x, y]) => [+x.toFixed(8), +y.toFixed(8)])] } : null
}

/** Zone dessinée → ramenée dans le champ, sans recouvrir les zones existantes.
 *  Renvoie le plus grand morceau, et s'il en restait d'autres (zone coupée en deux). */
export function clipZone(drawn: PolygonGeometry, parcel: PolygonGeometry, others: Zone[]): { geometry: PolygonGeometry | null; pieces: number; holes: boolean } {
  let result = intersection([ringOf(drawn)] as Geom, [ringOf(parcel)] as Geom)
  if (others.length) result = difference(result as Geom, ...others.map((z) => [ringOf(z.geometry)] as Geom))
  const kept = result.filter((p) => polyArea(p) > 1)
  return { geometry: largest(kept), pieces: kept.length, holes: kept.some((p) => p.length > 1) }
}

/** Partage le champ en n bandes de MÊME SURFACE. dir « cote » : bandes côte à côte (coupes nord-sud) ;
 *  « etage » : bandes l'une au-dessus de l'autre (coupes est-ouest). */
export function splitStrips(parcel: PolygonGeometry, n: number, dir: 'cote' | 'etage'): PolygonGeometry[] {
  const ring = ringOf(parcel)
  const axis = dir === 'cote' ? 0 : 1
  const vals = ring.map((p) => p[axis])
  const lo = Math.min(...vals) - 1e-6, hi = Math.max(...vals) + 1e-6
  const others = ring.map((p) => p[1 - axis])
  const olo = Math.min(...others) - 0.01, ohi = Math.max(...others) + 0.01
  const slab = (a: number, b: number): Geom => {
    const r: Ring = axis === 0 ? [[a, olo], [b, olo], [b, ohi], [a, ohi], [a, olo]] : [[olo, a], [olo, b], [ohi, b], [ohi, a], [olo, a]]
    return [r]
  }
  const areaBelow = (c: number) => intersection([ring] as Geom, slab(lo, c)).reduce((s, p) => s + polyArea(p), 0)
  const total = areaBelow(hi)
  const cuts = [lo]
  for (let i = 1; i < n; i++) {
    let a = cuts[i - 1], b = hi
    for (let k = 0; k < 40; k++) {                       // dichotomie : même surface pour chaque bande
      const mid = (a + b) / 2
      if (areaBelow(mid) < (total * i) / n) a = mid; else b = mid
    }
    cuts.push((a + b) / 2)
  }
  cuts.push(hi)
  const out: PolygonGeometry[] = []
  for (let i = 0; i < n; i++) {
    const g = largest(intersection([ring] as Geom, slab(cuts[i], cuts[i + 1])))
    if (g) out.push(g)
  }
  return out
}

export const zoneArea = (g: PolygonGeometry) => area(g as any)
export const newZoneId = () => `z${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`

export const tnd = (v: number) => `${Math.round(v).toLocaleString('fr-FR')} TND`
