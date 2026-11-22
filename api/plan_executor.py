"""Exécuteur du plan d'arrosage pour un appareil réel (ESP32).

Avec le simulateur Python, c'est le simulateur qui « suit le plan » en mode automatique. Un ESP32, lui,
n'obéit qu'à des commandes : en mode automatique, cet exécuteur lit l'heure de la ferme (simulateur),
regarde ce que le plan prévoit à cette heure (vannes A/B/C, donc pompe) et envoie les commandes
manquantes avec la source « scheduler ». Elles passent par safety.check_command et le journal comme
toutes les autres ; une commande refusée n'est retentée qu'après RETRY_S (pas de rafale).

Inactif si : appareil ≠ ESP32 en ligne, mode manuel (un humain a pris la main), arrêt d'urgence,
heure de la ferme ou plan inconnus.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any, Callable

from aquamind_core.safety import ActuatorCommand

logger = logging.getLogger(__name__)
RETRY_S = 3.0
OPEN_M3H = 0.1


class PlanExecutor:
    def __init__(self, controller, get_plan: Callable[[], Any], get_sim_ts: Callable[[], str | None],
                 mono: Callable[[], float] = time.monotonic) -> None:
        self.ctl = controller
        self.get_plan = get_plan
        self.get_sim_ts = get_sim_ts
        self.mono = mono
        self.last_try: dict[str, float] = {}
        self._schedule_key: Any = None
        self._schedule = None

    def _plan_schedule(self):
        res = self.get_plan()
        if res is None:
            return None
        if self._schedule_key is not res:
            from simulator.plan import _from_result

            inputs = getattr(res, "inputs", None) or {}
            start = date.fromisoformat(str(inputs.get("start", date.today().isoformat())))
            self._schedule = _from_result(res, start, "plan courant", "farm.json", inputs.get("site", "tunisia"))
            self._schedule_key = res
        return self._schedule

    def desired(self) -> dict[str, Any] | None:
        """Ce que le plan veut à l'heure de la ferme : {"valves": {A: bool…}, "pump": bool}."""
        sched, sim_ts = self._plan_schedule(), self.get_sim_ts()
        if sched is None or not sim_ts:
            return None
        try:
            h = sched.hour_index(datetime.fromisoformat(sim_ts))
        except ValueError:
            return None
        valves = {v: float(sched.allocation.get(v, [0.0] * sched.horizon_h)[h]) > OPEN_M3H for v in ("A", "B", "C")}
        return {"valves": valves, "pump": any(valves.values()), "hour": h}

    def active(self) -> bool:
        st = self.ctl.state
        dev = self.ctl.device_status()
        return dev["kind"] == "esp32" and dev["online"] and st.mode == "auto" and not st.estop

    def _send(self, key: str, cmd: ActuatorCommand) -> None:
        now = self.mono()
        if now - self.last_try.get(key, -1e9) < RETRY_S:
            return
        self.last_try[key] = now
        self.ctl.command(cmd)

    def tick(self) -> None:
        if not self.active():
            return
        want = self.desired()
        if want is None:
            return
        st = self.ctl.state
        why = f"plan d'arrosage, heure {want['hour']}"

        def mk(target: str, action: str, valve: str | None = None) -> ActuatorCommand:
            return ActuatorCommand(source="scheduler", actor="plan", target=target, action=action, valve_id=valve,
                                   reason=why)

        # Ordre sûr : couper la pompe si plus rien à arroser → ouvrir ce qu'il faut → fermer le reste
        # (jamais la dernière vanne pompe en marche) → démarrer la pompe.
        if st.pump_on and not want["pump"]:
            self._send("pump", mk("pump", "off"))
            return
        for v, o in want["valves"].items():
            if o and not st.valves.get(v):
                self._send(f"valve{v}", mk("valve", "open", v))
        for v, o in want["valves"].items():
            if not o and st.valves.get(v) and (not st.pump_on or sum(st.valves.values()) > 1):
                self._send(f"valve{v}", mk("valve", "close", v))
        if want["pump"] and not st.pump_on and any(st.valves.values()):
            self._send("pump", mk("pump", "on"))
