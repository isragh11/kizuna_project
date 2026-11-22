import { useLive } from './live'
import type { Crops } from './labels'
import { PLOTS, type OptimizeResult } from './types'

/** Culture de chaque parcelle : celle de la ferme en direct, sinon celle du plan. */
export function useCrops(plan?: OptimizeResult | null): Crops {
  const { plotInfo } = useLive()
  const out: Crops = {}
  for (const id of PLOTS) {
    out[id] = plotInfo[id]?.crop ?? plan?.inputs?.crops?.[id]?.crop ?? plan?.plots?.[id]?.crop
  }
  return out
}
