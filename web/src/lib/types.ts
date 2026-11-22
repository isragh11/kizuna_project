export type Site = 'tunisia' | 'japan'
export type PlotId = 'A' | 'B' | 'C'
export const PLOTS: PlotId[] = ['A', 'B', 'C']
export const SECTION_OF: Record<PlotId, string> = { A: 'S2', B: 'S3', C: 'S4' }
export const SECTIONS = ['S1', 'S2', 'S3', 'S4', 'S5'] as const

export interface Alert {
  type?: string
  alert_id?: string
  ts: string
  section: string
  score?: number
  detector?: string
  est_loss_m3_day: number
  est_loss_kwh_day: number
  est_loss_tnd_day: number
  est_loss_m3h?: number | null
  confirmed_by_ai?: boolean
  river_score?: number | null
  message: string
  active?: boolean
  received_at?: number | string
}

export interface ActuatorState {
  pump_on: boolean
  pump_until: number | null
  pump_remaining_s: number | null
  valves: Record<PlotId, boolean>
  estop: boolean
  mode: string
  tank_m3: number | null
  tank_age_s: number | null
  device: string
  limits?: { max_pump_minutes: number; default_pump_minutes: number; min_tank_m3: number }
}

export interface JournalEntry {
  accepted: boolean
  reason: string
  delivered?: boolean
  decided_at: string
  duration_min?: number | null
  command: { source: string; actor: string; target: string; action: string; valve_id?: string | null; command_id: string; ts: string; reason: string }
}

export interface Health {
  status: 'ok' | 'degraded'
  reasons?: string[]
  mqtt: boolean
  storage: string | null
  last_reading_age_s?: number | null
  simulator?: { alive: boolean; last_reading_age_s: number | null; status?: any }
  consumer: { embedded: boolean; ready: boolean; mode: string | null; stats: Record<string, number> | null }
  plan: { mode: string; source: string | null; computed_at: string | null }
  scheduler: boolean
  websocket_clients: number
}

export interface PlotResult {
  crop: string
  ky: number
  perennial?: boolean
  method: string
  kc: number
  stage_note: string
  need_m3: number
  delivered_m3: number
  base_served_m3: number
  margin_served_m3: number
  deficit_m3: number
  satisfaction: number
  tranche_values_tnd_per_m3: number[]
  tranche_fill: number[]
  value_protected_tnd: number
  value_lost_tnd: number
  priority: number
}

export interface OptimizeResult {
  horizon_h: number
  allocation: Record<string, number[]>
  pump_m3: number[]
  tank_m3: number[]
  pv_available_kwh: number[]
  grid_kwh: number[]
  pv_kwh: number[]
  pump_kwh: number[]
  satisfaction: Record<string, number>
  plots: Record<string, PlotResult>
  duals: { water: number; energy: number[]; pump: number[]; tank: number }
  kpis: Record<string, number>
  baseline_kpis: Record<string, number>
  explanations: string[]
  limiting_factor: 'water' | 'energy' | 'pump' | 'none'
  solve_time_ms: number
  inputs: Record<string, any>
}

export interface FlowPoint {
  t: string // heure simulée HH:MM
  simTs: string
  S1: number
  S2: number
  S3: number
  S4: number
  S5: number
  plan: number
  imbalance: number
  tank?: number
  pv?: number
}
