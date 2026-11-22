"""Garde-fous des actionneurs : UNE fonction, check_command, appelée par TOUTE commande.

Même règles quelle que soit l'origine (dashboard, agent IA, ESP32, minuterie interne). Actionneurs :
  pump       pompe principale (cuve → réseau d'irrigation, tronçon S1)
  valve A|B|C vannes des parcelles
  estop      arrêt d'urgence
  mode       retour au pilotage automatique (plan de l'optimiseur)

Règles, dans cet ordre :
  1. origine connue et action dans la liste blanche ;
  2. l'arrêt d'urgence est TOUJOURS accepté, et prioritaire sur tout ;
  3. le relâcher est réservé à un humain (dashboard / api), avec un motif ;
  4. arrêt d'urgence actif → seules les actions d'arrêt (pompe off, fermeture de vanne) passent ;
  5. arrêter la pompe est toujours permis ; fermer la dernière vanne ouverte pompe en marche est refusé ;
  6. démarrer la pompe : durée ≤ maximum, niveau de cuve connu et récent, cuve non vide,
     au moins une vanne ouverte, pas de redémarrage trop rapproché (anti-court-cycle).
Chaque décision est journalisée (qui, quoi, quand, acceptée ou refusée, pourquoi) par CommandJournal.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ALLOWED_ACTIONS: set[tuple[str, str]] = {
    ("pump", "on"), ("pump", "off"), ("valve", "open"), ("valve", "close"),
    ("estop", "engage"), ("estop", "release"), ("mode", "auto"),
}
# esp32_local : bouton physique de l'appareil (demande relayée par MQTT, jamais un humain authentifié :
# il peut arrêter ou demander la pompe, mais PAS relâcher l'arrêt d'urgence)
SOURCES = {"dashboard", "agent", "esp32", "esp32_local", "api", "safety", "scheduler", "test"}
HUMAN_SOURCES = {"dashboard", "api"}
STOP_ACTIONS = {("pump", "off"), ("valve", "close"), ("estop", "engage")}


@dataclass(frozen=True)
class SafetyLimits:
    """Limites (secondes et minutes RÉELLES : valables aussi pour une vraie pompe)."""

    max_pump_minutes: float = 120.0
    default_pump_minutes: float = 30.0
    min_tank_m3: float = 2.0
    max_tank_age_s: float = 120.0
    min_toggle_interval_s: float = 5.0
    valves: tuple[str, ...] = ("A", "B", "C")


@dataclass
class ActuatorCommand:
    """Commande demandée. target : pump | valve | estop | mode ; action : on/off, open/close, …"""

    source: str
    target: str
    action: str
    actor: str = ""
    valve_id: str | None = None
    duration_min: float | None = None
    reason: str = ""
    command_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ActuatorState:
    """État connu des actionneurs (mis à jour par les messages d'état des appareils)."""

    pump_on: bool = False
    pump_until: float | None = None          # horodatage (epoch) d'arrêt automatique
    valves: dict[str, bool] = field(default_factory=lambda: {"A": False, "B": False, "C": False})
    estop: bool = False
    mode: str = "auto"                       # auto (plan) | manual
    tank_m3: float | None = None
    tank_updated: float | None = None        # epoch de la dernière mesure de cuve
    last_pump_toggle: float | None = None
    device: str = "inconnu"


@dataclass
class Decision:
    accepted: bool
    reason: str
    command: ActuatorCommand
    duration_min: float | None = None
    decided_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "reason": self.reason, "duration_min": self.duration_min,
                "decided_at": self.decided_at, "command": asdict(self.command)}


def _refuse(cmd: ActuatorCommand, reason: str) -> Decision:
    return Decision(False, reason, cmd)


