"""Simulateur de capteurs : publie le flux de la ferme sur MQTT (paho-mqtt v2).

    python -m simulator.publisher                       # plan : API → optimiseur (prévision) → secours
    python -m simulator.publisher --plan scenario:tunisia_heatwave --start-hour 8
    python -m simulator.publisher --http-fallback       # POST vers l'API si MQTT indisponible

Horloge accélérée : 1 message = 10 min simulées (comme le notebook 04) ; avec SIM_SECONDS_PER_HOUR=5,
un pas toutes les 0,83 s réelles. Topics (base = MQTT_BASE_TOPIC) :
  {base}/plot/{A|B|C}/soil   {base}/plot/{A|B|C}/info (retenu : culture, stade)
  {base}/pipe/{S1..S5}/flow  {base}/pv/power  {base}/tank/level
Commandes écoutées :
  {base}/cmd/leak   {"section": "S3", "rate_m3h": 1.5}   fuite sur un tronçon
  {base}/cmd/flush  {"rate_m3h": 0.6}  (0 = arrêt)        rinçage des filtres (dérive légitime)
  {base}/cmd/reset  {}                                    répare tout (fuites + rinçage)
  {base}/cmd/speed  {"sim_seconds_per_hour": 1}           accélère l'horloge (tests)
  {base}/cmd/replan {}                                    recharge le dernier plan
Actionneurs (le simulateur joue le rôle de l'appareil, en attendant l'ESP32) :
  {base}/actuator/pump/cmd          {"on": true}       → {base}/actuator/pump/state (retenu)
  {base}/actuator/valve/{A|B|C}/cmd {"open": true}     → {base}/actuator/valve/{id}/state
  {base}/actuator/estop/cmd         {"engaged": true}  → {base}/actuator/estop/state
  {base}/actuator/mode/cmd          {"mode": "auto"}   → retour au plan
Les garde-fous sont appliqués AVANT par l'API (aquamind_core/safety.py) ; l'appareil applique en plus
localement l'arrêt d'urgence et la protection contre la marche à sec.
"""

from __future__ import annotations

import argparse
import json
import os
import logging
import queue
import signal
import threading
import time
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt

from aquamind_core.config import get_settings, load_farm
from aquamind_core.leaks import load_river_config
from simulator.physics import FarmSimulator
from simulator.plan import PlanSchedule, load_plan

logger = logging.getLogger("simulator")


def _plain(o: object) -> object:
    """Types numpy (bool_, float64…) → types Python, pour json.dumps (sinon TypeError)."""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"{type(o).__name__} non sérialisable")


