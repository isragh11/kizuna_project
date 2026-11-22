"""Contrat MQTT entre AquaMind et l'appareil « pompe + vannes A/B/C » (ESP32 Wokwi ou jumeau Python).

Tous les topics sont relatifs : {base}/<topic> sur le Mosquitto local, {préfixe aléatoire}/<topic> sur le
broker public. Le pont (iot/bridge_public.py) ne relaie QUE ces listes, chacune dans un seul sens :
aucun message ne peut revenir en boucle.

API → appareil (local → public)
  actuator/pump/cmd          {"on", "duration_s", "command_id", "source"}
  actuator/valve/{A|B|C}/cmd {"open", "command_id", "source"}
  actuator/estop/cmd         {"engaged", "command_id", "source"}   (retenu)
  actuator/mode/cmd          {"mode": "auto", "command_id", "source"}
  actuator/request/result    {"request_id", "accepted", "reason", "command_id"}
  status/api                 {"ts", "alive": true}     toutes les 5 s : sans lui 30 s → coupure locale
  sim/tick                   {"ts"}                    heure de la ferme simulée : l'appareil publie ses débits
  tank/level                 {"level_m3", …}           protection marche à sec locale
  cmd/leak, cmd/reset, cmd/flush                       fuites de démonstration (comme pour le simulateur)

Appareil → API (public → local)
  actuator/pump/state, actuator/valve/{A|B|C}/state, actuator/estop/state
        état CONFIRMÉ : {"ts", "device", "on"|"open"|"engaged", "mode", "command_id", "result", "reason", "source"}
        result : "ok" (commande appliquée), "refused" (refusée par l'appareil, raison), "local" (action locale)
  actuator/request   demande locale (bouton de l'appareil) : {"request_id", "target", "action", "valve_id",
                     "duration_min", "reason"} — l'API la fait passer par safety.py avec la source "esp32_local"
  device/heartbeat   toutes les 5 s : {"ts", "device", "uptime_s", "api_ok", "pump", "valves", "estop", "leak", …}
  device/status      {"online": bool} (retenu ; « dernière volonté » MQTT à la déconnexion)
  device/event       {"type": "leak"|"repair"|…, …}
  pipe/{S1..S5}/flow {"ts", "flow_m3h", "plan_m3h", "pressure_bar"}   (même format que le simulateur)
  plot/A/soil        {"ts", "moisture_m3m3"}
"""

from __future__ import annotations

import json
import math
import random
import re
from typing import Any

DOWN_TOPICS = [            # local → public (vers l'appareil)
    "actuator/pump/cmd", "actuator/valve/+/cmd", "actuator/estop/cmd", "actuator/mode/cmd",
    "actuator/request/result", "status/api", "sim/tick", "tank/level", "cmd/leak", "cmd/reset", "cmd/flush",
]
UP_TOPICS = [              # public → local (depuis l'appareil)
    "actuator/pump/state", "actuator/valve/+/state", "actuator/estop/state", "actuator/request",
    "device/heartbeat", "device/status", "device/event", "pipe/+/flow", "plot/A/soil",
]
VALVE_IDS = ("A", "B", "C")
SECTIONS = ("S1", "S2", "S3", "S4", "S5")
PLOT_SECTION = {"A": "S2", "B": "S3", "C": "S4"}
MAX_PAYLOAD_BYTES = 4096
PREFIX_RE = re.compile(r"^[a-z0-9][a-z0-9-]{7,40}$")

# Physique des débits : mêmes constantes que simulator/physics.py (le détecteur de fuites y est réglé)
RETURN_FRAC = 0.08
NOISE_REL, NOISE_ABS = 0.02, 0.05
LEAK_SECTION_SHARE = 0.25
PUMP_MAX_M3H = 10.0          # potentiomètre 1 au maximum
DRY_TANK_M3 = 0.3
MAX_PUMP_S = 7200            # durée maximale de marche côté appareil (= safety.max_pump_minutes)
API_SILENCE_S = 30.0         # sans « status/api » : tout couper


def _match(pattern: str, rel: str) -> bool:
    p, r = pattern.split("/"), rel.split("/")
    return len(p) == len(r) and all(a == "+" or a == b for a, b in zip(p, r))


