// Accès à l'API FastAPI, avec cache local : si l'API est coupée, on affiche les dernières données reçues.

const envUrl = (import.meta.env.VITE_API_URL as string | undefined)?.trim().replace(/\/$/, '')
export const API_URL = envUrl || 'http://127.0.0.1:8000'
export const WS_URL =
  (import.meta.env.VITE_WS_URL as string | undefined)?.trim() || API_URL.replace(/^http/, 'ws') + '/ws/live'

const CACHE_PREFIX = 'aquamind:cache:'

export interface Cached<T> {
  data: T
  fromCache: boolean
  savedAt?: string
}

export interface SendResult<T = any> {
  ok: boolean
  status: number
  data: T
}

function save(path: string, data: unknown) {
  try {
    localStorage.setItem(CACHE_PREFIX + path, JSON.stringify({ savedAt: new Date().toISOString(), data }))
  } catch {
    /* stockage plein ou désactivé : on continue sans cache */
  }
}

function load<T>(path: string): { savedAt: string; data: T } | null {
  try {
    const raw = localStorage.getItem(CACHE_PREFIX + path)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

async function fetchWithTimeout(url: string, init: RequestInit, timeoutMs: number) {
  const ctrl = new AbortController()
  const t = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    return await fetch(url, { ...init, signal: ctrl.signal })
  } finally {
    clearTimeout(t)
  }
}

export function errorText(body: any, fallback: string): string {
  if (!body) return fallback
  if (typeof body.detail === 'string') return body.detail
  if (Array.isArray(body.detail)) return body.detail.map((d: any) => d.msg).join(' ; ')
  if (typeof body.reason === 'string') return body.reason
  return fallback
}

/** GET avec cache : en cas de panne réseau, renvoie la dernière réponse connue (fromCache = true). */
export async function apiGet<T>(path: string, opts: { cache?: boolean; timeoutMs?: number } = {}): Promise<Cached<T>> {
  try {
    const r = await fetchWithTimeout(API_URL + path, {}, opts.timeoutMs ?? 20000)
    const body = await r.json().catch(() => null)
    if (!r.ok) throw Object.assign(new Error(errorText(body, `HTTP ${r.status}`)), { status: r.status })
    if (opts.cache !== false) save(path, body)
    return { data: body as T, fromCache: false }
  } catch (e: any) {
    if (e?.status) throw e
    const c = opts.cache !== false ? load<T>(path) : null
    if (c) return { data: c.data, fromCache: true, savedAt: c.savedAt }
    throw e
  }
}

/** POST : renvoie toujours le statut et le corps (409 = refusé par un garde-fou, avec la raison). */
export async function apiSend<T = any>(path: string, body?: unknown, timeoutMs = 60000): Promise<SendResult<T>> {
  const r = await fetchWithTimeout(
    API_URL + path,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) },
    timeoutMs,
  )
  const data = await r.json().catch(() => null)
  return { ok: r.ok, status: r.status, data }
}

export function cachedAt(path: string): string | null {
  return load(path)?.savedAt ?? null
}
