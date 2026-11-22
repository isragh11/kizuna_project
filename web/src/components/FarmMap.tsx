// Carte Leaflet (OpenStreetMap) : parcelles colorées selon l'eau reçue, tuyaux nommés, fuite en rouge.
import { CircleMarker, MapContainer, Polygon, Polyline, TileLayer, Tooltip } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import { pipeName, type Crops } from '../lib/labels'
import { SECTION_OF, type PlotId } from '../lib/types'

export interface MapPlot { id: PlotId; lat: number; lon: number; area_ha: number; crop: string; satisfaction?: number; valveOpen?: boolean; fallow?: boolean }

function square(lat: number, lon: number, areaHa: number): [number, number][] {
  const half = Math.sqrt(areaHa * 10000) / 2
  const dLat = half / 111320
  const dLon = half / (111320 * Math.cos((lat * Math.PI) / 180))
  return [[lat - dLat, lon - dLon], [lat - dLat, lon + dLon], [lat + dLat, lon + dLon], [lat + dLat, lon - dLon]]
}

function color(sat?: number, fallow?: boolean) {
  if (fallow || sat === undefined) return '#94a0b2'
  if (sat >= 0.95) return '#22a755'
  if (sat >= 0.6) return '#84cc16'
  if (sat >= 0.35) return '#f59e0b'
  return '#dc2626'
}

export default function FarmMap({ plots, alertSections, crops, height = 400 }: { plots: MapPlot[]; alertSections: Set<string>; crops: Crops; height?: number }) {
  if (!plots.length) return null
  const lat = plots.reduce((s, p) => s + p.lat, 0) / plots.length
  const lon = plots.reduce((s, p) => s + p.lon, 0) / plots.length
  const tank: [number, number] = [lat, lon - 0.0026]
  const junction: [number, number] = [lat, lon - 0.0012]
  const leak = (s: string) => alertSections.has(s)
  const line = (s: string, w = 5) => ({ color: leak(s) ? '#dc2626' : '#1d74cc', weight: leak(s) ? w + 4 : w, opacity: 0.95, dashArray: leak(s) ? '10 7' : undefined })
  return (
    <div className="overflow-hidden rounded-xl border border-ink-200" style={{ height }}>
      <MapContainer center={[lat, lon]} zoom={16} scrollWheelZoom={false} style={{ height: '100%', width: '100%' }}>
        <TileLayer attribution='&copy; OpenStreetMap' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        {plots.map((p) => (
          <Polygon key={p.id} positions={square(p.lat, p.lon, p.area_ha)}
            pathOptions={{ color: color(p.satisfaction, p.fallow), fillColor: color(p.satisfaction, p.fallow), fillOpacity: 0.4, weight: 2 }}>
            <Tooltip permanent direction="top" offset={[0, -14]} className="!bg-white/95 !text-xs !font-semibold">
              {p.id} · {p.crop}{p.fallow ? ' · au repos' : p.satisfaction !== undefined ? ` · ${Math.round(p.satisfaction * 100)} %` : ''}
            </Tooltip>
          </Polygon>
        ))}
        <Polyline key={`S1-${leak('S1')}`} positions={[tank, junction]} pathOptions={line('S1', 6)}>
          <Tooltip permanent={leak('S1')} direction="top">{leak('S1') ? '💦 FUITE · ' : ''}{pipeName('S1', crops)}</Tooltip>
        </Polyline>
        {plots.map((p) => {
          const s = SECTION_OF[p.id]
          return (
            <Polyline key={`l${p.id}-${leak(s)}`} positions={[junction, [p.lat, p.lon]]} pathOptions={line(s)}>
              <Tooltip permanent={leak(s)} direction="center">
                {leak(s) ? '💦 FUITE · ' : ''}{pipeName(s, crops)}{p.valveOpen !== undefined ? ` · robinet ${p.valveOpen ? 'ouvert' : 'fermé'}` : ''}
              </Tooltip>
            </Polyline>
          )
        })}
        <Polyline positions={[[junction[0] - 0.00015, junction[1]], [tank[0] - 0.00015, tank[1]]]} pathOptions={{ ...line('S5', 2) }}>
          <Tooltip>{pipeName('S5', crops)}</Tooltip>
        </Polyline>
        <CircleMarker center={tank} radius={11} pathOptions={{ color: '#1b5ea6', fillColor: '#5fb4f8', fillOpacity: 0.9 }}>
          <Tooltip permanent direction="left">Cuve + pompe</Tooltip>
        </CircleMarker>
      </MapContainer>
    </div>
  )
}