def check_command(cmd: ActuatorCommand, state: ActuatorState, limits: SafetyLimits, now: float) -> Decision:
    """Décide si la commande est sûre. `now` = epoch en secondes (injecté : testable)."""
    key = (cmd.target, cmd.action)
    if cmd.source not in SOURCES:
        return _refuse(cmd, f"origine inconnue « {cmd.source} »")
    if key not in ALLOWED_ACTIONS:
        return _refuse(cmd, f"action hors liste blanche : {cmd.target}.{cmd.action}")
    if key == ("estop", "engage"):
        return Decision(True, "arrêt d'urgence : prioritaire, toujours accepté", cmd)
    if key == ("estop", "release"):
        if cmd.source not in HUMAN_SOURCES:
            return _refuse(cmd, "seul un humain (dashboard) peut relâcher l'arrêt d'urgence")
        if not cmd.reason.strip():
            return _refuse(cmd, "motif obligatoire pour relâcher l'arrêt d'urgence")
        return Decision(True, "arrêt d'urgence relâché", cmd)
    if state.estop and key not in STOP_ACTIONS:
        return _refuse(cmd, "arrêt d'urgence actif : seules les commandes d'arrêt sont acceptées")
    if cmd.target == "valve" and cmd.valve_id not in limits.valves:
        return _refuse(cmd, f"vanne inconnue : {cmd.valve_id}")

    if key == ("pump", "off"):
        return Decision(True, "arrêt de la pompe : toujours permis", cmd)
    if key == ("valve", "close"):
        others_open = [v for v, o in state.valves.items() if o and v != cmd.valve_id]
        if state.pump_on and not others_open:
            return _refuse(cmd, "dernière vanne ouverte avec la pompe en marche : arrêter d'abord la pompe")
        return Decision(True, "fermeture de vanne", cmd)
    if key == ("valve", "open"):
        return Decision(True, "ouverture de vanne", cmd)
    if key == ("mode", "auto"):
        return Decision(True, "retour au pilotage automatique (plan de l'optimiseur)", cmd)

    # pump on
    duration = cmd.duration_min if cmd.duration_min is not None else limits.default_pump_minutes
    if duration <= 0:
        return _refuse(cmd, "durée de pompage invalide")
    if duration > limits.max_pump_minutes:
        return _refuse(cmd, f"durée {duration:.0f} min > maximum {limits.max_pump_minutes:.0f} min")
    if state.tank_m3 is None or state.tank_updated is None or now - state.tank_updated > limits.max_tank_age_s:
        return _refuse(cmd, "niveau de la cuve inconnu ou trop ancien : pompage refusé par prudence")
    if state.tank_m3 < limits.min_tank_m3:
        return _refuse(cmd, f"cuve vide ({state.tank_m3:.1f} m³ < {limits.min_tank_m3:.1f} m³) : marche à sec interdite")
    if not any(state.valves.values()):
        return _refuse(cmd, "aucune vanne ouverte : la pompe refoulerait sur un réseau fermé")
    if (not state.pump_on and state.last_pump_toggle is not None
            and now - state.last_pump_toggle < limits.min_toggle_interval_s):
        return _refuse(cmd, f"redémarrage trop rapproché (< {limits.min_toggle_interval_s:.0f} s) : anti-court-cycle")
    return Decision(True, f"marche autorisée pour {duration:.0f} min", cmd, duration_min=duration)


class CommandJournal:
    """Journal de TOUTES les décisions : fichier JSONL (une ligne par commande) + mémoire."""

    def __init__(self, path: Path | None, size: int = 500) -> None:
        self.path = path
        self.recent: deque[dict[str, Any]] = deque(maxlen=size)
        self.lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, decision: Decision) -> dict[str, Any]:
        entry = decision.to_dict()
        c = decision.command
        verdict = "ACCEPTÉE" if decision.accepted else "REFUSÉE"
        logger.log(logging.INFO if decision.accepted else logging.WARNING,
                   "Commande %s %s.%s%s par %s/%s : %s (%s)", c.command_id, c.target, c.action,
                   f"[{c.valve_id}]" if c.valve_id else "", c.source, c.actor or "?", verdict, decision.reason)
        with self.lock:
            self.recent.append(entry)
            if self.path is not None:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def last(self, n: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.recent)[-n:][::-1]
