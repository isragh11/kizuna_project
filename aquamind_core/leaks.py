"""Détection de fuites : chargement des artefacts (le backend n'entraîne jamais).

- IsolationForest BattLeDIM (iforest.joblib + leak_threshold.json) : indicateur lent, sans alerte
  (`use_for_alerting: false`, voir docs/RAPPORT_MODELES.md §7).
- River HalfSpaceTrees : recréé à partir de river_config.json ; il apprend en ligne dans le consumer.
- Le mode d'alerte recommandé (`recommended_mode`) vient du notebook 04 (5 graines, §8 du rapport) :
  `rule_adaptive` = règle de bilan de masse adaptative (AdaptiveRule), River en simple indice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from statistics import median
from typing import Any

from aquamind_core.config import ARTIFACTS_DIR


def _read(name: str) -> dict[str, Any]:
    with open(ARTIFACTS_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def load_iforest() -> Any:
    """IsolationForest entraîné sur BattLeDIM (notebook 03)."""
    import joblib

    return joblib.load(ARTIFACTS_DIR / "iforest.joblib")


@lru_cache
def load_leak_threshold() -> dict[str, Any]:
    """Seuils et logique d'alerte de l'IsolationForest et du CUSUM (notebook 03)."""
    return _read("leak_threshold.json")


@lru_cache
def load_leak_features() -> dict[str, Any]:
    """Définition des variables de l'IsolationForest (notebook 03)."""
    return _read("leak_features.json")


@lru_cache
def load_river_config() -> dict[str, Any]:
    """Configuration River + règle de bilan de masse (notebook 04)."""
    return _read("river_config.json")


def make_river() -> Any:
    """Nouveau HalfSpaceTrees avec les hyperparamètres calibrés (à faire apprendre en ligne)."""
    from river import anomaly

    p = load_river_config()["params"]
    return anomaly.HalfSpaceTrees(n_trees=p["n_trees"], height=p["height"],
                                  window_size=p["window_size"], seed=p["seed"])


def scale_features(x: dict[str, float]) -> dict[str, float]:
    """Mise à l'échelle fixe de River : clip(0,5 + x / (2 × scale_m3h), 0, 1)."""
    scale = load_river_config()["feature_scaling"]["scale_m3h"]
    return {k: min(1.0, max(0.0, 0.5 + v / (2 * scale))) for k, v in x.items()}


# ----------------------------------------------------------------- détection en direct

SECTIONS = ["S1", "S2", "S3", "S4", "S5"]
PUMPING_PLAN_M3H = 0.5


def flow_features(flows: dict[str, float], plans: dict[str, float]) -> dict[str, float]:
    """Variables du notebook 04 : déséquilibre du bilan de masse et résidus mesure − plan."""
    return {
        "imbalance": flows["S1"] - (flows["S2"] + flows["S3"] + flows["S4"] + flows["S5"]),
        "s1_resid": flows["S1"] - plans["S1"],
        "s2_resid": flows["S2"] - plans["S2"],
        "s3_resid": flows["S3"] - plans["S3"],
        "s4_resid": flows["S4"] - plans["S4"],
    }


@dataclass
class RuleStep:
    """Résultat d'un message pour la règle adaptative."""

    alarm: bool
    baseline: float
    deviation: float
    accepted_new_normal: bool = False


