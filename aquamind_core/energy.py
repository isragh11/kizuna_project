"""Énergie : coût de pompage et production solaire horaire disponible."""

from __future__ import annotations

import logging
from functools import lru_cache

import pandas as pd

from aquamind_core.config import PROCESSED_DIR

logger = logging.getLogger(__name__)

GRAVITY = 9.81
HEAD_LOSS_FACTOR = 1.10  # +10 % de pertes de charge


def pump_kwh_per_m3(depth_m: float, efficiency: float) -> float:
    """Énergie pour remonter 1 m³ du puits (kWh/m³) = 9,81 × H / (3600 × η), + 10 % de pertes."""
    if efficiency <= 0:
        raise ValueError("le rendement de la pompe doit être > 0")
    return GRAVITY * max(0.0, depth_m) / (3600 * efficiency) * HEAD_LOSS_FACTOR


@lru_cache
def monthly_pv_profile(site: str) -> pd.DataFrame:
    """Profil PVGIS moyen (kW par kWc) : index = mois 1..12, colonnes = heures locales 0..23."""
    df = pd.read_parquet(PROCESSED_DIR / f"pv_hourly_{site}.parquet")
    return df.groupby(["month", "hour"])["pv_kw_per_kwc"].mean().unstack("hour").reindex(
        columns=range(24), fill_value=0.0
    ).fillna(0.0)


def pv_hourly_kwh(profile: list[float], peak_kw: float, scale: float = 1.0) -> list[float]:
    """Production solaire horaire (kWh) = profil (kW/kWc) × puissance crête × facteur."""
    return [max(0.0, v) * peak_kw * max(0.0, scale) for v in profile]


def pv_horizon_kwh(site: str, month: int, peak_kw: float, day_factors: list[float],
                   scale: float = 1.0) -> list[float]:
    """Production solaire disponible sur l'horizon (24 h par jour).

    Pour chaque jour : profil PVGIS moyen du mois × facteur d'ensoleillement du jour
    (1 = journée moyenne du mois ; < 1 = nuageux) × pv_scale."""
    profile = monthly_pv_profile(site).loc[month].tolist()
    out: list[float] = []
    for factor in day_factors:
        out.extend(pv_hourly_kwh(profile, peak_kw, scale * factor))
    return out
