// Relevé du contour en marchant : le téléphone enregistre la position GPS pendant qu'on fait le tour du champ.
// Intégré depuis la branche master, rendu robuste :
// - pause et seuil passent par des refs (le suivi GPS garde sinon les valeurs du démarrage) ;
// - une perte de signal passagère n'arrête pas le relevé, seul un refus d'autorisation l'arrête ;
// - l'écran reste allumé (Wake Lock) : sur téléphone, écran verrouillé = plus de GPS ;
// - à la fin, le tour est nettoyé (doublons, dépassement du départ, bruit, petites boucles).
import { useEffect, useRef, useState } from 'react'
import { Footprints, Pause, Play, Square, Undo2 } from 'lucide-react'
import { Button, InfoTip } from './ui'
import { cleanGpsTrack, distanceM, measure, MIN_AREA_M2, type PolygonGeometry } from '../lib/parcels'

export interface GpsPoint { latitude: number; longitude: number; timestamp: number; accuracy: number; altitude: number | null }

const MIN_STEP_M = 3          // on ignore les points à moins de 3 m du précédent (on piétine)
const MAX_POINTS = 4000       // ~ 12 km de tour : au-delà on arrête de stocker (le serveur accepte 5 000 coins)

export function GpsRecorder({ onGeometry, onTrack, onRecording }: {
  onGeometry: (g: PolygonGeometry) => void
  onTrack: (points: GpsPoint[]) => void
  onRecording?: (on: boolean) => void
}) {
  const [recording, setRecording] = useState(false)
  const [paused, setPaused] = useState(false)
  const [points, setPoints] = useState<GpsPoint[]>([])
  const [accuracy, setAccuracy] = useState<number | null>(null)
  const [threshold, setThreshold] = useState(25)
  const [ignored, setIgnored] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [signalLost, setSignalLost] = useState(false)
  const [screenOn, setScreenOn] = useState<boolean | null>(null)
  const watchId = useRef<number | null>(null)
  const wakeLock = useRef<any>(null)
  const pausedRef = useRef(paused)
  const thresholdRef = useRef(threshold)
  const recordingRef = useRef(recording)
  pausedRef.current = paused
  thresholdRef.current = threshold
  recordingRef.current = recording

  useEffect(() => { onTrack(points) }, [points, onTrack])
  useEffect(() => { onRecording?.(recording) }, [recording, onRecording])

  // --- écran allumé pendant le relevé
  const keepScreenOn = async () => {
    try {
      wakeLock.current = await (navigator as any).wakeLock?.request('screen')
      setScreenOn(!!wakeLock.current)
    } catch { setScreenOn(false) }
  }
  const releaseScreen = () => { try { wakeLock.current?.release() } catch { /* déjà relâché */ } wakeLock.current = null }
  useEffect(() => {
    const back = () => { if (document.visibilityState === 'visible' && recordingRef.current) keepScreenOn() }
    document.addEventListener('visibilitychange', back)
    return () => {
      document.removeEventListener('visibilitychange', back)
      if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current)
      releaseScreen()
    }
  }, [])

  const stopWatch = () => {
    if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current)
    watchId.current = null
    releaseScreen()
  }

  const start = () => {
    setError(null); setNote(null)
    if (!('geolocation' in navigator)) { setError("Cet appareil ne donne pas sa position (pas de GPS)."); return }
    if (!window.isSecureContext) { setError('Le GPS du navigateur exige une adresse sécurisée (https ou localhost).'); return }
    setPoints([]); setIgnored(0); setPaused(false); setSignalLost(false); setRecording(true)
    keepScreenOn()
    watchId.current = navigator.geolocation.watchPosition(
      (pos) => {
        const p: GpsPoint = {
          latitude: pos.coords.latitude, longitude: pos.coords.longitude, timestamp: pos.timestamp,
          accuracy: pos.coords.accuracy, altitude: pos.coords.altitude,
        }
        if (!Number.isFinite(p.latitude) || !Number.isFinite(p.longitude)) return
        setAccuracy(p.accuracy)
        setSignalLost(false)
        if (pausedRef.current) return
        if (!(p.accuracy <= thresholdRef.current)) { setIgnored((n) => n + 1); return }
        setPoints((cur) => {
          const prev = cur[cur.length - 1]
          if (prev && distanceM([prev.longitude, prev.latitude], [p.longitude, p.latitude]) < MIN_STEP_M) return cur
          if (cur.length >= MAX_POINTS) return cur
          return [...cur, p]
        })
      },
      (e) => {
        // Seul un refus d'autorisation arrête le relevé. Une perte de signal (code 2) ou un délai
        // dépassé (code 3) est passagère pendant une marche (arbres, mur, hangar) : on continue d'écouter.
        if (e.code === 1) {
          setError('Localisation refusée : autorisez-la dans les réglages du navigateur (icône à gauche de l\'adresse), puis recommencez.')
          stopWatch(); setRecording(false)
        } else {
          setSignalLost(true)
        }
      },
      { enableHighAccuracy: true, maximumAge: 1000, timeout: 20000 },
    )
  }

  const finish = () => {
    stopWatch(); setRecording(false); setPaused(false); setSignalLost(false)
    if (points.length < 3) { setError('Il faut au moins 3 points pour fermer le champ. Marchez un peu plus le long de la limite.'); return }
    // Bruit toléré : la moitié de la précision médiane, entre 1 et 5 m
    const accs = points.map((p) => p.accuracy).sort((a, b) => a - b)
    const noise = Math.min(5, Math.max(1, accs[Math.floor(accs.length / 2)] / 2))
    const { ring, gapM, untangled } = cleanGpsTrack(points.map((p) => [p.longitude, p.latitude]), noise)
    if (!ring) { setError('Le tour enregistré ne forme pas un champ (vous avez peut-être marché en ligne droite). Recommencez en faisant tout le tour.'); return }
    const g: PolygonGeometry = { type: 'Polygon', coordinates: [ring] }
    if (measure(g).areaM2 < MIN_AREA_M2) { setError('Le tour enregistré est trop petit pour être un champ. Recommencez en suivant toute la limite.'); return }
    const notes: string[] = []
    if (gapM > 15) notes.push(`Vous vous êtes arrêté à ${Math.round(gapM)} m du départ : le contour a été refermé en ligne droite.`)
    if (untangled) notes.push('De petites boucles dues au GPS ont été retirées automatiquement.')
    setNote(notes.join(' ') || null)
    onGeometry(g)
  }

  const walked = points.reduce((s, p, i) => (i ? s + distanceM([points[i - 1].longitude, points[i - 1].latitude], [p.longitude, p.latitude]) : 0), 0)
  const backToStart = points.length > 5 ? distanceM([points[0].longitude, points[0].latitude], [points[points.length - 1].longitude, points[points.length - 1].latitude]) : null
  const tooImprecise = accuracy !== null && accuracy > threshold

  return (
    <div className="space-y-3 rounded-xl border border-ink-200 bg-white p-3 text-sm">
      <ol className="list-decimal space-y-0.5 pl-5 text-ink-700">
        <li>Placez-vous à un coin du champ, téléphone en main.</li>
        <li>Appuyez sur « Commencer » et faites le tour en suivant la limite.</li>
        <li>Revenez au point de départ puis appuyez sur « Terminer ».</li>
      </ol>

      <div className="grid grid-cols-3 gap-2 text-center">
        <div className={`rounded-lg p-2 ${tooImprecise ? 'bg-sun-50 text-sun-600' : 'bg-ink-50'}`}>
          <div className="font-semibold tabular-nums">{accuracy === null ? '—' : `± ${Math.round(accuracy)} m`}</div>
          <div className="text-[11px] text-ink-500">précision</div>
        </div>
        <div className="rounded-lg bg-ink-50 p-2"><div className="font-semibold tabular-nums">{points.length}</div><div className="text-[11px] text-ink-500">points gardés</div></div>
        <div className="rounded-lg bg-ink-50 p-2"><div className="font-semibold tabular-nums">{Math.round(walked)} m</div><div className="text-[11px] text-ink-500">parcourus</div></div>
      </div>
      {recording && backToStart !== null && (
        <p className={`rounded-lg p-2 text-center ${backToStart <= 10 ? 'bg-leaf-50 text-leaf-700' : 'bg-ink-50 text-ink-700'}`}>
          {backToStart <= 10 ? '✅ Vous êtes revenu au départ : appuyez sur « Terminer ».' : `Départ à ${Math.round(backToStart)} m`}
        </p>
      )}

      <label className="block text-ink-700">
        <span className="flex items-center">Ignorer les points moins précis que {threshold} m
          <InfoTip text="Le GPS d'un téléphone est précis à 3–10 m à découvert, beaucoup moins sous les arbres ou près des murs. Les points plus imprécis que ce seuil sont ignorés pour ne pas déformer le champ." /></span>
        <input type="range" min={5} max={100} step={5} value={threshold} onChange={(e) => setThreshold(Number(e.target.value))}
          className="mt-1 w-full accent-water-600" aria-label="Seuil de précision en mètres" />
      </label>

      {tooImprecise && recording && <p className="rounded-lg bg-sun-50 p-2 text-sun-600">Signal trop imprécis : points ignorés pour l'instant{ignored ? ` (${ignored})` : ''}. Attendez quelques secondes à découvert, ou augmentez le seuil.</p>}
      {signalLost && recording && <p className="rounded-lg bg-sun-50 p-2 text-sun-600">Signal GPS perdu un instant : le relevé continue, il reprendra dès que le téléphone retrouve sa position.</p>}
      {recording && screenOn === false && <p className="rounded-lg bg-water-50 p-2 text-water-700">Gardez l'écran allumé pendant le tour : un téléphone verrouillé n'enregistre plus sa position.</p>}
      {paused && <p className="rounded-lg bg-water-50 p-2 text-water-700">En pause : déplacez-vous sans enregistrer (obstacle, portail…), puis reprenez.</p>}
      {error && <p className="rounded-lg bg-red-50 p-2 text-red-700">{error}</p>}
      {note && !recording && <p className="rounded-lg bg-sun-50 p-2 text-sun-600">{note}</p>}

      <div className="flex flex-wrap gap-2">
        {!recording ? (
          <Button variant="primary" onClick={start} className="w-full sm:w-auto"><Footprints size={15} /> {points.length ? 'Recommencer un tour' : 'Commencer le tour'}</Button>
        ) : (
          <>
            <Button onClick={() => setPaused((v) => !v)}>{paused ? <><Play size={15} /> Reprendre</> : <><Pause size={15} /> Pause</>}</Button>
            <Button onClick={() => setPoints((cur) => cur.slice(0, -1))} disabled={!points.length}><Undo2 size={15} /> Retirer le dernier point</Button>
            <Button variant="success" onClick={finish} className="w-full sm:w-auto"><Square size={15} /> Terminer et fermer le champ</Button>
          </>
        )}
      </div>
    </div>
  )
}
