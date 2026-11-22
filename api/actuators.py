"""Contrôleur des actionneurs : TOUTE commande passe par aquamind_core.safety.check_command.

Le même chemin sert au dashboard, à un agent IA ou à un ESP32 : seule l'origine (`source`) change.
Commandes publiées sur MQTT ({base}/actuator/.../cmd) ; l'appareil (simulateur aujourd'hui, ESP32
demain) répond sur {base}/actuator/.../state, ce qui met à jour l'état connu.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
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
CONFIRM_TIMEOUT_S = 5.0          # au-delà : « pas de réponse de l'appareil »
REQUEST_TARGETS = {("pump", "on"), ("pump", "off"), ("valve", "open"), ("valve", "close"), ("estop", "engage")}


class Bus(Protocol):
    def publish(self, rel_topic: str, body: dict[str, Any], qos: int = 1, retain: bool = False) -> bool: ...


class ActuatorController:
    def __init__(self, bus: Bus, limits: SafetyLimits | None = None, journal: CommandJournal | None = None,
                 notify: Callable[[dict[str, Any]], None] | None = None,
                 clock: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 offline_after_s: float = 15.0) -> None:
        self.bus = bus
        self.limits = limits or SafetyLimits()
        self.journal = journal or CommandJournal(JOURNAL_FILE)
        self.notify = notify or (lambda e: None)
        self.clock = clock
        self.mono = mono
        self.offline_after_s = offline_after_s
        self.state = ActuatorState()
        self.lock = threading.RLock()
        self._timer: threading.Timer | None = None
        # Suivi « envoyée → confirmée / refusée / sans réponse » (la vérité vient de l'appareil)
        self.inflight: dict[str, dict[str, Any]] = {}
        self.confirmations: deque[dict[str, Any]] = deque(maxlen=30)
        self.confirmed: dict[str, Any] = {}                 # dernier état rapporté par l'appareil
        # Appareil : battement de cœur (ESP32) ; le simulateur n'en envoie pas
        self.device: dict[str, Any] = {"name": None, "last_hb": None, "info": {}, "online": False,
                                       "said_offline": False}

    # ------------------------------------------------------------------ entrée unique
    def command(self, cmd: ActuatorCommand) -> dict[str, Any]:
        """Vérifie (safety), journalise, puis publie. Renvoie la décision et sa livraison."""
        with self.lock:
            decision = check_command(cmd, self.state, self.limits, self.clock())
            if (not decision.accepted and self.state.estop
                    and any(p["target"] == "estop" and p["action"] == "release" for p in self.inflight.values())):
                # L'appareil reste physiquement en arrêt tant qu'il n'a pas confirmé le relâchement : on refuse
                # (prudence), mais on dit pourquoi.
                decision.reason += (" — le relâchement est en cours de confirmation par l'appareil : "
                                    "réessayez dans un instant")
            delivered = self._apply(decision) if decision.accepted else False
            entry = self.journal.record(decision)
            entry["delivered"] = delivered
            if decision.accepted and not delivered:
                entry["reason"] += " — ATTENTION : broker MQTT indisponible, commande non transmise"
            if decision.accepted and delivered:
                self.inflight[cmd.command_id] = {
                    "command_id": cmd.command_id, "target": cmd.target, "action": cmd.action,
                    "valve_id": cmd.valve_id, "source": cmd.source, "sent": self.mono(),
                }
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
        # Une commande du plan (exécuteur, source « scheduler ») garde le mode automatique ;
        # toute autre commande pompe/vanne fait passer en manuel (l'humain a pris la main).
        mode = "auto" if c.source == "scheduler" else "manual"
        if c.target == "valve":
            is_open = c.action == "open"
            self.state.valves[c.valve_id] = is_open
            self.state.mode = mode
            return self.bus.publish(f"actuator/valve/{c.valve_id}/cmd", {**meta, "open": is_open})
        # pompe
        on = c.action == "on"
        if on != self.state.pump_on:
            self.state.last_pump_toggle = now
        self.state.pump_on = on
        self.state.mode = mode
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
        """{base}/actuator/<pump|valve/X|estop>/state → état CONFIRMÉ (la vérité vient de l'appareil).

        Si l'état cite une commande en attente (command_id), on mesure le délai envoi → confirmation
        et on publie « actuator_confirm » : ok ✅, refusée ❌ (raison de l'appareil) ou action locale."""
        parts = rel_topic.split("/")
        confirm: dict[str, Any] | None = None
        with self.lock:
            self.state.device = str(payload.get("device", self.state.device))
            if "mode" in payload:
                self.state.mode = str(payload["mode"])
            if parts[1] == "pump" and "on" in payload:
                self.state.pump_on = bool(payload["on"])
                self.confirmed["pump_on"] = self.state.pump_on
                if not self.state.pump_on:
                    self.state.pump_until = None
            elif parts[1] == "valve" and len(parts) >= 4 and parts[2] in self.state.valves:
                self.state.valves[parts[2]] = bool(payload.get("open", False))
                self.confirmed.setdefault("valves", {})[parts[2]] = self.state.valves[parts[2]]
            elif parts[1] == "estop" and "engaged" in payload:
                self.state.estop = bool(payload["engaged"])
                self.confirmed["estop"] = self.state.estop
            self.confirmed["mode"] = self.state.mode
            self.confirmed["device"] = self.state.device
            cid, result = payload.get("command_id"), str(payload.get("result") or "ok")
            pending = self.inflight.pop(cid, None) if cid else None
            if pending is not None:
                sent = pending.pop("sent")
                confirm = {**pending, "result": result if result in ("ok", "refused") else "ok",
                           "reason": str(payload.get("reason") or ""), "device": self.state.device,
                           "latency_ms": round((self.mono() - sent) * 1000)}
            elif result in ("local", "refused") and payload.get("reason"):
                confirm = {"command_id": cid, "target": parts[1], "action": None,
                           "valve_id": parts[2] if parts[1] == "valve" else None,
                           "source": str(payload.get("source") or "esp32_local"), "result": result,
                           "reason": str(payload["reason"]), "device": self.state.device, "latency_ms": None}
            if confirm is not None:
                confirm["at"] = self.clock()
                self.confirmations.appendleft(confirm)
                log = logger.warning if confirm["result"] == "refused" else logger.info
                log("Appareil %s : %s.%s %s%s", confirm["device"], confirm["target"], confirm.get("action") or "",
                    confirm["result"], f" en {confirm['latency_ms']} ms" if confirm["latency_ms"] is not None
                    else f" ({confirm['reason']})")
        self.notify({"type": "actuator_state", **self.snapshot()})
        if confirm is not None:
            self.notify({"type": "actuator_confirm", **confirm})

    # ------------------------------------------------------------------ appareil : boutons, battement
    def on_request(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Demande d'un bouton de l'appareil ({base}/actuator/request) → MÊME chemin que le dashboard :
        safety.check_command, journal, publication. La source est TOUJOURS « esp32_local » (jamais
        celle annoncée par le message) : un bouton ne peut pas relâcher l'arrêt d'urgence."""
        target, action = str(payload.get("target", "")), str(payload.get("action", ""))
        request_id = str(payload.get("request_id") or uuid.uuid4().hex[:8])[:40]
        if (target, action) not in REQUEST_TARGETS:
            entry = None
            reply = {"request_id": request_id, "accepted": False, "reason": f"demande non autorisée : {target}.{action}"}
        else:
            duration = payload.get("duration_min")
            entry = self.command(ActuatorCommand(
                source="esp32_local", actor=str(payload.get("device") or self.state.device)[:40], target=target,
                action=action, valve_id=str(payload["valve_id"]) if payload.get("valve_id") else None,
                duration_min=float(duration) if isinstance(duration, (int, float)) else None,
                reason=str(payload.get("reason") or "bouton de l'appareil")[:200]))
            reply = {"request_id": request_id, "accepted": entry["accepted"], "reason": entry["reason"],
                     "command_id": entry["command"]["command_id"]}
        self.bus.publish("actuator/request/result", reply)
        return entry

    def on_heartbeat(self, payload: dict[str, Any]) -> None:
        """Battement de cœur de l'appareil (toutes les 5 s)."""
        with self.lock:
            was_online = self.device["online"]
            self.device.update(name=str(payload.get("device") or "appareil"), last_hb=self.mono(),
                               info=payload, online=True, said_offline=False)
        if not was_online:
            logger.info("Appareil %s en ligne", self.device["name"])
            self.notify({"type": "device", **self.device_status()})

    def on_device_status(self, payload: dict[str, Any]) -> None:
        """{base}/device/status : {"online": false} = dernière volonté MQTT (déconnexion brutale)."""
        if payload.get("online") is False:
            with self.lock:
                changed = self.device["online"]
                self.device["online"] = False
                self.device["said_offline"] = True
            if changed:
                logger.warning("Appareil %s déconnecté (dernière volonté MQTT)", self.device["name"])
                self.notify({"type": "device", **self.device_status()})

    def tick(self) -> None:
        """Appelé chaque seconde : commandes sans réponse, appareil devenu muet."""
        now = self.mono()
        events: list[dict[str, Any]] = []
        with self.lock:
            for cid, p in list(self.inflight.items()):
                if now - p["sent"] > CONFIRM_TIMEOUT_S:
                    del self.inflight[cid]
                    ev = {**{k: v for k, v in p.items() if k != "sent"}, "result": "timeout", "latency_ms": None,
                          "reason": f"pas de réponse de l'appareil en {CONFIRM_TIMEOUT_S:.0f} s",
                          "device": self.state.device, "at": self.clock()}
                    self.confirmations.appendleft(ev)
                    events.append(ev)
                    # l'état affiché redevient celui que l'appareil a confirmé en dernier
                    if "pump_on" in self.confirmed:
                        self.state.pump_on = self.confirmed["pump_on"]
                    for v, o in self.confirmed.get("valves", {}).items():
                        self.state.valves[v] = o
                    logger.warning("Commande %s sans confirmation de l'appareil", cid)
            hb = self.device["last_hb"]
            if self.device["online"] and hb is not None and now - hb > self.offline_after_s:
                self.device["online"] = False
                events.append({"type": "device", "_status": True})
        for ev in events:
            if ev.get("_status"):
                logger.warning("Appareil %s hors-ligne (aucun battement depuis %.0f s)",
                               self.device["name"], self.offline_after_s)
                self.notify({"type": "device", **self.device_status()})
            else:
                self.notify({"type": "actuator_confirm", **ev})
        if any(not e.get("_status") for e in events):
            self.notify({"type": "actuator_state", **self.snapshot()})

    def device_status(self) -> dict[str, Any]:
        """Quel appareil, en ligne ou pas. Un ESP32 vu dans cette session reste « esp32 » même muet."""
        with self.lock:
            hb, name = self.device["last_hb"], self.device["name"]
            age = round(self.mono() - hb, 1) if hb is not None else None
            # ESP32 muet ET états venus du simulateur depuis : on est revenu au simulateur Python
            back_to_sim = (self.state.device == "simulateur" and age is not None and age > self.offline_after_s)
            if hb is not None and not back_to_sim:
                kind = "esp32" if str(name).startswith("esp32") else "appareil"
                online = self.device["online"] and age is not None and age <= self.offline_after_s
            else:
                kind = "simulateur" if self.state.device == "simulateur" else "inconnu"
                online = kind == "simulateur"
            if kind == "simulateur":
                label = "simulateur Python"
            else:
                label = {"esp32-wokwi": "ESP32 Wokwi", "esp32-twin": "ESP32 (jumeau Python)"}.get(
                    str(name), f"ESP32 ({name})" if kind == "esp32" else (name or "inconnu"))
            return {"device": name or self.state.device, "kind": kind, "label": label, "online": online,
                    "heartbeat_age_s": age, "offline_after_s": self.offline_after_s,
                    "info": {k: v for k, v in (self.device["info"] or {}).items() if k != "ts"}}

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
        mono = self.mono()
        with self.lock:
            st["inflight"] = [{**{k: v for k, v in p.items() if k != "sent"}, "age_s": round(mono - p["sent"], 1)}
                              for p in self.inflight.values()]
            st["last_confirmations"] = list(self.confirmations)[:10]
            st["confirmed"] = dict(self.confirmed)
        st["device_status"] = self.device_status()
        return st
