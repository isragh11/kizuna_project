// Données temps réel partagées : WebSocket /ws/live (reconnexion automatique), santé de l'API,
// état des actionneurs, alertes. Les mises à jour sont regroupées (≤ 2 rendus/s) pour rester fluide.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { apiGet, WS_URL } from './api'
import { SOURCE_TEXT, commandText, reasonText } from './labels'
import type { ActuatorState, Alert, FlowPoint, Health, Site } from './types'

const MAX_POINTS = 144 // 24 h simulées

interface LiveData {
  wsConnected: boolean
  apiOnline: boolean
  health: Health | null
  healthFromCache: boolean
  lastApiOkAt: number | null
  lastReadingAt: number | null // horloge du navigateur (ms)
  series: FlowPoint[]
  latest: { flows: Record<string, { flow: number; plan: number; pressure?: number }>; tank?: number; tankPct?: number; pv?: number; soil: Record<string, number> }
  alerts: Alert[]
  actuators: ActuatorState | null
  plotInfo: Record<string, { crop: string; stage: string; note?: string; kc: number; method: string }>
  events: { ts: number; type: string; text: string }[]
  planVersion: number
  site: Site
  setSite: (s: Site) => void
  refreshActuators: () => void
  refreshAlerts: () => void
}

const LiveContext = createContext<LiveData | null>(null)

/** Heure RÉELLE de réception d'une alerte (ms), pour le tri ; à défaut, son horodatage. */
export function alertTime(a: Alert): number {
  const r = a.received_at
  if (typeof r === 'number') return r
  const t = Date.parse(String(r ?? (a as any)._time ?? a.ts))
  return Number.isNaN(t) ? 0 : t
}

export function useLive(): LiveData {
  const ctx = useContext(LiveContext)
  if (!ctx) throw new Error('useLive hors de LiveProvider')
  return ctx
}

