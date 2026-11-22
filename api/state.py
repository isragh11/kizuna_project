"""État partagé de l'API : consumer embarqué, dernier plan, actionneurs, diffusion temps réel."""

from __future__ import annotations

import logging
import os
import threading
import time
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
        self.plot_info: dict[str, dict[str, Any]] = {}          # cultures du jour de la ferme simulée
        self.last_plan: OptimizeResult | None = None
        self.plan_meta: dict[str, Any] = {"mode": "auto", "source": None, "computed_at": None}
        self.plan_lock = threading.Lock()
        self.scheduler = None
        self.started_at = datetime.now(timezone.utc)
        self.actuators = ActuatorController(self, notify=self.hub.publish,
                                            offline_after_s=self.settings.device_offline_s)
        from api.plan_executor import PlanExecutor

        self.executor = PlanExecutor(self.actuators, get_plan=lambda: self.last_plan,
                                     get_sim_ts=lambda: (self.consumer.sim_status.get("sim_ts")
                                                         if self.consumer else None))
        self._loop_stop = threading.Event()

    # ------------------------------------------------------------------ boucle de surveillance (1 s)
    def start_loops(self) -> None:
        """Chaque seconde : commandes sans réponse, appareil muet, plan → commandes (ESP32) ;
        toutes les api_heartbeat_s : « status/api » pour l'appareil (sans lui 30 s → coupure locale)."""
        def run() -> None:
            last_hb = 0.0
            while not self._loop_stop.wait(1.0):
                try:
                    self.actuators.tick()
                    self.executor.tick()
                    now = time.monotonic()
                    if now - last_hb >= self.settings.api_heartbeat_s:
                        last_hb = now
                        self.publish("status/api", {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                                    "alive": True}, qos=0)
                except Exception:
                    logger.exception("Boucle de surveillance : erreur (elle continue)")
        threading.Thread(target=run, name="surveillance", daemon=True).start()

    def stop_loops(self) -> None:
        self._loop_stop.set()

    def _on_device(self, rel: str, payload: dict[str, Any]) -> None:
        if rel == "actuator/request":
            self.actuators.on_request(payload)
        elif rel == "device/heartbeat":
            self.actuators.on_heartbeat(payload)
        elif rel == "device/status":
            self.actuators.on_device_status(payload)
        elif rel == "device/event":
            self.hub.publish({"type": "device_event", **{k: v for k, v in payload.items() if k != "type"},
                              "event": payload.get("type")})

    # le contrôleur publie via self.publish → consumer (broker) ou NullBus
    def publish(self, rel_topic: str, body: dict[str, Any], qos: int = 1, retain: bool = False) -> bool:
        return self.bus.publish(rel_topic, body, qos=qos, retain=retain)

    # ------------------------------------------------------------------ consumer embarqué
    def start_consumer(self) -> None:
        from stream.consumer import Consumer

        c = Consumer(on_reading=self._on_reading, on_event=self._on_event,
                     on_actuator=self.actuators.on_device_state, post_to_api=False,
                     client_id=f"aquamind-api-{os.getpid()}", on_device=self._on_device)
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
        if body.get("type") == "plot_info":
            plot_id = str(body.get("topic", "")).split("/")[1] if "/" in str(body.get("topic", "")) else None
            if plot_id:
                self.plot_info[plot_id] = {k: body.get(k) for k in ("crop", "stage", "note", "kc", "method", "ts")}
        if body.get("type") == "alert":
            self.alerts.appendleft(body)
        elif body.get("type") == "alert_update":
            for stored in self.alerts:
                if stored.get("alert_id") == body.get("alert_id"):
                    stored.update({k: v for k, v in body.items() if k not in ("type", "alert_id")})
        self.hub.publish(body)

    def alerts_with_status(self, limit: int = 50) -> list[dict[str, Any]]:
        """Alertes récentes avec « active » : celle que le détecteur considère comme ouverte."""
        current = self.consumer.monitor.current.alert_id if self.consumer and self.consumer.monitor.current else None
        return [{**a, "active": a.get("alert_id") == current and current is not None}
                for a in list(self.alerts)[:limit]]

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
