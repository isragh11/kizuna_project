// Vocabulaire simple pour l'agriculteur : noms des tuyaux, des cultures, des étapes, des commandes.
import type { PlotId } from './types'

export const CROP_EMOJI: Record<string, string> = { Tomate: '🍅', Olivier: '🫒', Piment: '🌶️', Blé: '🌾', Riz: '🌾' }
const PLURAL: Record<string, string> = { Tomate: 'tomates', Olivier: 'oliviers', Piment: 'piments', Blé: 'blé', Riz: 'riz' }
export const PLOT_COLOR: Record<PlotId, string> = { A: '#e2584d', B: '#6b9a2e', C: '#e08a1e' }

export type Crops = Partial<Record<PlotId, string>>

export const cropPlural = (crop?: string) => (crop ? PLURAL[crop] ?? crop.toLowerCase() : '')
export const emoji = (crop?: string) => (crop ? CROP_EMOJI[crop] ?? '🌱' : '🌱')

/** « Tomates (A) » */
export function plotName(id: PlotId, crops: Crops): string {
  const c = crops[id]
  return c ? `${cropPlural(c).replace(/^./, (x) => x.toUpperCase())} (${id})` : `Parcelle ${id}`
}

const SECTION_PLOT: Record<string, PlotId> = { S2: 'A', S3: 'B', S4: 'C' }

/** Nom réel d'un tuyau : S1 → « Tuyau principal », S3 → « Tuyau des oliviers (B) »… */
export function pipeName(section: string, crops: Crops): string {
  if (section === 'S1') return 'Tuyau principal'
  if (section === 'S5') return 'Retour vers la cuve'
  const id = SECTION_PLOT[section]
  if (!id) return section
  const c = crops[id]
  return c ? `Tuyau des ${cropPlural(c)} (${id})` : `Tuyau de la parcelle ${id}`
}

export const PIPE_HELP: Record<string, string> = {
  S1: "Le gros tuyau qui sort de la cuve : toute l'eau d'arrosage passe par lui.",
  S2: 'Le tuyau qui part vers la parcelle A, avec son robinet (vanne).',
  S3: 'Le tuyau qui part vers la parcelle B, avec son robinet (vanne).',
  S4: 'Le tuyau qui part vers la parcelle C, avec son robinet (vanne).',
  S5: 'Un petit tuyau qui renvoie un peu d\'eau dans la cuve pour garder une bonne pression.',
}

/** Étape de croissance en mots simples (à partir de la note du calendrier). */
export function stageText(note?: string | null): string {
  if (!note) return '—'
  const day = note.match(/jour (\d+)\/(\d+)/)
  const d = day ? ` (jour ${day[1]} sur ${day[2]})` : ''
  if (note.includes('jachère')) return 'Rien ne pousse (hors saison)'
  if (note.includes('repos')) return "Repos d'hiver"
  if (note.includes("saison d'irrigation")) return "Saison d'arrosage"
  if (note.startsWith('initial')) return `Jeune plante${d}`
  if (note.startsWith('développement')) return `En croissance${d}`
  if (note.startsWith('mi-saison')) return `Pleine croissance${d}`
  if (note.startsWith('arrière-saison')) return `Fin de saison${d}`
  return note
}

export function thirstText(kc: number): string {
  if (kc <= 0) return 'aucun'
  if (kc < 0.7) return 'faible'
  if (kc < 1.0) return 'moyen'
  return 'élevé'
}

export const METHOD_TEXT: Record<string, string> = { drip: 'Goutte-à-goutte', sprinkler: 'Aspersion (arroseurs)', flood: 'Submersion (inondation)' }

export function satisfactionText(s: number): { label: string; tone: 'ok' | 'mid' | 'low' | 'bad' } {
  if (s >= 0.95) return { label: 'Bien arrosée', tone: 'ok' }
  if (s >= 0.6) return { label: 'Un peu juste', tone: 'mid' }
  if (s >= 0.35) return { label: "Manque d'eau", tone: 'low' }
  return { label: 'Gros manque (survie)', tone: 'bad' }
}

export const LIMIT_TEXT: Record<string, { title: string; text: string }> = {
  water: { title: "Pas assez d'eau autorisée", text: "Le quota d'eau ne suffit pas pour tout le monde : l'IA partage l'eau entre les cultures." },
  energy: { title: "Pas assez d'électricité", text: "Le soleil et le réseau ne suffisent pas à pomper tout ce qu'il faudrait." },
  pump: { title: 'La pompe tourne au maximum', text: 'La pompe ne peut pas sortir plus d\'eau aux heures utiles.' },
  none: { title: 'Rien ne manque', text: 'Toutes les cultures reçoivent l\'eau dont elles ont besoin.' },
}

const COMMAND_TEXT: Record<string, string> = {
  'pump.on': 'Démarrer la pompe', 'pump.off': 'Arrêter la pompe', 'valve.open': 'Ouvrir le robinet',
  'valve.close': 'Fermer le robinet', 'estop.engage': "Arrêt d'urgence", 'estop.release': "Fin de l'arrêt d'urgence",
  'mode.auto': "Rendre la main à l'IA",
}
export function commandText(target: string, action: string, valve?: string | null, crops: Crops = {}): string {
  const base = COMMAND_TEXT[`${target}.${action}`] ?? `${target}.${action}`
  return valve ? `${base} — ${plotName(valve as PlotId, crops)}` : base
}

export const SOURCE_TEXT: Record<string, string> = {
  dashboard: 'Vous (tableau de bord)', agent: 'Assistant IA', esp32: 'Boîtier de la ferme', safety: 'Sécurité automatique',
  api: 'Système', test: 'Test',
}

/** Raisons du serveur reformulées pour l'agriculteur. */
export function reasonText(reason: string): string {
  const r = reason.toLowerCase()
  if (r.includes('aucune vanne ouverte')) return "Ouvrez d'abord un robinet : la pompe ne doit pas pousser l'eau dans des tuyaux fermés."
  if (r.includes('cuve vide')) return 'La cuve est presque vide : la pompe tournerait à sec et s\'abîmerait.'
  if (r.includes('niveau de la cuve inconnu')) return 'On ne connaît pas le niveau de la cuve en ce moment : par prudence, on ne pompe pas.'
  if (r.includes('maximum')) return 'Durée trop longue : 2 heures au maximum, pour ne pas vider la cuve par oubli.'
  if (r.includes("arrêt d'urgence actif")) return "L'arrêt d'urgence est actif : seules les commandes d'arrêt sont possibles."
  if (r.includes('seul un humain')) return "Seule une personne peut relâcher l'arrêt d'urgence, pas une machine."
  if (r.includes('motif obligatoire')) return 'Écrivez pourquoi il est sûr de relâcher.'
  if (r.includes('dernière vanne')) return 'Arrêtez d\'abord la pompe (ou utilisez « Couper cette parcelle »).'
  if (r.includes('court-cycle')) return 'Attendez quelques secondes avant de redémarrer la pompe (protection du moteur).'
  if (r.includes('toujours permis') || r.includes('toujours accepté')) return 'Les arrêts sont toujours acceptés.'
  if (r.includes('marche autorisée')) return reason.replace('marche autorisée pour', 'Pompe autorisée pour')
  if (r.includes('relâché')) return "L'arrêt d'urgence est levé."
  if (r.includes('retour au pilotage automatique')) return "L'IA reprend l'arrosage selon le plan."
  return reason
}

export const m3ToLiters = (m3: number) => Math.round(m3 * 1000)
