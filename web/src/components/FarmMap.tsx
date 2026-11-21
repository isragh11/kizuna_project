// Carte Leaflet (tuiles OpenStreetMap) : parcelles colorées selon la satisfaction du plan,
// tronçons S1–S5 en rouge quand une alerte est active, cuve.
import { CircleMarker, MapContainer, Polygon, Polyline, TileLayer, Tooltip } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { SECTION_OF, type PlotId } from '../lib/types'

export interface MapPlot { id: PlotId; lat: number; lon: number; area_ha: number; crop: string; satisfaction?: number; valveOpen?: boolean; fallow?: boolean }

function square(lat: number, lon: number, areaHa: number): [number, number][] {
  const half = Math.sqrt(areaHa * 10000) / 2
  const dLat = half / 111320
  const dLon = half / (111320 * Math.cos((lat * Math.PI) / 180))
  return [[lat - dLat, lon - dLon], [lat - dLat, lon + dLon], [lat + dLat, lon + dLon], [lat + dLat, lon - dLon]]
}

function color(sat?: number) {
  if (sat === undefined) return '#94a0b2'
  if (sat >= 0.95) return '#22a755'
  if (sat >= 0.6) return '#84cc16'
  if (sat >= 0.35) return '#f59e0b'
  return '#dc2626'
}

export default function FarmMap({ plots, alertSections, height = 380 }: { plots: MapPlot[]; alertSections: Set<string>; height?: number }) {
  if (!plots.length) return null
  const lat = plots.reduce((s, p) => s + p.lat, 0) / plots.length
  const lon = plots.reduce((s, p) => s + p.lon, 0) / plots.length
  const tank: [number, number] = [lat, lon - 0.0026]
  const junction: [number, number] = [lat, lon - 0.0012]
  const line = (s: string) => ({ color: alertSections.has(s) ? '#dc2626' : '#1d74cc', weight: alertSections.has(s) ? 6 : 4, opacity: 0.9, dashArray: alertSections.has(s) ? '8 6' : undefined })
  return (
    <div className="overflow-hidden rounded-xl border border-ink-200" style={{ height }}>
      <MapContainer center={[lat, lon]} zoom={16} scrollWheelZoom={false} style={{ height: '100%', width: '100%' }}>
        <TileLayer attribution='&copy; OpenStreetMap' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        {plots.map((p) => (
          <Polygon key={p.id} positions={square(p.lat, p.lon, p.area_ha)}
            pathOptions={{ color: color(p.fallow ? undefined : p.satisfaction), fillColor: color(p.fallow ? undefined : p.satisfaction), fillOpacity: 0.35, weight: 2 }}>
            <Tooltip permanent direction="top" offset={[0, -14]} className="!bg-white/95 !text-xs !font-semibold">
              {p.id} · {p.crop}{p.fallow ? ' · jachère' : p.satisfaction !== undefined ? ` · ${Math.round(p.satisfaction * 100)} %` : ''}
            </Tooltip>
          </Polygon>
        ))}
        <Polyline positions={[tank, junction]} pathOptions={line('S1')}><Tooltip>S1 · conduite principale</Tooltip></Polyline>
        {plots.map((p) => (
          <Polyline key={'l' + p.id} positions={[junction, [p.lat, p.lon]]} pathOptions={line(SECTION_OF[p.id])}>
            <Tooltip>{SECTION_OF[p.id]} · parcelle {p.id}{p.valveOpen !== undefined ? ` · vanne ${p.valveOpen ? 'ouverte' : 'fermée'}` : ''}</Tooltip>
          </Polyline>
        ))}
        <Polyline positions={[[junction[0] - 0.00015, junction[1]], [tank[0] - 0.00015, tank[1]]]} pathOptions={{ ...line('S5'), weight: 2 }}>
          <Tooltip>S5 · retour vers la cuve</Tooltip>
        </Polyline>
        <CircleMarker center={tank} radius={10} pathOptions={{ color: '#1b5ea6', fillColor: '#5fb4f8', fillOpacity: 0.9 }}>
          <Tooltip>Cuve + pompe (puits, solaire)</Tooltip>
        </CircleMarker>
      </MapContainer>
    </div>
  )
}
