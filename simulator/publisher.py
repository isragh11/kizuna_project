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
"""

from __future__ import annotations

import argparse
import json
import logging
import queue
import signal
import threading
import time
from datetime import timedelta

import paho.mqtt.client as mqtt

from aquamind_core.config import get_settings, load_farm
from aquamind_core.leaks import load_river_config
from simulator.physics import FarmSimulator
from simulator.plan import PlanSchedule, load_plan

logger = logging.getLogger("simulator")


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
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"aquamind-sim-{seed}")
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
        client.subscribe(f"{self.base}/cmd/#", qos=1)
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
                    self.plan = load_plan(self.plan_mode)
                    self.sim.plan = self.plan
            except (ValueError, TypeError) as exc:
                logger.error("Commande %s invalide : %s", name, exc)

    # ------------------------------------------------------------- publication
    def _publish(self, rel_topic: str, payload: dict, retain: bool = False) -> None:
        topic = f"{self.base}/{rel_topic}"
        if self.connected.is_set():
            self.client.publish(topic, json.dumps(payload), qos=1 if "/flow" in rel_topic else 0, retain=retain)
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

    def run(self) -> None:
        self.client.connect_async(self.settings.mqtt_host, self.settings.mqtt_port, keepalive=30)
        self.client.loop_start()
        if not self.connected.wait(10) and not self.http_fallback:
            logger.error("MQTT injoignable sur %s:%s (lancer scripts/start-services.ps1)",
                         self.settings.mqtt_host, self.settings.mqtt_port)
        last_day = None
        try:
            while not self.stop.is_set():
                t0 = time.perf_counter()
                self._apply_commands()
                if self.ts.date() != last_day:
                    last_day = self.ts.date()
                    for rel, payload in self.sim.crop_info(self.ts):
                        self._publish(rel, payload, retain=True)
                for rel, payload in self.sim.step(self.ts):
                    self._publish(rel, payload)
                self.ts += timedelta(minutes=self.tick_min)
                period = self.sim_s_per_h * self.tick_min / 60
                self.stop.wait(max(0.0, period - (time.perf_counter() - t0)))
        finally:
            self.client.loop_stop()
            self.client.disconnect()


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
