"""Détection de fuites en direct, sans MQTT (testable) : un appel par pas de débits S1–S5.

Mode d'alerte lu dans river_config.json (`recommended_mode`, notebook 04 sur 5 graines) :
  rule_adaptive        règle de bilan de masse adaptative (défaut recommandé)
  hybrid_and_adaptive  règle adaptative ET River
  rule                 règle fixe |déséquilibre| > seuil
  river                River seul
River tourne toujours : en mode rule_adaptive il sert d'indice « confirmé par l'IA » et ne bloque
jamais une alerte. IsolationForest et CUSUM (BattLeDIM) ne sont pas utilisés en direct.
"""

from __future__ import annotations

import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aquamind_core.config import hourly_electricity_price, load_economics
from aquamind_core.energy import pump_kwh_per_m3
from aquamind_core.leaks import (
    PUMPING_PLAN_M3H,
    AdaptiveRule,
    RiverIndex,
    estimate_losses,
    flow_features,
    load_river_config,
    locate_section,
)
from aquamind_core.schemas import Alert, Farm

logger = logging.getLogger(__name__)

SECTION_PLOT = {"S1": "conduite principale", "S2": "parcelle A", "S3": "parcelle B", "S4": "parcelle C",
                "S5": "retour cuve"}
DEFAULT_PUMPING_HOURS = 10.0
CONFIRM_WINDOW = 3


@dataclass
class Event:
    """Évènement produit par le détecteur : alerte, fin d'alerte, nouveau normal."""

    kind: str                      # "alert" | "alert_end" | "new_normal"
    payload: dict[str, Any] = field(default_factory=dict)
    alert: Alert | None = None


