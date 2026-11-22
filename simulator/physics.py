"""Physique de la ferme simulée, sans MQTT (testable et réutilisable pour préchauffer River).

Topologie (comme le notebook 04) : S1 = conduite principale en sortie de cuve ; S2, S3, S4 = parcelles
A, B, C ; S5 = retour vers la cuve (8 % du débit livré). Hors fuite : S1 = S2 + S3 + S4 + S5.
Le puits remplit la cuve (débit de pompage du plan). Une fuite sur le tronçon i (entre S1 et le compteur
du tronçon) augmente S1 du débit de fuite et réduit la mesure du tronçon de 25 % de ce débit, comme dans
le notebook 04 : le bilan de masse est rompu. Elle ne coule que si la branche est en eau (vanne ouverte). Le rinçage des filtres (dérive légitime) prélève sur S1
sans passer par S2–S4. Bruit, rampes et pics isolés : mêmes valeurs que le notebook 04.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from aquamind_core.agronomy import plot_kc
from aquamind_core.crop_calendar import apply_calendar
from aquamind_core.energy import monthly_pv_profile
from aquamind_core.schemas import Farm
from simulator.plan import PlanSchedule

SECTIONS = ["S1", "S2", "S3", "S4", "S5"]
PLOT_SECTION = {"A": "S2", "B": "S3", "C": "S4"}
RETURN_FRAC = 0.08
NOISE_REL, NOISE_ABS = 0.02, 0.05
SPIKE_PROB, SPIKE_SIZE = 0.002, 1.5
LEAK_SECTION_SHARE = 0.25
ROOT_ZONE_MM = 300.0
PUMPING_PLAN_M3H = 0.5
DRY_TANK_M3 = 0.3


@dataclass
class FarmState:
    """État dynamique : cuve, humidité des sols, fuites et rinçage en cours."""

    tank_m3: float
    soil: dict[str, float]
    leaks: dict[str, float] = field(default_factory=dict)   # tronçon → m³/h
    flush_m3h: float = 0.0
    prev_plan: dict[str, float] = field(default_factory=dict)


class FarmSimulator:
    """Produit, à chaque pas, les messages capteurs (topic relatif, charge utile)."""

    def __init__(self, farm: Farm, plan: PlanSchedule, tick_minutes: int = 10, seed: int = 42,
                 soil_init: float = 0.20, spikes: bool = True) -> None:
        self.base_farm = farm
        self.plan = plan
        self.tick_minutes = tick_minutes
        self.rng = np.random.default_rng(seed)
        self.spikes = spikes
        self.state = FarmState(tank_m3=plan.tank_initial_m3,
                               soil={p.id: soil_init for p in farm.plots})
        self._farm_day: tuple[str, Farm] | None = None
        # Actionneurs : None = pilotage automatique (plan) ; sinon commandes manuelles
        self.manual: dict[str, Any] | None = None
        self.estop = False
        # Mode « appareil externe » (ESP32 Wokwi) : les débits viennent de l'appareil, pas du modèle ;
        # le simulateur ne publie ni débits ni sol des parcelles tenues par l'appareil.
        self.external_flows: dict[str, tuple[float, float]] | None = None   # tronçon → (mesuré, plan)
        self.external_plots: set[str] = set()

    # ------------------------------------------------------------- commandes
    def inject_leak(self, section: str, rate_m3h: float) -> None:
        if section not in SECTIONS[1:4] and section != "S1":
            raise ValueError(f"tronçon inconnu : {section}")
        self.state.leaks[section] = max(0.0, float(rate_m3h))

    def set_flush(self, rate_m3h: float) -> None:
        self.state.flush_m3h = max(0.0, float(rate_m3h))

    def reset(self) -> None:
        self.state.leaks.clear()
        self.state.flush_m3h = 0.0

    # ------------------------------------------------------------- actionneurs (rôle de l'appareil)
    def _ensure_manual(self, ts: datetime) -> dict[str, Any]:
        if self.manual is None:
            plan = self._auto_flows(ts)
            self.manual = {"pump": plan["S1"] > PUMPING_PLAN_M3H,
                           "valves": {pid: plan[s] > 0.1 for pid, s in PLOT_SECTION.items()}}
        return self.manual

    def actuate(self, kind: str, body: dict[str, Any], ts: datetime, valve_id: str | None = None) -> tuple[bool, str]:
        """Applique une commande d'actionneur (comme le ferait l'ESP32). L'arrêt d'urgence prime.

        Renvoie (appliquée, raison) : la raison d'un refus est renvoyée au dashboard dans l'état."""
        if kind == "estop":
            self.estop = bool(body.get("engaged", True))
            if self.estop:
                self.manual = {"pump": False, "valves": {pid: False for pid in PLOT_SECTION}}
            return True, ""
        if kind == "mode":
            if self.estop:
                return False, "arrêt d'urgence actif"
            if body.get("mode") == "auto":
                self.manual = None
            return True, ""
        m = self._ensure_manual(ts)
        if kind == "pump":
            on = bool(body.get("on", False))
            if on and self.estop:                      # sécurité locale : pas de marche sous arrêt d'urgence
                return False, "arrêt d'urgence actif"
            m["pump"] = on
            return True, ""
        if kind == "valve" and valve_id in PLOT_SECTION:
            is_open = bool(body.get("open", False))
            if is_open and self.estop:
                return False, "arrêt d'urgence actif"
            m["valves"][valve_id] = is_open
            return True, ""
        return False, f"actionneur inconnu : {kind}"

    def actuator_state(self, ts: datetime) -> dict[str, Any]:
        """État effectif des actionneurs (publié sur .../state)."""
        if self.manual is not None:
            pump, valves = self.manual["pump"], dict(self.manual["valves"])
        else:
            plan = self._auto_flows(ts)
            pump = plan["S1"] > PUMPING_PLAN_M3H
            valves = {pid: plan[s] > 0.1 for pid, s in PLOT_SECTION.items()}
        return {"pump_on": bool(pump) and not self.estop, "valves": valves, "estop": self.estop,
                "mode": "manual" if self.manual is not None else "auto",
                "dry_run_protection": bool(self.state.tank_m3 < DRY_TANK_M3)}

    # ------------------------------------------------------------- pas de simulation
    def farm_on(self, ts: datetime) -> Farm:
        """Ferme du jour : culture et stade selon crop_calendar.json (recalculé une fois par jour)."""
        key = ts.date().isoformat()
        if self._farm_day is None or self._farm_day[0] != key:
            self._farm_day = (key, apply_calendar(self.base_farm, ts.date()))
        return self._farm_day[1]

    def plan_flows(self, ts: datetime) -> dict[str, float]:
        """Consignes (m³/h) de chaque tronçon : plan de l'optimiseur, ou commandes manuelles."""
        if self.estop:
            return {s: 0.0 for s in SECTIONS}
        if self.manual is None:
            return self._auto_flows(ts)
        plan = {s: 0.0 for s in SECTIONS}
        open_ids = [pid for pid, o in self.manual["valves"].items() if o]
        if self.manual["pump"] and open_ids:
            per = self.base_farm.pump_max_m3h * (1 - RETURN_FRAC) / len(open_ids)
            for pid in open_ids:
                plan[PLOT_SECTION[pid]] = per
        delivered = plan["S2"] + plan["S3"] + plan["S4"]
        plan["S5"] = RETURN_FRAC / (1 - RETURN_FRAC) * delivered
        plan["S1"] = delivered + plan["S5"]
        return plan

    def _auto_flows(self, ts: datetime) -> dict[str, float]:
        """Débits du plan (m³/h) pour chaque tronçon à l'heure ts."""
        h = self.plan.hour_index(ts)
        plan = {s: 0.0 for s in SECTIONS}
        for plot_id, section in PLOT_SECTION.items():
            plan[section] = float(self.plan.allocation.get(plot_id, [0.0] * self.plan.horizon_h)[h])
        delivered = plan["S2"] + plan["S3"] + plan["S4"]
        plan["S5"] = RETURN_FRAC / (1 - RETURN_FRAC) * delivered
        plan["S1"] = delivered + plan["S5"]
        return plan

    def step(self, ts: datetime) -> list[tuple[str, dict[str, Any]]]:
        """Un pas de `tick_minutes` : renvoie les messages [(topic relatif, payload)]."""
        st, rng = self.state, self.rng
        dt_h = self.tick_minutes / 60
        farm = self.farm_on(ts)
        if self.external_flows is not None:
            return self._step_external(ts, farm, dt_h)
        plan = self.plan_flows(ts)
        pumping_line = plan["S1"] > PUMPING_PLAN_M3H

        meas: dict[str, float] = {}
        for s in SECTIONS:
            p = plan[s]
            prev = st.prev_plan.get(s, p)
            actual = 0.5 * (p + prev) if p != prev else p     # la conduite met un pas à se stabiliser
            meas[s] = actual * (1 + NOISE_REL * rng.standard_normal()) + NOISE_ABS * rng.standard_normal()
        st.prev_plan = plan
        total_leak = 0.0
        if pumping_line:
            for section, rate in st.leaks.items():
                if section != "S1" and plan[section] <= 0.1:
                    continue                    # vanne de la branche fermée : la fuite ne coule pas
                meas["S1"] += rate
                if section != "S1":
                    meas[section] -= LEAK_SECTION_SHARE * rate
                total_leak += rate
            meas["S1"] += st.flush_m3h
        if self.spikes and rng.random() < SPIKE_PROB:
            meas[rng.choice(SECTIONS)] += SPIKE_SIZE * rng.choice([-1.0, 1.0])
        meas = {s: max(0.0, v) for s, v in meas.items()}
        if st.tank_m3 < DRY_TANK_M3:                     # cuve vide : la pompe ne débite plus rien
            meas = {s: max(0.0, NOISE_ABS * rng.standard_normal()) for s in SECTIONS}
        pressure = (2.5 - 0.25 * total_leak + 0.03 * rng.standard_normal()) if pumping_line else 0.2

        # cuve : entrée = pompage du puits ; sortie = S1 ; retour = S5
        inflow = 0.0 if self.estop else float(self.plan.pump_m3[self.plan.hour_index(ts)])
        st.tank_m3 = float(min(self.base_farm.tank_capacity_m3,
                               max(0.0, st.tank_m3 + (inflow - meas["S1"] + meas["S5"]) * dt_h)))

        # solaire : profil PVGIS du mois, interpolé dans l'heure, × ensoleillement du jour du plan
        prof = monthly_pv_profile(self.plan.site).loc[ts.month].tolist()
        frac = ts.hour + ts.minute / 60
        h0 = int(frac) % 24
        v = prof[h0] + (prof[(h0 + 1) % 24] - prof[h0]) * (frac - int(frac))
        day_factor = self.plan.pv_day_factors[min(self.plan.day_index(ts), len(self.plan.pv_day_factors) - 1)]
        pv_kw = max(0.0, v * self.base_farm.pv_peak_kw * day_factor * (1 + 0.03 * rng.standard_normal()))

        # sols : + irrigation efficace, − ETc (forme journalière en cloche entre 6h et 19h)
        et0_day = self.plan.et0_mm[min(self.plan.day_index(ts), len(self.plan.et0_mm) - 1)]
        shape = max(0.0, math.sin(math.pi * (frac - 6) / 13)) if 6 <= frac <= 19 else 0.0
        etc_frac = shape * (math.pi / 26) * dt_h    # ∫ sin = 26/π h sur la journée → somme ≈ 1
        msgs: list[tuple[str, dict[str, Any]]] = []
        iso = ts.isoformat()
        for plot in farm.plots:
            section = PLOT_SECTION.get(plot.id)
            irrig_mm = (meas[section] * dt_h / (plot.area_ha * 10) * plot.irrigation_efficiency
                        if section else 0.0)
            etc_mm = et0_day * plot_kc(plot) * etc_frac
            st.soil[plot.id] = float(np.clip(st.soil[plot.id] + (irrig_mm - etc_mm) / ROOT_ZONE_MM, 0.05, 0.45))
            msgs.append((f"plot/{plot.id}/soil", {
                "ts": iso, "moisture_m3m3": round(st.soil[plot.id] + 0.002 * rng.standard_normal(), 4),
                "kc": round(plot_kc(plot), 3)}))
        for s in SECTIONS:
            msgs.append((f"pipe/{s}/flow", {"ts": iso, "flow_m3h": round(meas[s], 4),
                                            "plan_m3h": round(plan[s], 4),
                                            "pressure_bar": round(pressure, 3)}))
        msgs.append(("pv/power", {"ts": iso, "power_kw": round(pv_kw, 3)}))
        msgs.append(("tank/level", {"ts": iso, "level_m3": round(st.tank_m3, 3),
                                    "level_pct": round(100 * st.tank_m3 / self.base_farm.tank_capacity_m3, 2),
                                    "inflow_m3h": round(inflow, 3)}))
        return msgs

    def _step_external(self, ts: datetime, farm: Farm, dt_h: float) -> list[tuple[str, dict[str, Any]]]:
        """Pas en mode « appareil externe » : les débits mesurés par l'ESP32 vident la cuve et arrosent
        les parcelles qu'il ne tient pas (B, C) ; soleil et cuve restent simulés ici. Aucun débit publié."""
        st, rng = self.state, self.rng
        meas = {s: (self.external_flows or {}).get(s, (0.0, 0.0))[0] for s in SECTIONS}
        inflow = float(self.plan.pump_m3[self.plan.hour_index(ts)])
        st.tank_m3 = float(min(self.base_farm.tank_capacity_m3,
                               max(0.0, st.tank_m3 + (inflow - meas["S1"] + meas["S5"]) * dt_h)))
        prof = monthly_pv_profile(self.plan.site).loc[ts.month].tolist()
        frac = ts.hour + ts.minute / 60
        h0 = int(frac) % 24
        v = prof[h0] + (prof[(h0 + 1) % 24] - prof[h0]) * (frac - int(frac))
        day_factor = self.plan.pv_day_factors[min(self.plan.day_index(ts), len(self.plan.pv_day_factors) - 1)]
        pv_kw = max(0.0, v * self.base_farm.pv_peak_kw * day_factor * (1 + 0.03 * rng.standard_normal()))
        et0_day = self.plan.et0_mm[min(self.plan.day_index(ts), len(self.plan.et0_mm) - 1)]
        shape = max(0.0, math.sin(math.pi * (frac - 6) / 13)) if 6 <= frac <= 19 else 0.0
        etc_frac = shape * (math.pi / 26) * dt_h
        iso = ts.isoformat()
        msgs: list[tuple[str, dict[str, Any]]] = []
        for plot in farm.plots:
            if plot.id in self.external_plots:
                continue                                     # sol de A : potentiomètre de l'ESP32
            section = PLOT_SECTION.get(plot.id)
            irrig_mm = (meas[section] * dt_h / (plot.area_ha * 10) * plot.irrigation_efficiency
                        if section else 0.0)
            etc_mm = et0_day * plot_kc(plot) * etc_frac
            st.soil[plot.id] = float(np.clip(st.soil[plot.id] + (irrig_mm - etc_mm) / ROOT_ZONE_MM, 0.05, 0.45))
            msgs.append((f"plot/{plot.id}/soil", {
                "ts": iso, "moisture_m3m3": round(st.soil[plot.id] + 0.002 * rng.standard_normal(), 4),
                "kc": round(plot_kc(plot), 3)}))
        msgs.append(("pv/power", {"ts": iso, "power_kw": round(pv_kw, 3)}))
        msgs.append(("tank/level", {"ts": iso, "level_m3": round(st.tank_m3, 3),
                                    "level_pct": round(100 * st.tank_m3 / self.base_farm.tank_capacity_m3, 2),
                                    "inflow_m3h": round(inflow, 3)}))
        return msgs

    def crop_info(self, ts: datetime) -> list[tuple[str, dict[str, Any]]]:
        """Messages d'information (retenus) : culture et stade du jour par parcelle."""
        return [(f"plot/{p.id}/info", {"ts": ts.isoformat(), "crop": p.crop.name, "stage": p.growth_stage,
                                       "note": p.season_note, "kc": round(plot_kc(p), 3),
                                       "method": p.irrigation_method})
                for p in self.farm_on(ts).plots]


def synthetic_ticks(farm: Farm, plan: PlanSchedule, n_ticks: int, start: datetime,
                    tick_minutes: int = 10, seed: int = 7) -> list[tuple[dict[str, float], dict[str, float]]]:
    """Flux normal (sans fuite) pour préchauffer River : [(débits, plans)] par pas."""
    sim = FarmSimulator(farm, plan, tick_minutes, seed=seed)
    out = []
    ts = start
    for _ in range(n_ticks):
        flows, plans = {}, {}
        for topic, payload in sim.step(ts):
            if topic.startswith("pipe/"):
                s = topic.split("/")[1]
                flows[s], plans[s] = payload["flow_m3h"], payload["plan_m3h"]
        out.append((flows, plans))
        ts += timedelta(minutes=tick_minutes)
    return out
