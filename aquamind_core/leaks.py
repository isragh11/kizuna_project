"""Détection de fuites : chargement des artefacts (le backend n'entraîne jamais).

- IsolationForest BattLeDIM (iforest.joblib + leak_threshold.json) : indicateur lent, sans alerte
  (`use_for_alerting: false`, voir docs/RAPPORT_MODELES.md §7).
- River HalfSpaceTrees : recréé à partir de river_config.json ; il apprend en ligne dans le consumer.
- Le mode d'alerte recommandé (`recommended_mode`) vient du notebook 04 (5 graines, §8 du rapport).
"""

from __future__ import annotations

import json
from functools import lru_cache
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
