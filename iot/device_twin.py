"""Jumeau Python de l'ESP32 « pompe + vannes A/B/C » : MÊME protocole et MÊMES règles que
iot/wokwi/sketch.ino. Sert aux tests automatiques et à une démo sans navigateur Wokwi.

    .\\.venv\\Scripts\\python -m iot.device_twin                 # via le broker public (comme Wokwi), TLS vérifié
    .\\.venv\\Scripts\\python -m iot.device_twin --broker local  # directement sur Mosquitto (sans pont)
    touches : p = bouton pompe · f = bouton « percer un tuyau » · e = arrêt d'urgence physique · q = quitter

Sécurité locale (identique au sketch) :
  1. arrêt d'urgence (commande ou bouton) : tout est coupé, prioritaire, verrouillé jusqu'au relâchement
     par l'API (un humain, via safety.py) ;
  2. aucun « status/api » depuis 30 s : pompe arrêtée et vannes fermées ;
  3. pas de pompe sans vanne ouverte ; pas de fermeture de la dernière vanne pompe en marche ;
  4. durée maximale de marche (durée demandée, plafonnée à 2 h) ;
  5. cuve vide (< 0,3 m³) : pompe refusée / arrêtée ;
  un refus est publié comme ÉTAT, avec la raison.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import secrets
import ssl
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from iot import protocol

logger = logging.getLogger("jumeau")
Msg = tuple[str, dict[str, Any], bool]          # (topic relatif, charge utile, retenu)
HEARTBEAT_S = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DeviceLogic:
    """État et règles de l'appareil, sans réseau (testable). Chaque méthode renvoie les messages à publier."""

    name: str = "esp32-twin"
    pump_m3h: float = 6.0                 # potentiomètre 1
    soil_m3m3: float = 0.25               # potentiomètre 2 (parcelle A)
    pump_on: bool = False
    valves: dict[str, bool] = field(default_factory=lambda: {v: False for v in protocol.VALVE_IDS})
    estop: bool = False
    mode: str = "auto"
    pump_until: float | None = None
    leak: tuple[str, float] | None = None
    flush_m3h: float = 0.0
    tank_m3: float | None = None
    api_last: float | None = None
    api_ok: bool = False
    started: float = field(default_factory=time.monotonic)
    last_hb: float = -1e9
    last_message: str = ""
    rng: random.Random = field(default_factory=random.Random)
    _req: int = 0

    # ------------------------------------------------------------------ états publiés
    def _state(self, kind: str, valve: str | None = None, command_id: str | None = None, result: str = "ok",
               reason: str = "", source: str | None = None, now: float | None = None) -> Msg:
        common: dict[str, Any] = {"ts": _now_iso(), "device": self.name, "mode": self.mode,
                                  "command_id": command_id, "result": result, "reason": reason, "source": source}
        if kind == "pump":
            rem = max(0, int(self.pump_until - (now or time.monotonic()))) if self.pump_on and self.pump_until else None
            return ("actuator/pump/state", {**common, "on": self.pump_on, "remaining_s": rem}, True)
        if kind == "valve":
            return (f"actuator/valve/{valve}/state", {**common, "open": self.valves[valve]}, True)
        return ("actuator/estop/state", {**common, "engaged": self.estop}, True)

    def all_states(self, reason: str = "", result: str = "ok", source: str | None = None) -> list[Msg]:
        msgs = [self._state("estop", result=result, reason=reason, source=source),
                self._state("pump", result=result, reason=reason, source=source)]
        msgs += [self._state("valve", v, result=result, reason=reason, source=source) for v in protocol.VALVE_IDS]
        return msgs

    def _cut_all(self) -> None:
        self.pump_on, self.pump_until = False, None
        self.valves = {v: False for v in protocol.VALVE_IDS}

    # ------------------------------------------------------------------ messages reçus
    def on_message(self, rel: str, p: dict[str, Any], now: float) -> list[Msg]:
        cid, src = p.get("command_id"), p.get("source")
        if rel == "status/api":
            self.api_last, self.api_ok = now, True
            return []
        if rel == "sim/tick":
            return self.flows(str(p.get("ts") or _now_iso()))
        if rel == "tank/level":
            if isinstance(p.get("level_m3"), (int, float)):
                self.tank_m3 = float(p["level_m3"])
            return []
        if rel == "cmd/leak":
            self.leak = (str(p.get("section", "S3")), float(p.get("rate_m3h", 1.5)))
            return []
        if rel == "cmd/reset":
            self.leak, self.flush_m3h = None, 0.0
            return []
        if rel == "cmd/flush":
            self.flush_m3h = max(0.0, float(p.get("rate_m3h", 0.6)))
            return []
        if rel == "actuator/request/result":
            self.last_message = ("OK : " if p.get("accepted") else "REFUS : ") + str(p.get("reason", ""))[:60]
            return []
        if rel == "actuator/estop/cmd":
            if p.get("engaged", True):
                self.estop = True
                self._cut_all()
                self.mode = "manual"
            else:
                self.estop = False
            return [self._state("estop", command_id=cid, source=src)] + \
                [self._state("pump", source=src)] + [self._state("valve", v, source=src) for v in protocol.VALVE_IDS]
        if rel == "actuator/mode/cmd":
            if self.estop:
                return [self._state("pump", command_id=cid, result="refused", reason="arrêt d'urgence actif", source=src)]
            if p.get("mode") == "auto":
                self.mode = "auto"
            return [self._state("pump", command_id=cid, source=src)]
        manual_mode = "auto" if src == "scheduler" else "manual"
        if rel == "actuator/pump/cmd":
            if p.get("on"):
                why = self._pump_on_refusal()
                if why:
                    return [self._state("pump", command_id=cid, result="refused", reason=why, source=src, now=now)]
                dur = min(float(p.get("duration_s") or 1800), protocol.MAX_PUMP_S)
                self.pump_on, self.pump_until, self.mode = True, now + dur, manual_mode
            else:
                self.pump_on, self.pump_until, self.mode = False, None, manual_mode
            return [self._state("pump", command_id=cid, source=src, now=now)]
        if rel.startswith("actuator/valve/") and rel.endswith("/cmd"):
            v = rel.split("/")[2]
            if v not in protocol.VALVE_IDS:
                return []
            if p.get("open"):
                if self.estop:
                    return [self._state("valve", v, command_id=cid, result="refused", reason="arrêt d'urgence actif", source=src)]
                self.valves[v] = True
            else:
                others = [x for x, o in self.valves.items() if o and x != v]
                if self.pump_on and not others:
                    return [self._state("valve", v, command_id=cid, result="refused",
                                        reason="dernière vanne ouverte avec la pompe en marche", source=src)]
                self.valves[v] = False
            self.mode = manual_mode
            return [self._state("valve", v, command_id=cid, source=src)]
        return []

    def _pump_on_refusal(self) -> str:
        if self.estop:
            return "arrêt d'urgence actif"
        if not any(self.valves.values()):
            return "aucune vanne ouverte"
        if self.tank_m3 is not None and self.tank_m3 < protocol.DRY_TANK_M3:
            return "cuve vide : marche à sec interdite"
        return ""

    # ------------------------------------------------------------------ surveillance locale
    def poll(self, now: float) -> list[Msg]:
        out: list[Msg] = []
        if self.pump_on and self.pump_until is not None and now >= self.pump_until:
            self.pump_on, self.pump_until = False, None
            out.append(self._state("pump", result="local", reason="durée maximale atteinte : arrêt", source="esp32_local"))
        if self.pump_on and self.tank_m3 is not None and self.tank_m3 < protocol.DRY_TANK_M3:
            self.pump_on, self.pump_until = False, None
            out.append(self._state("pump", result="local", reason="cuve vide : pompe arrêtée", source="esp32_local"))
        if self.api_ok and self.api_last is not None and now - self.api_last > protocol.API_SILENCE_S:
            self.api_ok = False
            if self.pump_on or any(self.valves.values()):
                self._cut_all()
                out += self.all_states(reason="API muette depuis 30 s : coupure de sécurité", result="local",
                                       source="esp32_local")
        if now - self.last_hb >= HEARTBEAT_S:
            self.last_hb = now
            out.append(("device/heartbeat", {
                "ts": _now_iso(), "device": self.name, "uptime_s": int(now - self.started), "api_ok": self.api_ok,
                "pump": self.pump_on, "valves": dict(self.valves), "estop": self.estop,
                "leak": self.leak[0] if self.leak else "", "flow_m3h": self.pump_m3h, "fw": "aquamind-twin-1"}, False))
        return out

    # ------------------------------------------------------------------ boutons
    def _request(self, target: str, action: str, **extra: Any) -> Msg:
        self._req += 1
        return ("actuator/request", {"request_id": f"{self.name}-{self._req}", "device": self.name,
                                     "target": target, "action": action, **extra}, False)

    def press_estop(self) -> list[Msg]:
        """Bouton rouge : coupure LOCALE immédiate (avant tout réseau), puis demande à l'API (journal)."""
        self.estop = True
        self._cut_all()
        self.mode = "manual"
        return self.all_states(reason="arrêt d'urgence physique", result="local", source="esp32_local") + \
            [self._request("estop", "engage", reason="bouton d'arrêt d'urgence de l'appareil")]

    def press_pump(self) -> list[Msg]:
        """Bouton pompe : arrêter est immédiat et local (toujours sûr) ; démarrer passe par l'API."""
        if self.pump_on:
            self.pump_on, self.pump_until, self.mode = False, None, "manual"
            return [self._state("pump", result="local", reason="arrêt par le bouton de l'appareil", source="esp32_local"),
                    self._request("pump", "off", reason="bouton pompe de l'appareil")]
        return [self._request("pump", "on", duration_min=30, reason="bouton pompe de l'appareil")]

    def press_leak(self) -> list[Msg]:
        if self.leak:
            self.leak = None
            return [("device/event", {"ts": _now_iso(), "device": self.name, "type": "repair"}, False)]
        self.leak = (protocol.leak_target(self.valves), 1.5)
        return [("device/event", {"ts": _now_iso(), "device": self.name, "type": "leak",
                                  "section": self.leak[0], "rate_m3h": 1.5}, False)]

    # ------------------------------------------------------------------ mesures
    def flows(self, ts: str) -> list[Msg]:
        f = protocol.device_flows(self.pump_m3h, self.pump_on, self.valves, self.estop, self.leak, self.flush_m3h,
                                  rng=self.rng)
        msgs: list[Msg] = [(f"pipe/{s}/flow", {"ts": ts, **v}, False) for s, v in f.items()]
        msgs.append(("plot/A/soil", {"ts": ts, "moisture_m3m3": round(self.soil_m3m3, 4)}, False))
        return msgs


