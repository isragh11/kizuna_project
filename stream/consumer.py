"""Consumer MQTT : valide, stocke (InfluxDB ou SQLite) et détecte les fuites en direct.

    python -m stream.consumer

- S'abonne à {base}/#, valide chaque message avec SensorReading, l'écrit dans le stockage.
- Regroupe les 5 débits S1–S5 d'un même pas, puis LeakMonitor : règle adaptative (mode lu dans
  river_config.json) + indice River préchauffé au démarrage.
- Alerte → {base}/alerts (MQTT, JSON) + stockage + POST {API_URL}/internal/alerts si l'API tourne.
- Commande {base}/cmd/new_normal : « nouveau normal » (la situation actuelle devient la référence).
- Statut retenu : {base}/status/consumer (ready / offline).

Choix d'architecture : processus séparé de l'API. Il redémarre seul sans couper l'API, se teste
isolément, et l'API (Phase 6) lira le stockage et le topic des alertes.
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from datetime import datetime
from typing import Any

import paho.mqtt.client as mqtt
from pydantic import ValidationError

from aquamind_core.config import get_settings, load_farm
from aquamind_core.leaks import SECTIONS
from aquamind_core.schemas import SensorReading
from simulator.physics import synthetic_ticks
from simulator.plan import default_plan
from stream.detector import Event, LeakMonitor
from stream.storage import MemoryBuffer, make_storage

logger = logging.getLogger("consumer")

KIND_OF = {"soil": "soil", "flow": "flow", "power": "pv", "level": "tank"}
API_RETRY_S = 30.0


def parse_message(base: str, topic: str, payload: dict[str, Any]) -> SensorReading | None:
    """Topic + charge utile → SensorReading (None si ce n'est pas une mesure)."""
    if not topic.startswith(base + "/"):
        return None
    parts = topic[len(base) + 1:].split("/")
    if len(parts) == 3 and parts[0] in ("plot", "pipe") and parts[2] in KIND_OF:
        kind, source = KIND_OF[parts[2]], parts[1]
    elif len(parts) == 2 and parts[0] in ("pv", "tank") and parts[1] in KIND_OF:
        kind, source = KIND_OF[parts[1]], parts[0]
    else:
        return None
    values = {k: float(v) for k, v in payload.items()
              if k != "ts" and isinstance(v, (int, float)) and not isinstance(v, bool)}
    return SensorReading(ts=payload["ts"], kind=kind, source_id=source, values=values)


class Consumer:
    def __init__(self) -> None:
        self.s = get_settings()
        self.base = self.s.mqtt_base_topic
        self.farm = load_farm()
        self.storage = make_storage(self.s)
        self.memory = MemoryBuffer()
        self.monitor = LeakMonitor(self.farm)
        self.pending: dict[str, dict[str, tuple[float, float]]] = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.stats = {"messages": 0, "invalid": 0, "ticks": 0, "alerts": 0}
        self.api_down_until = 0.0
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="aquamind-consumer")
        self.client.will_set(f"{self.base}/status/consumer", json.dumps({"status": "offline"}), qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    # ------------------------------------------------------------------ démarrage
    def warmup(self) -> None:
        """Préchauffe River et la règle sur 2 fenêtres de flux normal synthétique (aucune alerte)."""
        plan = default_plan(self.farm, datetime.now().date())
        n = 2 * self.monitor.river.model.window_size
        start = datetime.combine(datetime.now().date(), datetime.min.time())
        t0 = time.perf_counter()
        self.monitor.warmup(synthetic_ticks(self.farm, plan, n, start))
        logger.info("River préchauffé en %.1f s (mode d'alerte : %s)", time.perf_counter() - t0, self.monitor.mode)

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.error("Connexion MQTT refusée : %s", reason_code)
            return
        client.subscribe(f"{self.base}/#", qos=1)
        client.publish(f"{self.base}/status/consumer", json.dumps({
            "status": "ready", "storage": self.storage.name, "mode": self.monitor.mode,
            "ts": datetime.now().isoformat()}), qos=1, retain=True)
        logger.info("Abonné à %s/# — stockage %s, mode %s", self.base, self.storage.name, self.monitor.mode)

    # ------------------------------------------------------------------ messages
    def _on_message(self, client, userdata, msg) -> None:
        try:
            payload = json.loads(msg.payload or b"{}")
        except ValueError:
            self.stats["invalid"] += 1
            return
        rel = msg.topic[len(self.base) + 1:] if msg.topic.startswith(self.base + "/") else ""
        if rel == "cmd/new_normal":
            with self.lock:
                self._emit(self.monitor.new_normal())
            logger.warning("Commande « nouveau normal » reçue : référence recalée")
            return
        if not isinstance(payload, dict) or rel.startswith(("cmd/", "alerts", "status/")) or rel.endswith("/info"):
            return
        try:
            reading = parse_message(self.base, msg.topic, payload)
        except (ValidationError, KeyError, TypeError, ValueError) as exc:
            self.stats["invalid"] += 1
            logger.debug("Message invalide sur %s : %s", msg.topic, exc)
            return
        if reading is None:
            return
        self.stats["messages"] += 1
        self.memory.readings.append(reading)
        try:
            self.storage.write_reading(reading)
        except Exception as exc:
            logger.error("Écriture du stockage impossible : %s", exc)
        if reading.kind == "flow":
            self._collect_flow(reading)

    def _collect_flow(self, r: SensorReading) -> None:
        key = r.ts.isoformat()
        with self.lock:
            tick = self.pending.setdefault(key, {})
            tick[r.source_id] = (r.values.get("flow_m3h", 0.0), r.values.get("plan_m3h", 0.0))
            if len(tick) < len(SECTIONS):
                for old in [k for k in self.pending if k < key]:   # pas incomplet plus ancien : abandonné
                    logger.debug("Pas incomplet abandonné : %s", old)
                    del self.pending[old]
                return
            del self.pending[key]
            flows = {s: v[0] for s, v in tick.items()}
            plans = {s: v[1] for s, v in tick.items()}
            self.stats["ticks"] += 1
            for ev in self.monitor.process(r.ts, flows, plans):
                self._emit(ev)

    # ------------------------------------------------------------------ alertes
    def _emit(self, ev: Event) -> None:
        if ev.alert is not None:
            a = ev.alert
            body = {"type": "alert", **json.loads(a.model_dump_json())}
            self.stats["alerts"] += 1
            self.memory.alerts.append(a)
            try:
                self.storage.write_alert(a)
            except Exception as exc:
                logger.error("Alerte non stockée : %s", exc)
            logger.warning("ALERTE %s : %s", a.alert_id, a.message)
            self._post_api(body)
        else:
            body = {"type": ev.kind, **ev.payload}
            logger.info("%s : %s", ev.kind, ev.payload)
        self.client.publish(f"{self.base}/alerts", json.dumps(body, ensure_ascii=False), qos=1)

    def _post_api(self, body: dict[str, Any]) -> None:
        """POST {API}/internal/alerts (Phase 6) ; en cas d'échec, on réessaie dans 30 s."""
        if time.monotonic() < self.api_down_until:
            return
        import requests

        try:
            requests.post(f"{self.s.api_url.rstrip('/')}/internal/alerts", json=body, timeout=1)
        except Exception:
            self.api_down_until = time.monotonic() + API_RETRY_S

    # ------------------------------------------------------------------ boucle
    def run(self) -> None:
        self.warmup()
        self.client.connect_async(self.s.mqtt_host, self.s.mqtt_port, keepalive=30)
        self.client.loop_start()
        last = time.monotonic()
        try:
            while not self.stop.wait(1.0):
                if time.monotonic() - last >= 60:
                    last = time.monotonic()
                    logger.info("Bilan : %s, heures de pompage estimées %.1f h/j", self.stats,
                                self.monitor.pumping_hours_per_day())
        finally:
            self.client.publish(f"{self.base}/status/consumer", json.dumps({"status": "offline"}),
                                qos=1, retain=True).wait_for_publish(2)
            self.client.loop_stop()
            self.client.disconnect()
            self.storage.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    c = Consumer()
    signal.signal(signal.SIGINT, lambda *_: c.stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: c.stop.set())
    c.run()


if __name__ == "__main__":
    main()
