import { useEffect, useState } from 'react'
import { apiGet } from './api'
import { useLive } from './live'
import type { OptimizeResult } from './types'

/** Dernier plan appliqué (GET /optimize/last), rechargé à chaque nouveau plan diffusé par le WebSocket. */
export function usePlan() {
  const { planVersion, apiOnline } = useLive()
  const [plan, setPlan] = useState<OptimizeResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fromCache, setFromCache] = useState(false)
  useEffect(() => {
    apiGet<OptimizeResult>('/optimize/last')
      .then((r) => { setPlan(r.data); setFromCache(r.fromCache); setError(null) })
      .catch((e) => setError(String(e?.message ?? e)))
  }, [planVersion, apiOnline])
  return { plan, error, fromCache }
}

export interface ScenarioInfo { key: string; title: string; description: string; site: 'tunisia' | 'japan'; start: string; quota_factor: number }

export function useScenarios() {
  const [list, setList] = useState<ScenarioInfo[]>([])
  useEffect(() => { apiGet<ScenarioInfo[]>('/scenarios').then((r) => setList(r.data)).catch(() => {}) }, [])
  return list
}