# ---------------------------------------------------------------------------------- exécution MQTT

class TwinRunner:
    """Le jumeau branché sur MQTT. `press('p'|'f'|'e')` simule un bouton (clavier ou script de test)."""

    def __init__(self, broker: str = "public", pump_m3h: float = 6.0, soil: float = 0.25,
                 name: str = "esp32-twin") -> None:
        import paho.mqtt.client as mqtt

        from aquamind_core.config import get_settings
        from aquamind_core.tls import verified_context

        s = get_settings()
        if broker == "public":
            self.prefix, self.host, self.port = s.wokwi_topic_prefix, s.public_mqtt_host, s.public_mqtt_port
            if not self.prefix:
                sys.exit("WOKWI_TOPIC_PREFIX absent : lancer `python scripts/wokwi_config.py`")
        else:
            self.prefix, self.host, self.port = s.mqtt_base_topic, s.mqtt_host, s.mqtt_port
        self.name = name
        self.dev = DeviceLogic(name=name, pump_m3h=pump_m3h, soil_m3m3=soil)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"aqm-twin-{secrets.token_hex(3)}")
        if broker == "public":

            self.client.tls_set_context(verified_context())                # certificat et nom d'hôte vérifiés
        self.client.will_set(f"{self.prefix}/device/status", json.dumps({"online": False, "device": name}),
                             qos=1, retain=True)
        self.client.on_connect, self.client.on_message = self._on_connect, self._on_message
        self.client.reconnect_delay_set(1, 10)
        self._thread: threading.Thread | None = None

    def send(self, msgs: list[Msg]) -> None:
        for rel, payload, retain in msgs:
            self.client.publish(f"{self.prefix}/{rel}", json.dumps(payload, ensure_ascii=False), qos=1, retain=retain)

    def _on_connect(self, c, u, f, rc, p) -> None:
        if rc.is_failure:
            logger.error("Connexion refusée : %s", rc)
            return
        c.subscribe([(f"{self.prefix}/{t}", 1) for t in protocol.DOWN_TOPICS])
        c.publish(f"{self.prefix}/device/status", json.dumps({"online": True, "device": self.name}), qos=1, retain=True)
        with self.lock:
            self.send(self.dev.all_states(source="esp32_local"))
        logger.info("Jumeau %s connecté à %s:%s (préfixe %s…)", self.name, self.host, self.port, self.prefix[:8])

    def _on_message(self, c, u, msg) -> None:
        rel = protocol.relative(msg.topic, self.prefix)
        payload = protocol.decode(msg.payload)
        if rel is None or payload is None:
            return
        with self.lock:
            out = self.dev.on_message(rel, payload, time.monotonic())
            self.send(out)
        if rel.endswith("/cmd") or rel == "actuator/request/result":
            logger.info("%s %s → %s", rel, {k: v for k, v in payload.items()
                                            if k in ("on", "open", "engaged", "mode", "accepted", "reason")},
                        [m[1].get("result") for m in out if m[0].endswith("/state")] or self.dev.last_message)

    def press(self, key: str) -> None:
        with self.lock:
            if key == "p":
                self.send(self.dev.press_pump())
            elif key == "f":
                self.send(self.dev.press_leak())
            elif key == "e":
                self.send(self.dev.press_estop())
        logger.info("Bouton %s · pompe %s · vannes %s · arrêt d'urgence %s · fuite %s", key, self.dev.pump_on,
                    self.dev.valves, self.dev.estop, self.dev.leak)

    def _loop(self) -> None:
        while not self.stop.wait(0.2):
            with self.lock:
                self.send(self.dev.poll(time.monotonic()))

    def start(self) -> "TwinRunner":
        self.client.connect_async(self.host, self.port, keepalive=15)
        self.client.loop_start()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self.stop.set()
        if self._thread:
            self._thread.join(2)
        self.client.publish(f"{self.prefix}/device/status", json.dumps({"online": False, "device": self.name}),
                            qos=1, retain=True).wait_for_publish(3)
        self.client.loop_stop()
        self.client.disconnect()


def run(broker: str, pump_m3h: float, soil: float, stop: threading.Event | None = None,
        name: str = "esp32-twin") -> None:
    twin = TwinRunner(broker, pump_m3h, soil, name).start()
    stop = stop or twin.stop

    def keys() -> None:
        for line in sys.stdin:
            k = line.strip().lower()[:1]
            if k == "q":
                stop.set()
                return
            if k in ("p", "f", "e"):
                twin.press(k)

    if sys.stdin and sys.stdin.isatty():
        threading.Thread(target=keys, daemon=True).start()
    try:
        while not stop.wait(0.5) and not twin.stop.is_set():
            pass
    finally:
        twin.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Jumeau Python de l'ESP32 AquaMind")
    ap.add_argument("--broker", choices=["public", "local"], default="public")
    ap.add_argument("--flow", type=float, default=6.0, help="débit de la pompe (m³/h), comme le potentiomètre 1")
    ap.add_argument("--soil", type=float, default=0.25, help="humidité du sol de A (m³/m³), potentiomètre 2")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()
    import signal

    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run(args.broker, args.flow, args.soil, stop)


if __name__ == "__main__":
    main()