export function LiveProvider({ children }: { children: ReactNode }) {
  const [, setVersion] = useState(0)
  const dirty = useRef(false)
  const d = useRef<Omit<LiveData, 'setSite' | 'refreshActuators' | 'refreshAlerts' | 'site'>>({
    wsConnected: false, apiOnline: true, health: null, healthFromCache: false, lastApiOkAt: null, lastReadingAt: null,
    series: [], latest: { flows: {}, soil: {} }, alerts: [], actuators: null, plotInfo: {}, events: [], planVersion: 0,
  })
  const pending = useRef<Record<string, Record<string, { flow: number; plan: number }>>>({})
  const [site, setSiteState] = useState<Site>(() => (localStorage.getItem('aquamind:site') as Site) || 'tunisia')
  const setSite = useCallback((s: Site) => { localStorage.setItem('aquamind:site', s); setSiteState(s) }, [])
  const touch = () => { dirty.current = true }

  // ---------------------------------------------------------------- messages WebSocket
  const handle = useCallback((m: any) => {
    const cur = d.current
    switch (m.type) {
      case 'hello':
        if (m.actuators) cur.actuators = m.actuators
        if (Array.isArray(m.alerts)) mergeAlerts(m.alerts)
        if (m.plot_info) for (const [id, v] of Object.entries(m.plot_info as Record<string, any>)) cur.plotInfo[id] = v
        break
      case 'reading': {
        cur.lastReadingAt = Date.now()
        const v = m.values || {}
        if (m.kind === 'flow') {
          cur.latest.flows[m.source_id] = { flow: v.flow_m3h, plan: v.plan_m3h, pressure: v.pressure_bar }
          const tick = (pending.current[m.ts] ||= {})
          tick[m.source_id] = { flow: v.flow_m3h, plan: v.plan_m3h }
          if (Object.keys(tick).length === 5) {
            delete pending.current[m.ts]
            for (const k of Object.keys(pending.current)) if (k < m.ts) delete pending.current[k]
            const f = (s: string) => tick[s]?.flow ?? 0
            cur.series = [...cur.series.slice(-(MAX_POINTS - 1)), {
              t: m.ts.slice(11, 16), simTs: m.ts, S1: f('S1'), S2: f('S2'), S3: f('S3'), S4: f('S4'), S5: f('S5'),
              plan: tick.S1?.plan ?? 0, imbalance: f('S1') - f('S2') - f('S3') - f('S4') - f('S5'),
              tank: cur.latest.tank, pv: cur.latest.pv,
            }]
          }
        } else if (m.kind === 'tank') {
          cur.latest.tank = v.level_m3
          cur.latest.tankPct = v.level_pct
        } else if (m.kind === 'pv') {
          cur.latest.pv = v.power_kw
        } else if (m.kind === 'soil') {
          cur.latest.soil[m.source_id] = v.moisture_m3m3
        }
        break
      }
      case 'alert':
        mergeAlerts([{ ...m, active: true, received_at: m.received_at ?? Date.now() }])
        pushEvent('alert', `Alerte ${m.section} : ${(m.message || '').slice(0, 90)}…`)
        break
      case 'alert_update':
        cur.alerts = cur.alerts.map((a) => { if (a.alert_id !== m.alert_id) return a; const { type: _t, ...rest } = m; void _t; return { ...a, ...rest } })
        break
      case 'alert_end':
        cur.alerts = cur.alerts.map((a) => (a.alert_id === m.alert_id ? { ...a, active: false } : a))
        pushEvent('alert_end', 'Fin d\'alerte : bilan de masse rétabli')
        break
      case 'new_normal':
        cur.alerts = cur.alerts.map((a) => ({ ...a, active: false }))
        pushEvent('new_normal', `Nouveau normal (${m.source ?? 'commande'})`)
        break
      case 'alerts_cleared':
        cur.alerts = cur.alerts.filter((a) => a.active)
        break
      case 'no_flow':
        pushEvent('no_flow', m.message)
        break
      case 'actuator_state':
        cur.actuators = { ...(cur.actuators || {}), ...m } as ActuatorState
        break
      case 'actuator_command':
        pushEvent('command', `${commandText(m.command?.target, m.command?.action, m.command?.valve_id)} (${SOURCE_TEXT[m.command?.source] ?? m.command?.source}) : ${m.accepted ? 'fait' : 'refusé — ' + reasonText(m.reason ?? '')}`)
        break
      case 'plan':
        cur.planVersion += 1
        pushEvent('plan', `Nouveau plan : ${m.meta?.source ?? ''}`)
        break
      case 'plot_info': {
        const id = String(m.topic || '').split('/')[1]
        if (id) cur.plotInfo[id] = { crop: m.crop, stage: m.stage, note: m.note, kc: m.kc, method: m.method }
        break
      }
    }
    touch()
  }, [])

  function mergeAlerts(list: Alert[]) {
    const cur = d.current
    const byId = new Map(cur.alerts.map((a) => [a.alert_id ?? a.ts + a.section, a]))
    for (const a of list) {
      const key = a.alert_id ?? a.ts + a.section
      byId.set(key, { ...byId.get(key), ...a, active: a.active ?? byId.get(key)?.active ?? false })
    }
    cur.alerts = [...byId.values()].sort((x, y) => alertTime(y) - alertTime(x)).slice(0, 100)
  }

  function pushEvent(type: string, text: string) {
    d.current.events = [{ ts: Date.now(), type, text }, ...d.current.events].slice(0, 40)
  }

  // ---------------------------------------------------------------- connexion WebSocket
  useEffect(() => {
    let ws: WebSocket | null = null
    let stopped = false
    let retry = 0
    let timer: number | undefined
    const connect = () => {
      ws = new WebSocket(WS_URL)
      ws.onopen = () => { retry = 0; d.current.wsConnected = true; touch() }
      ws.onmessage = (ev) => { try { handle(JSON.parse(ev.data)) } catch { /* message illisible ignoré */ } }
      ws.onclose = () => {
        d.current.wsConnected = false
        touch()
        if (!stopped) timer = window.setTimeout(connect, Math.min(10000, 1000 * 2 ** retry++))
      }
      ws.onerror = () => ws?.close()
    }
    connect()
    return () => { stopped = true; window.clearTimeout(timer); ws?.close() }
  }, [handle])

  // ---------------------------------------------------------------- sondages : santé, actionneurs, alertes
  const refreshActuators = useCallback(() => {
    apiGet<ActuatorState>('/actuators/state').then((r) => { if (!r.fromCache) { d.current.actuators = r.data; touch() } }).catch(() => {})
  }, [])
  const refreshAlerts = useCallback(() => {
    apiGet<Alert[]>('/alerts?limit=50').then((r) => { mergeAlerts(r.data); touch() }).catch(() => {})
  }, [])

  useEffect(() => {
    let alive = true
    const poll = async () => {
      try {
        const r = await apiGet<Health>('/health', { timeoutMs: 4000 })
        if (!alive) return
        d.current.health = r.data
        d.current.healthFromCache = r.fromCache
        d.current.apiOnline = !r.fromCache
        if (!r.fromCache) d.current.lastApiOkAt = Date.now()
      } catch {
        d.current.apiOnline = false
      }
      touch()
    }
    poll()
    refreshAlerts()
    refreshActuators()
    const h = window.setInterval(poll, 3000)
    const a = window.setInterval(refreshActuators, 4000)
    const flush = window.setInterval(() => { if (dirty.current) { dirty.current = false; setVersion((v) => v + 1) } }, 500)
    return () => { alive = false; clearInterval(h); clearInterval(a); clearInterval(flush) }
  }, [refreshActuators, refreshAlerts])

  // Objet recréé à chaque rendu groupé (≤ 2/s) : les champs modifiés en place (latest, plotInfo) sont vus.
  const value: LiveData = { ...d.current, latest: { ...d.current.latest }, site, setSite, refreshActuators, refreshAlerts }
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>
}

/** Âge (s) de la dernière mesure : vu par l'API (/health) ou, à défaut, par le navigateur (WebSocket). */
export function readingAgeSeconds(l: LiveData): number | null {
  const fromApi = l.health?.last_reading_age_s
  if (typeof fromApi === 'number' && l.apiOnline) return fromApi
  return l.lastReadingAt ? (Date.now() - l.lastReadingAt) / 1000 : null
}
