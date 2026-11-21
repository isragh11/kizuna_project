"""État partagé de l'API : consumer embarqué, dernier plan, actionneurs, diffusion temps réel."""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

from aquamind_core.config import Settings, get_settings, load_farm
from aquamind_core.schemas import OptimizeResult, SensorReading
from api.actuators import ActuatorController
from api.hub import LiveHub
from stream.storage import MemoryBuffer

logger = logging.getLogger(__name__)


class NullBus:
    """Bus sans broker (tests, consumer désactivé) : enregistre ce qui aurait été publié."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any], bool]] = []

    def publish(self, rel_topic: str, body: dict[str, Any], qos: int = 1, retain: bool = False) -> bool:
        self.sent.append((rel_topic, body, retain))
        return False


class AppState:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.hub = LiveHub()
        self.consumer = None
        self.bus: Any = NullBus()
        self.memory = MemoryBuffer()
        self.alerts: deque[dict[str, Any]] = deque(maxlen=500)
        self.last_plan: OptimizeResult | None = None
        self.plan_meta: dict[str, Any] = {"mode": "auto", "source": None, "computed_at": None}
        self.plan_lock = threading.Lock()
        self.scheduler = None
        self.started_at = datetime.now(timezone.utc)
        self.actuators = ActuatorController(self, notify=self.hub.publish)

    # le contrôleur publie via self.publish → consumer (broker) ou NullBus
    def publish(self, rel_topic: str, body: dict[str, Any], qos: int = 1, retain: bool = False) -> bool:
        return self.bus.publish(rel_topic, body, qos=qos, retain=retain)

    # ------------------------------------------------------------------ consumer embarqué
    def start_consumer(self) -> None:
        from stream.consumer import Consumer

        c = Consumer(on_reading=self._on_reading, on_event=self._on_event,
                     on_actuator=self.actuators.on_device_state, post_to_api=False, client_id=f"aquamind-api-{os.getpid()}")
        c.start()
        self.consumer, self.bus, self.memory = c, c, c.memory

    def stop_consumer(self) -> None:
        if self.consumer is not None:
            self.consumer.close()

    def _on_reading(self, r: SensorReading) -> None:
        if r.kind == "tank" and "level_m3" in r.values:
            self.actuators.on_tank_level(r.values["level_m3"])
        self.hub.publish({"type": "reading", "kind": r.kind, "source_id": r.source_id,
                          "ts": r.ts.isoformat(), "values": r.values})

    def _on_event(self, body: dict[str, Any]) -> None:
        if body.get("type") == "alert":
            self.alerts.appendleft(body)
        self.hub.publish(body)

    def add_external_alert(self, body: dict[str, Any]) -> bool:
        """POST /internal/alerts (consumer lancé à part) : ignoré si l'alerte est déjà connue."""
        aid = body.get("alert_id")
        if aid and any(a.get("alert_id") == aid for a in self.alerts):
            return False
        self.alerts.appendleft({"type": "alert", **body})
        self.hub.publish({"type": "alert", **body})
        return True

    # ------------------------------------------------------------------ plan
    def set_plan(self, result: OptimizeResult, source: str, mode: str) -> None:
        with self.plan_lock:
            self.last_plan = result
            self.plan_meta = {"mode": mode, "source": source,
                              "computed_at": datetime.now(timezone.utc).isoformat()}
        self.hub.publish({"type": "plan", "meta": self.plan_meta, "kpis": result.kpis,
                          "limiting_factor": result.limiting_factor, "explanations": result.explanations[:4]})
        self.publish("cmd/replan", {"source": source})       # le simulateur recharge /optimize/last

    def refresh_auto_plan(self, force: bool = False) -> bool:
        """Prévision Open-Meteo + optimisation pour demain (APScheduler, toutes les heures)."""
        if not force and self.plan_meta.get("mode") == "manual":
            logger.info("Plan manuel conservé (le rafraîchissement automatique ne l'écrase pas)")
            return False
        from aquamind_core.forecast import live_inputs
        from aquamind_core.optimizer import optimize
        from aquamind_core.schemas import OptimizeRequest

        try:
            farm = load_farm()
            inputs = live_inputs("tunisia", days=2)
            req = OptimizeRequest(farm=farm, site="tunisia", date=inputs.start,
                                  water_quota_m3=farm.water_quota_m3_day, et0_mm=inputs.et0_mm,
                                  rain_mm=inputs.rain_mm, tmean_c=inputs.tmean_c, tmax_c=inputs.tmax_c,
                                  pv_day_factors=inputs.pv_day_factors)
            self.set_plan(optimize(req, inputs), "prévision Open-Meteo (automatique)", "auto")
            logger.info("Plan automatique recalculé pour le %s", inputs.start)
            return True
        except Exception as exc:
            logger.warning("Plan automatique impossible : %s", exc)
            return False