class Publisher:
    """Boucle temps réel : un pas de simulation, publication, puis attente du pas suivant."""

    def __init__(self, plan_mode: str, start_hour: int | None, http_fallback: bool, seed: int) -> None:
        self.settings = get_settings()
        self.base = self.settings.mqtt_base_topic
        self.tick_min = int(load_river_config().get("tick_minutes", 10))
        self.sim_s_per_h = float(self.settings.sim_seconds_per_hour)
        self.plan_mode = plan_mode
        self.http_fallback = http_fallback
        self.plan: PlanSchedule = load_plan(plan_mode)
        self.sim = FarmSimulator(load_farm(self.plan.farm_file), self.plan, self.tick_min, seed=seed)
        self.ts = self.plan.start_dt + timedelta(hours=start_hour if start_hour is not None else 0)
        self.commands: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.stop = threading.Event()
        self.connected = threading.Event()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"aquamind-sim-{seed}-{os.getpid()}")
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = lambda c, u, m: self.commands.put((m.topic, self._json(m.payload)))

    @staticmethod
    def _json(raw: bytes) -> dict:
        try:
            data = json.loads(raw or b"{}")
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.error("Connexion MQTT refusée : %s", reason_code)
            return
        client.subscribe([(f"{self.base}/cmd/#", 1), (f"{self.base}/actuator/+/cmd", 1),
                          (f"{self.base}/actuator/valve/+/cmd", 1)])
        self.connected.set()
        logger.info("Connecté à MQTT %s:%s, base %s", self.settings.mqtt_host, self.settings.mqtt_port, self.base)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self.connected.clear()
        logger.warning("Déconnecté de MQTT (%s) : reconnexion automatique", reason_code)

    # ------------------------------------------------------------- commandes
    def _apply_commands(self) -> None:
        while not self.commands.empty():
            topic, cmd = self.commands.get_nowait()
            name = topic.rsplit("/", 1)[-1]
            rel = topic[len(self.base) + 1:]
            if rel.startswith("actuator/"):
                parts = rel.split("/")                       # actuator/pump/cmd, actuator/valve/A/cmd
                self.sim.actuate(parts[1], cmd, self.ts, parts[2] if parts[1] == "valve" else None)
                logger.info("Actionneur %s : %s", "/".join(parts[1:-1]), cmd)
                self._publish_actuators()
                continue
            try:
                if name == "leak":
                    self.sim.inject_leak(str(cmd.get("section", "S3")), float(cmd.get("rate_m3h", 1.5)))
                    logger.warning("Fuite injectée : %s, %.2f m³/h", cmd.get("section", "S3"), cmd.get("rate_m3h", 1.5))
                elif name == "flush":
                    self.sim.set_flush(float(cmd.get("rate_m3h", 0.6)))
                    logger.warning("Rinçage des filtres : %.2f m³/h", self.sim.state.flush_m3h)
                elif name == "reset":
                    self.sim.reset()
                    logger.info("Réparation : fuites et rinçage arrêtés")
                elif name == "speed":
                    self.sim_s_per_h = max(0.05, float(cmd.get("sim_seconds_per_hour", self.sim_s_per_h)))
                    logger.info("Horloge : %.2f s réelles par heure simulée", self.sim_s_per_h)
                elif name == "replan":
                    self._adopt_plan(load_plan(self.plan_mode))
            except (ValueError, TypeError) as exc:
                logger.error("Commande %s invalide : %s", name, exc)

    # ------------------------------------------------------------- publication
    def _publish(self, rel_topic: str, payload: dict, retain: bool = False) -> None:
        topic = f"{self.base}/{rel_topic}"
        if self.connected.is_set():
            self.client.publish(topic, json.dumps(payload, default=_plain), qos=1 if "/flow" in rel_topic else 0,
                                retain=retain)
        elif self.http_fallback:
            self._post_fallback(topic, payload)

    def _post_fallback(self, topic: str, payload: dict) -> None:
        """Secours sans broker : POST {API}/internal/readings (endpoint de la Phase 6)."""
        import requests

        try:
            requests.post(f"{self.settings.api_url.rstrip('/')}/internal/readings",
                          json={"topic": topic, "payload": payload}, timeout=1)
        except Exception as exc:
            logger.debug("Repli HTTP impossible : %s", exc)

    def _publish_actuators(self) -> None:
        """Publie l'état des actionneurs (retenu) quand il change."""
        state = self.sim.actuator_state(self.ts)
        if state == getattr(self, "_last_actuators", None):
            return
        self._last_actuators = state
        common = {"ts": self.ts.isoformat(), "mode": state["mode"], "device": "simulateur"}
        self._publish("actuator/pump/state", {**common, "on": state["pump_on"],
                                              "dry_run_protection": state["dry_run_protection"]}, retain=True)
        for pid, is_open in state["valves"].items():
            self._publish(f"actuator/valve/{pid}/state", {**common, "open": is_open}, retain=True)
        self._publish("actuator/estop/state", {**common, "engaged": state["estop"]}, retain=True)

    def run(self) -> None:
        self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=30)
        self.client.loop_start()
        if not self.connected.wait(10) and not self.http_fallback:
            logger.error("MQTT injoignable sur %s:%s (lancer scripts/start-services.ps1)",
                         self.settings.mqtt_host, self.settings.mqtt_port)
        last_day = None
        published, errors = 0, 0
        try:
            while not self.stop.is_set():
                t0 = time.perf_counter()
                try:                                   # une erreur sur un pas ne doit JAMAIS arrêter la démo
                    self._apply_commands()
                    if self.plan.hour_index(self.ts) == 0 and self.ts.minute == 0 and self.ts != self.plan.start_dt:
                        self._next_plan()              # fin de l'horizon : plan suivant, sinon on boucle
                    if self.ts.date() != last_day:
                        last_day = self.ts.date()
                        logger.info("Vivant : jour simulé %s, %d messages publiés, %d erreur(s), plan « %s », "
                                    "MQTT %s", last_day, published, errors, self.plan.source,
                                    "connecté" if self.connected.is_set() else "DÉCONNECTÉ")
                        for rel, payload in self.sim.crop_info(self.ts):
                            self._publish(rel, payload, retain=True)
                    for rel, payload in self.sim.step(self.ts):
                        self._publish(rel, payload)
                        published += 1
                    self._publish_actuators()
                    self._publish("status/simulator", {"ts": datetime.now().isoformat(timespec="seconds"),
                                                       "sim_ts": self.ts.isoformat(), "alive": True,
                                                       "speed_s_per_h": self.sim_s_per_h}, retain=True)
                except Exception:
                    errors += 1
                    logger.exception("Erreur au pas %s (le simulateur continue)", self.ts)
                self.ts += timedelta(minutes=self.tick_min)
                period = self.sim_s_per_h * self.tick_min / 60
                self.stop.wait(max(0.0, period - (time.perf_counter() - t0)))
        finally:
            self._publish("status/simulator", {"ts": datetime.now().isoformat(timespec="seconds"), "alive": False},
                          retain=True)
            self.client.loop_stop()
            self.client.disconnect()

    def _adopt_plan(self, new: PlanSchedule) -> None:
        """Adopte un plan. S'il porte sur d'autres dates (ex. scénario canicule de juillet), la ferme
        simulée se place à ces dates, à la même heure de la journée : cultures, soleil et chaleur
        correspondent alors au plan suivi."""
        if new.start != self.plan.start or new.source != self.plan.source:
            logger.info("Nouveau plan « %s » (départ %s)", new.source, new.start)
        if new.start_dt.date() != self.plan.start_dt.date():
            self.ts = new.start_dt + timedelta(hours=self.ts.hour, minutes=self.ts.minute)
            logger.info("La ferme simulée passe au %s (dates du plan)", self.ts)
        if new.farm_file != self.plan.farm_file:
            self.sim.base_farm = load_farm(new.farm_file)
        self.plan = new
        self.sim.plan = new
        self.sim._farm_day = None                    # recalcule cultures et stades à la nouvelle date

    def _next_plan(self) -> None:
        """Fin de l'horizon : recharge le dernier plan (API ou optimiseur) ; à défaut, rejoue le même."""
        if self.plan_mode == "default":
            return
        try:
            self._adopt_plan(load_plan(self.plan_mode))
        except Exception as exc:
            logger.warning("Plan suivant indisponible (%s) : le plan actuel est rejoué", exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulateur de capteurs AquaMind (MQTT)")
    parser.add_argument("--plan", default="auto",
                        help="auto | api | live | default | scenario:<clé> (ex. scenario:tunisia_heatwave)")
    parser.add_argument("--start-hour", type=int, default=7, help="heure simulée de départ (défaut 7h)")
    parser.add_argument("--http-fallback", action="store_true", help="POST vers l'API si MQTT indisponible")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    pub = Publisher(args.plan, args.start_hour, args.http_fallback, args.seed)
    signal.signal(signal.SIGINT, lambda *_: pub.stop.set())
    logger.info("Départ %s, 1 pas = %d min simulées = %.2f s réelles", pub.ts,
                pub.tick_min, pub.sim_s_per_h * pub.tick_min / 60)
    pub.run()


if __name__ == "__main__":
    main()