class AdaptiveRule:
    """Règle de bilan de masse adaptative — même logique que le notebook 04 (section 6 ter).

    Pendant le pompage, le déséquilibre est comparé à la médiane des `window` derniers messages de
    pompage jugés normaux (référence 0 pompe arrêtée ou historique trop court). Les messages en écart
    n'entrent pas dans la référence ; un écart qui persiste `accept_after` messages de pompage devient
    le « nouveau normal ». La commande new_normal() fait la même chose immédiatement (bouton)."""

    def __init__(self, threshold: float, n_consec: int, window: int, min_history: int,
                 accept_after: int) -> None:
        self.threshold, self.n_consec = threshold, n_consec
        self.window, self.min_history, self.accept_after = window, min_history, accept_after
        self.hist: list[float] = []
        self.pending: list[float] = []
        self.above_run = 0
        self.relearn = 0

    @classmethod
    def from_config(cls) -> "AdaptiveRule":
        a = load_river_config()["adaptive_rule"]
        return cls(a["abs_threshold_m3h"], a["consecutive_ticks"], a["window_pumping_ticks"],
                   a["min_history_ticks"], a["accept_after_pumping_ticks"])

    def update(self, imbalance: float, pumping: bool) -> RuleStep:
        """Traite un message ; renvoie l'état d'alarme (≥ n_consec messages en écart)."""
        if pumping and self.relearn > 0:          # après « nouveau normal » : on réapprend, sans alarme
            self.hist = (self.hist + [imbalance])[-self.window:]
            self.relearn -= 1
            return RuleStep(False, float(median(self.hist)), 0.0)
        base = float(median(self.hist[-self.window:])) if pumping and len(self.hist) >= self.min_history else 0.0
        deviation = imbalance - base
        self.above_run = self.above_run + 1 if abs(deviation) > self.threshold else 0
        alarm = self.above_run >= self.n_consec
        accepted = False
        if pumping:
            if self.above_run > 0:
                self.pending.append(imbalance)
                if len(self.pending) >= self.accept_after:
                    self.hist, self.pending, self.above_run = self.pending[-self.window:], [], 0
                    accepted = True
            else:
                self.pending = []
                self.hist = (self.hist + [imbalance])[-self.window:]
        return RuleStep(alarm, base, deviation, accepted)

    def new_normal(self) -> None:
        """Bouton « nouveau normal » : l'état actuel devient la référence."""
        self.hist = self.pending[-self.window:]
        self.pending, self.above_run = [], 0
        self.relearn = max(0, self.min_history - len(self.hist))


class RiverIndex:
    """Indice River HalfSpaceTrees : « confirmé par l'IA » ; n'empêche ni ne crée jamais une alerte."""

    def __init__(self) -> None:
        cfg = load_river_config()
        self.model = make_river()
        self.threshold = float(cfg["threshold"])
        self.n_consec = int(cfg["consecutive_ticks"])
        self.run = 0
        self.last_score = 0.0

    def update(self, features: dict[str, float]) -> bool:
        """score_one puis learn_one ; True si le score dépasse le seuil n_consec messages de suite."""
        x = scale_features(features)
        self.last_score = float(self.model.score_one(x))
        self.model.learn_one(x)
        self.run = self.run + 1 if self.last_score > self.threshold else 0
        return self.run >= self.n_consec


def estimate_losses(loss_m3h: float, pumping_hours_per_day: float, kwh_per_m3: float,
                    water_value_tnd_per_m3: float, grid_price_tnd_per_kwh: float) -> dict[str, float]:
    """Pertes d'une fuite : m³/jour, kWh de pompage gaspillés/jour, TND/jour (eau + électricité).

    Une fuite ne coule que quand la conduite est en charge : m³/j = débit perdu × heures de
    pompage par jour. TND = m³ × valeur de l'eau + kWh × tarif réseau (hypothèses, economics.json)."""
    m3_day = max(0.0, loss_m3h) * max(0.0, pumping_hours_per_day)
    kwh_day = m3_day * kwh_per_m3
    tnd_day = m3_day * water_value_tnd_per_m3 + kwh_day * grid_price_tnd_per_kwh
    return {"m3_day": round(m3_day, 2), "kwh_day": round(kwh_day, 2), "tnd_day": round(tnd_day, 2)}


def locate_section(features: dict[str, float], min_drop_m3h: float = 0.2) -> str:
    """Tronçon suspect : celui dont le débit mesuré est le plus en dessous du plan (S2–S4), sinon S1.

    Seuil 0,2 m³/h : au-dessus du bruit d'un débitmètre (≈ 0,15 m³/h à 7 m³/h) ; une fuite de 1,5 m³/h
    fait baisser son tronçon de 0,375 m³/h (25 % du débit de fuite, notebook 04)."""
    resid = {s: features[f"{s.lower()}_resid"] for s in ("S2", "S3", "S4")}
    section = min(resid, key=resid.get)
    return section if resid[section] < -min_drop_m3h else "S1"