class LeakMonitor:
    """Règle adaptative + indice River + estimation des pertes, un message de débits à la fois."""

    def __init__(self, farm: Farm, mode: str | None = None) -> None:
        cfg = load_river_config()
        self.mode = mode or cfg.get("recommended_mode", "rule_adaptive")
        self.rule = AdaptiveRule.from_config()
        self.fixed_threshold = float(cfg["rule_baseline"]["abs_threshold_m3h"])
        self.n_consec = int(cfg["consecutive_ticks"])
        self.river = RiverIndex()
        self.tick_h = float(cfg.get("tick_minutes", 10)) / 60
        self.kwh_per_m3 = pump_kwh_per_m3(farm.well_depth_m, farm.pump_efficiency)
        self.water_value = float(load_economics()["water_price_tnd_per_m3"]["value"])
        self.prices = hourly_electricity_price()
        self.pumping_hist: deque[bool] = deque(maxlen=int(round(24 / self.tick_h)))
        self.river_recent: deque[bool] = deque(maxlen=CONFIRM_WINDOW)
        self.fixed_run = 0
        self.alarm = False
        self.current: Alert | None = None
        self.ticks = 0

    # ------------------------------------------------------------------ préchauffage
    def warmup(self, ticks: list[tuple[dict[str, float], dict[str, float]]]) -> None:
        """Rejoue un flux normal : River apprend sa fenêtre, la règle construit sa référence."""
        for flows, plans in ticks:
            feats = flow_features(flows, plans)
            pumping = plans["S1"] > PUMPING_PLAN_M3H
            self.river.update(feats)
            self.rule.update(feats["imbalance"], pumping)
            self.pumping_hist.append(pumping)
        self.rule.above_run, self.rule.pending, self.river.run = 0, [], 0
        logger.info("Préchauffage : %d pas (River fenêtre %d)", len(ticks), self.river.model.window_size)

    def new_normal(self) -> Event:
        """Commande cmd/new_normal : l'état actuel devient la référence ; l'alerte en cours se termine."""
        self.rule.new_normal()
        ev = Event("new_normal", {"source": "commande", "alert_id": self.current.alert_id if self.current else None})
        self.alarm, self.current = False, None
        return ev

    # ------------------------------------------------------------------ un pas
    def process(self, ts: datetime, flows: dict[str, float], plans: dict[str, float]) -> list[Event]:
        self.ticks += 1
        feats = flow_features(flows, plans)
        pumping = plans["S1"] > PUMPING_PLAN_M3H
        self.pumping_hist.append(pumping)
        step = self.rule.update(feats["imbalance"], pumping)
        river_alarm = self.river.update(feats)
        self.river_recent.append(river_alarm)
        self.fixed_run = self.fixed_run + 1 if abs(feats["imbalance"]) > self.fixed_threshold else 0
        fixed_alarm = self.fixed_run >= self.n_consec

        alarm = {"rule_adaptive": step.alarm, "hybrid_and_adaptive": step.alarm and river_alarm,
                 "rule": fixed_alarm, "river": river_alarm,
                 "hybrid_and": fixed_alarm and river_alarm}.get(self.mode, step.alarm)
        events: list[Event] = []
        if step.accepted_new_normal:
            events.append(Event("new_normal", {"source": "automatique (écart persistant 8 h de pompage)",
                                               "baseline_m3h": round(step.baseline, 3)}))
            logger.warning("Nouveau normal accepté automatiquement (écart persistant)")
        if alarm and not self.alarm:
            self.current = self._make_alert(ts, feats, step.deviation)
            events.append(Event("alert", alert=self.current))
        elif alarm and self.current and not self.current.confirmed_by_ai and river_alarm:
            self.current = self.current.model_copy(update={"confirmed_by_ai": True,
                                                           "river_score": round(self.river.last_score, 3)})
            events.append(Event("alert_update", {"alert_id": self.current.alert_id, "confirmed_by_ai": True}))
        elif not alarm and self.alarm and self.current:
            events.append(Event("alert_end", {"alert_id": self.current.alert_id, "ts": ts.isoformat()}))
            self.current = None
        self.alarm = alarm
        return events

    def pumping_hours_per_day(self) -> float:
        if len(self.pumping_hist) < 36:
            return DEFAULT_PUMPING_HOURS
        return 24 * sum(self.pumping_hist) / len(self.pumping_hist)

    def _make_alert(self, ts: datetime, feats: dict[str, float], deviation: float) -> Alert:
        section = locate_section(feats)
        loss_m3h = abs(deviation)
        hours = self.pumping_hours_per_day()
        price = self.prices[ts.hour]
        loss = estimate_losses(loss_m3h, hours, self.kwh_per_m3, self.water_value, price)
        confirmed = any(self.river_recent)
        ai = ("Confirmé par l'IA (River)." if confirmed
              else "Indice IA (River) : pas encore confirmé — la règle physique suffit à alerter.")
        where = (f"{section} ({SECTION_PLOT[section]})" if section != "S1" else
                 "S1 (conduite principale, ou prélèvement non mesuré : rinçage, nouvelle vanne ?)")
        msg = (f"Fuite probable — {where} : bilan de masse rompu de "
               f"{loss_m3h:.2f} m³/h par rapport à la référence. Pertes estimées si rien n'est fait : "
               f"{loss['m3_day']:.1f} m³/j ({hours:.0f} h de pompage/j), {loss['kwh_day']:.1f} kWh/j, "
               f"{loss['tnd_day']:.2f} TND/j (eau {self.water_value:.2f} TND/m³ + électricité "
               f"{price:.3f} TND/kWh, hypothèses). {ai} Si ce changement est voulu, appuyer sur "
               "« nouveau normal ».")
        return Alert(ts=ts, section=section, score=round(loss_m3h, 3), detector="rule_adaptive",
                     est_loss_m3_day=loss["m3_day"], est_loss_kwh_day=loss["kwh_day"],
                     est_loss_tnd_day=loss["tnd_day"], message=msg, alert_id=uuid.uuid4().hex[:12],
                     est_loss_m3h=round(loss_m3h, 3), confirmed_by_ai=confirmed,
                     river_score=round(self.river.last_score, 3))
