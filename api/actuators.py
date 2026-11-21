"""Contrôleur des actionneurs : TOUTE commande passe par aquamind_core.safety.check_command.

Le même chemin sert au dashboard, à un agent IA ou à un ESP32 : seule l'origine (`source`) change.
Commandes publiées sur MQTT ({base}/actuator/.../cmd) ; l'appareil (simulateur aujourd'hui, ESP32
demain) répond sur {base}/actuator/.../state, ce qui met à jour l'état connu.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict
from typing import Any, Callable, Protocol

from aquamind_core.config import DATA_DIR
from aquamind_core.safety import (
    ActuatorCommand,
    ActuatorState,
    CommandJournal,
    Decision,
    SafetyLimits,
    check_command,
)

logger = logging.getLogger(__name__)
JOURNAL_FILE = DATA_DIR / "actuator_log.jsonl"


class Bus(Protocol):
    def publish(self, rel_topic: str, body: dict[str, Any], qos: int = 1, retain: bool = False) -> bool: ...


class ActuatorController:
    def __init__(self, bus: Bus, limits: SafetyLimits | None = None, journal: CommandJournal | None = None,
                 notify: Callable[[dict[str, Any]], None] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.bus = bus
        self.limits = limits or SafetyLimits()
        self.journal = journal or CommandJournal(JOURNAL_FILE)
        self.notify = notify or (lambda e: None)
        self.clock = clock
        self.state = ActuatorState()
        self.lock = threading.RLock()
        self._timer: threading.Timer | None = None

    # ------------------------------------------------------------------ entrée unique
    def command(self, cmd: ActuatorCommand) -> dict[str, Any]:
        """Vérifie (safety), journalise, puis publie. Renvoie la décision et sa livraison."""
        with self.lock:
            decision = check_command(cmd, self.state, self.limits, self.clock())
            delivered = self._apply(decision) if decision.accepted else False
            entry = self.journal.record(decision)
            entry["delivered"] = delivered
            if decision.accepted and not delivered:
                entry["reason"] += " — ATTENTION : broker MQTT indisponible, commande non transmise"
        self.notify({"type": "actuator_command", **entry})
        return entry

    def isolate(self, valve_id: str, source: str, actor: str = "", reason: str = "") -> dict[str, Any]:
        """Isoler une parcelle, de façon atomique : 1) arrêt pompe → 2) fermeture de la vanne.

        Chaque étape passe par safety.check_command (pompe off et fermeture sont des actions d'arrêt,
        acceptées même sous arrêt d'urgence) ; la pompe étant arrêtée d'abord, on peut toujours fermer
        la dernière vanne ouverte. Les autres parcelles restent arrêtées jusqu'à une nouvelle commande."""
        why = f"isolement de la parcelle {valve_id}" + (f" : {reason}" if reason else "")
        with self.lock:                                  # aucune autre commande ne s'intercale
            steps = [self.command(ActuatorCommand(source=source, actor=actor, target="pump", action="off",
                                                  reason=why))]
            if steps[0]["accepted"]:
                steps.append(self.command(ActuatorCommand(source=source, actor=actor, target="valve",
                                                          action="close", valve_id=valve_id, reason=why)))
        accepted = all(s["accepted"] for s in steps) and len(steps) == 2
        delivered = all(s.get("delivered", False) for s in steps)
        return {"accepted": accepted, "delivered": delivered, "valve_id": valve_id, "steps": steps,
                "reason": ("parcelle isolée : pompe arrêtée puis vanne fermée" if accepted
                           else f"isolement incomplet : {steps[-1]['reason']}")}

    def _apply(self, d: Decision) -> bool:
        c, now = d.command, self.clock()
        meta = {"command_id": c.command_id, "source": c.source, "actor": c.actor, "ts": c.ts}
        if c.target == "estop":
            engaged = c.action == "engage"
            self.state.estop = engaged
            ok = self.bus.publish("actuator/estop/cmd", {**meta, "engaged": engaged}, retain=True)
            if engaged:                                     # tout couper, explicitement
                self._cancel_timer()
                ok &= self.bus.publish("actuator/pump/cmd", {**meta, "on": False})
                for v in self.state.valves:
                    ok &= self.bus.publish(f"actuator/valve/{v}/cmd", {**meta, "open": False})
                self.state.pump_on, self.state.pump_until = False, None
                self.state.valves = {v: False for v in self.state.valves}
                self.state.mode = "manual"
            return ok
        if c.target == "mode":
            self.state.mode = "auto"
            self._cancel_timer()
            return self.bus.publish("actuator/mode/cmd", {**meta, "mode": "auto"})
        if c.target == "valve":
            is_open = c.action == "open"
            self.state.valves[c.valve_id] = is_open
            self.state.mode = "manual"
            return self.bus.publish(f"actuator/valve/{c.valve_id}/cmd", {**meta, "open": is_open})
        # pompe
        on = c.action == "on"
        if on != self.state.pump_on:
            self.state.last_pump_toggle = now
        self.state.pump_on = on
        self.state.mode = "manual"
        self._cancel_timer()
        if on:
            seconds = d.duration_min * 60
            self.state.pump_until = now + seconds
            self._timer = threading.Timer(seconds, self._auto_off)
            self._timer.daemon = True
            self._timer.start()
            return self.bus.publish("actuator/pump/cmd", {**meta, "on": True, "duration_s": int(seconds)})
        self.state.pump_until = None
        return self.bus.publish("actuator/pump/cmd", {**meta, "on": False})

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _auto_off(self) -> None:
        """Durée maximale atteinte : arrêt par la minuterie de sécurité (passe aussi par safety)."""
        self.command(ActuatorCommand(source="safety", actor="minuterie", target="pump", action="off",
                                     reason="durée de pompage autorisée écoulée"))

    # ------------------------------------------------------------------ retours des appareils
    def on_device_state(self, rel_topic: str, payload: dict[str, Any]) -> None:
        """{base}/actuator/<pump|valve/X|estop>/state → état connu (la vérité vient de l'appareil)."""
        parts = rel_topic.split("/")
        with self.lock:
            self.state.device = str(payload.get("device", self.state.device))
            if "mode" in payload:
                self.state.mode = str(payload["mode"])
            if parts[1] == "pump" and "on" in payload:
                self.state.pump_on = bool(payload["on"])
                if not self.state.pump_on:
                    self.state.pump_until = None
            elif parts[1] == "valve" and len(parts) >= 4 and parts[2] in self.state.valves:
                self.state.valves[parts[2]] = bool(payload.get("open", False))
            elif parts[1] == "estop" and "engaged" in payload:
                self.state.estop = bool(payload["engaged"])
        self.notify({"type": "actuator_state", **self.snapshot()})

    def on_tank_level(self, level_m3: float) -> None:
        with self.lock:
            self.state.tank_m3 = float(level_m3)
            self.state.tank_updated = self.clock()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            st = asdict(self.state)
        now = self.clock()
        st["pump_remaining_s"] = max(0, int(st["pump_until"] - now)) if st["pump_until"] else None
        st["tank_age_s"] = round(now - st["tank_updated"], 1) if st["tank_updated"] else None
        st["limits"] = asdict(self.limits)
        return st