def allowed(direction: str, rel: str) -> bool:
    """Le pont relaie-t-il ce topic relatif dans ce sens ? direction : 'down' | 'up'."""
    patterns = DOWN_TOPICS if direction == "down" else UP_TOPICS
    if any(_match(p, rel) for p in patterns):
        if "/valve/" in rel and rel.split("/")[2] not in VALVE_IDS:
            return False
        if rel.startswith("pipe/") and rel.split("/")[1] not in SECTIONS:
            return False
        return True
    return False


def is_telemetry(rel: str) -> bool:
    """Mesures (débits, sol) : seules soumises à la limite de débit du pont. Le reste (états, demandes,
    battement de cœur, événements) passe toujours."""
    return rel.startswith(("pipe/", "plot/"))


def relative(topic: str, prefix: str) -> str | None:
    """« prefix/a/b » → « a/b » ; None si le topic n'est pas sous ce préfixe."""
    return topic[len(prefix) + 1:] if topic.startswith(prefix + "/") else None


def decode(raw: bytes) -> dict[str, Any] | None:
    """Charge utile acceptée : objet JSON de taille raisonnable, sinon None (message ignoré)."""
    if raw is None or len(raw) > MAX_PAYLOAD_BYTES:
        return None
    try:
        data = json.loads(raw or b"{}")
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def device_flows(pump_m3h: float, pump_on: bool, valves: dict[str, bool], estop: bool,
                 leak: tuple[str, float] | None = None, flush_m3h: float = 0.0,
                 rng: random.Random | None = None, noise: bool = True) -> dict[str, dict[str, float]]:
    """Débits mesurés S1..S5 de l'appareil (même modèle que FarmSimulator.plan_flows + step).

    La pompe ne débite que si elle tourne, qu'au moins une vanne est ouverte et sans arrêt d'urgence.
    Une fuite sur la branche i (vanne ouverte) ajoute son débit à S1 et retire 25 % de ce débit à la
    mesure de la branche : le bilan S1 = S2+S3+S4+S5 est rompu, le détecteur le voit."""
    rng = rng or random.Random()
    open_ids = [v for v in VALVE_IDS if valves.get(v)]
    pumping = pump_on and bool(open_ids) and not estop and pump_m3h > 0
    plan = {s: 0.0 for s in SECTIONS}
    if pumping:
        delivered = pump_m3h * (1 - RETURN_FRAC)
        for v in open_ids:
            plan[PLOT_SECTION[v]] = delivered / len(open_ids)
        plan["S5"] = pump_m3h * RETURN_FRAC
        plan["S1"] = pump_m3h
    meas = {s: (plan[s] * (1 + NOISE_REL * rng.gauss(0, 1)) + NOISE_ABS * rng.gauss(0, 1)) if noise else plan[s]
            for s in SECTIONS}
    leak_rate = 0.0
    if pumping and leak:
        section, rate = leak
        if section == "S1" or plan.get(section, 0.0) > 0.1:
            meas["S1"] += rate
            if section != "S1":
                meas[section] -= LEAK_SECTION_SHARE * rate
            leak_rate = rate
    if pumping:
        meas["S1"] += flush_m3h
    pressure = (2.5 - 0.25 * leak_rate + (0.03 * rng.gauss(0, 1) if noise else 0.0)) if pumping else 0.2
    return {s: {"flow_m3h": round(max(0.0, meas[s]), 4), "plan_m3h": round(plan[s], 4),
                "pressure_bar": round(pressure, 3)} for s in SECTIONS}


def leak_target(valves: dict[str, bool]) -> str:
    """Bouton « percer un tuyau » : la branche de la première vanne ouverte, sinon le tuyau principal."""
    for v in VALVE_IDS:
        if valves.get(v):
            return PLOT_SECTION[v]
    return "S1"


def soil_from_pot(raw: int) -> float:
    """Potentiomètre 2 (0..4095) → humidité volumique du sol de A (0,05..0,45 m³/m³)."""
    return round(0.05 + 0.40 * max(0, min(4095, raw)) / 4095, 4)


def flow_from_pot(raw: int) -> float:
    """Potentiomètre 1 (0..4095) → débit de la pompe (0..10 m³/h)."""
    return round(PUMP_MAX_M3H * max(0, min(4095, raw)) / 4095, 3)


def gauss_box_muller(u1: float, u2: float) -> float:
    """Loi normale à partir de deux tirages uniformes (même méthode que le sketch ESP32)."""
    return math.sqrt(-2.0 * math.log(max(u1, 1e-12))) * math.cos(2 * math.pi * u2)
