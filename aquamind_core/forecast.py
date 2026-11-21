"""Prévision de l'ET0 et entrées météo du plan (horizon 24–48 h).

Chaîne de secours pour l'ET0 de J+1 : modèle LightGBM (et0_lgbm.txt) → ET0 Open-Meteo prévue
→ Hargreaves (températures prévues). Les variables du modèle sont construites EXACTEMENT comme dans
notebooks/02_train_et0_lightgbm.ipynb (voir et0_features.json) ; le test unitaire le vérifie en
retrouvant la MAE de test enregistrée.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

from aquamind_core.agronomy import hargreaves_et0
from aquamind_core.config import ARTIFACTS_DIR, PROCESSED_DIR, get_settings

logger = logging.getLogger(__name__)

HISTORY_DAYS = 7
DEFAULT_MAE_MM = 0.63
PV_FACTOR_BOUNDS = (0.05, 1.3)


@dataclass
class PlanInputs:
    """Météo journalière de l'horizon du plan (une valeur par jour) et provenance de chaque valeur."""

    site: str
    start: date
    et0_mm: list[float]
    rain_mm: list[float]
    tmean_c: list[float]
    pv_day_factors: list[float]
    sources: list[str] = field(default_factory=list)
    tmax_c: list[float] = field(default_factory=list)

    @property
    def days(self) -> int:
        return len(self.et0_mm)

    def as_dict(self) -> dict[str, Any]:
        return {"site": self.site, "start": self.start.isoformat(), "et0_mm": self.et0_mm,
                "rain_mm": self.rain_mm, "tmean_c": self.tmean_c,
                "tmax_c": self.tmax_c, "pv_day_factors": self.pv_day_factors, "sources": self.sources}


# ---------------------------------------------------------------- modèle LightGBM

@lru_cache
def load_feature_meta() -> dict[str, Any]:
    """Métadonnées du modèle : ordre des variables, colonnes sources, codes de site."""
    with open(ARTIFACTS_DIR / "et0_features.json", encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def load_et0_model() -> Any:
    """Charge le booster LightGBM (format natif). Le backend ne réentraîne jamais."""
    import lightgbm as lgb

    model = lgb.Booster(model_file=str(ARTIFACTS_DIR / "et0_lgbm.txt"))
    expected = load_feature_meta()["features"]
    if model.feature_name() != expected:
        raise RuntimeError("et0_lgbm.txt et et0_features.json ne correspondent pas")
    return model


@lru_cache
def et0_mae(site: str) -> float:
    """MAE de test du modèle ET0 pour le site (mm/j) : marge de sécurité par défaut."""
    try:
        with open(ARTIFACTS_DIR / "et0_metrics.json", encoding="utf-8") as f:
            return float(json.load(f)["test"][site]["lightgbm"]["MAE"])
    except (OSError, KeyError, ValueError) as exc:
        logger.warning("MAE ET0 introuvable (%s) : marge par défaut %.2f mm/j", exc, DEFAULT_MAE_MM)
        return DEFAULT_MAE_MM


def build_features(daily: pd.DataFrame, site: str) -> pd.DataFrame:
    """Variables du modèle pour chaque jour J (prédiction de J+1), comme dans le notebook 02.

    daily : index = date, colonnes = colonnes sources Open-Meteo (archive ou récente)."""
    meta = load_feature_meta()
    source: dict[str, str] = meta["source_columns"]
    g = daily.sort_index().asfreq("D")
    f = pd.DataFrame(index=g.index)
    for short, col in source.items():
        values = g[col] if col in g else pd.Series(np.nan, index=g.index)
        for k in range(1, HISTORY_DAYS + 1):
            f[f"{short}_lag{k}"] = values.shift(k - 1)
    for short, col in source.items():
        values = g[col] if col in g else pd.Series(np.nan, index=g.index)
        f[f"{short}_mean3"] = values.rolling(3).mean()
        f[f"{short}_mean7"] = values.rolling(7).mean()
    amp = g[source["tmax"]] - g[source["tmin"]]
    f["amp_lag1"] = amp
    f["amp_mean7"] = amp.rolling(7).mean()
    doy = (f.index + pd.Timedelta(days=1)).dayofyear.to_numpy()
    f["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    f["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    f["site"] = meta["site_codes"][site]
    return f[meta["features"]]


def predict_et0_next(daily: pd.DataFrame, site: str) -> float | None:
    """ET0 de J+1 (mm/j) à partir des 7 jours qui se terminent à J (dernière ligne de `daily`).

    Retourne None si une des variables obligatoires (températures, ET0) manque."""
    feats = build_features(daily.iloc[-HISTORY_DAYS:], site).iloc[[-1]]
    required = [c for c in feats.columns if c.startswith(("et0_", "tmax_", "tmin_"))]
    if feats[required].isna().any(axis=None):
        return None
    return float(max(0.0, load_et0_model().predict(feats)[0]))


# ---------------------------------------------------------------- données météo

@lru_cache
def load_archive(site: str) -> pd.DataFrame:
    """Archive Open-Meteo journalière 2015–2025 du site."""
    return pd.read_parquet(PROCESSED_DIR / f"weather_daily_{site}.parquet")


@lru_cache
def monthly_radiation(site: str) -> pd.Series:
    """Rayonnement journalier moyen par mois (MJ/m²/j), sur l'archive : référence « journée normale »."""
    arch = load_archive(site)
    return arch.groupby(arch.index.month)["shortwave_radiation_sum"].mean()


def pv_factor(site: str, day: date, radiation_mj: float | None) -> float:
    """Ensoleillement du jour / moyenne du mois (1 = normal), borné ; 1 si inconnu."""
    if radiation_mj is None or not np.isfinite(radiation_mj):
        return 1.0
    ref = float(monthly_radiation(site).loc[day.month])
    return float(np.clip(radiation_mj / ref, *PV_FACTOR_BOUNDS)) if ref > 0 else 1.0


def _site_lat(site: str) -> float:
    s = get_settings()
    return s.farm_lat if site == "tunisia" else s.japan_lat


def _hargreaves_from_row(row: pd.Series, site: str, day: date) -> float | None:
    tmax, tmin = row.get("temperature_2m_max"), row.get("temperature_2m_min")
    if tmax is None or tmin is None or not (np.isfinite(tmax) and np.isfinite(tmin)):
        return None
    return hargreaves_et0(float(tmax), float(tmin), _site_lat(site), day.timetuple().tm_yday)


def _day_inputs(daily: pd.DataFrame, site: str, start: date, days: int,
                model_label: str, weather_label: str) -> PlanInputs:
    """Entrées de l'horizon : J1 = modèle LightGBM (7 jours précédents) ; J2… = ET0 Open-Meteo du jour.

    Repli : ET0 Open-Meteo, puis Hargreaves, si le modèle ou la donnée manque."""
    et0, rain, tmean, tmax, pvf, sources = [], [], [], [], [], []
    for d in range(days):
        day = start + timedelta(days=d)
        ts = pd.Timestamp(day)
        row = daily.loc[ts] if ts in daily.index else pd.Series(dtype=float)
        value, src = None, ""
        if d == 0:
            hist = daily.loc[:ts - pd.Timedelta(days=1)].tail(HISTORY_DAYS)
            if len(hist) == HISTORY_DAYS:
                try:
                    value = predict_et0_next(hist, site)
                    src = model_label
                except Exception as exc:  # modèle absent ou illisible : on continue avec le repli
                    logger.warning("Modèle ET0 indisponible (%s) : repli Open-Meteo", exc)
                    value = None
        if value is None and np.isfinite(row.get("et0_fao_evapotranspiration", np.nan)):
            value, src = float(row["et0_fao_evapotranspiration"]), weather_label
        if value is None:
            value, src = _hargreaves_from_row(row, site, day), "Hargreaves (températures)"
        if value is None:
            raise ValueError(f"aucune donnée météo pour {site} le {day}")
        et0.append(round(value, 3))
        sources.append(src)
        rain.append(float(np.nan_to_num(row.get("precipitation_sum", 0.0))))
        tmean.append(float(np.nan_to_num(row.get("temperature_2m_mean", 20.0), nan=20.0)))
        tmax.append(float(np.nan_to_num(row.get("temperature_2m_max", tmean[-1]), nan=tmean[-1])))
        pvf.append(round(pv_factor(site, day, row.get("shortwave_radiation_sum")), 3))
    return PlanInputs(site, start, et0, rain, tmean, pvf, sources, tmax)


def archive_inputs(site: str, start: date, days: int = 2) -> PlanInputs:
    """Entrées d'un scénario rejoué sur des jours réels de l'archive (2015–2025).

    J1 : prédiction du modèle à partir des 7 jours précédents (comme en exploitation).
    J2 : ET0 Open-Meteo de l'archive (joue le rôle de la prévision Open-Meteo à J+2)."""
    return _day_inputs(load_archive(site), site, start, days,
                       model_label="modèle LightGBM (7 jours précédents)",
                       weather_label="Open-Meteo (archive)")


def recent_daily(site: str) -> pd.DataFrame:
    """7 derniers jours + 3 jours de prévision (API avec cache ; sinon parquet ; sinon prévision horaire)."""
    from ingest.open_meteo import fetch_recent_daily  # import local : aquamind_core reste sans réseau
    from ingest.sites import get_sites

    s = get_sites()[site]
    try:
        return fetch_recent_daily(s.lat, s.lon, tz=s.tz)
    except Exception as exc:
        logger.warning("Météo récente indisponible (%s) : repli sur les fichiers locaux", exc)
    path = PROCESSED_DIR / f"recent_daily_{site}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    return _daily_from_hourly_forecast(site)


def _daily_from_hourly_forecast(site: str) -> pd.DataFrame:
    """Agrège la prévision horaire (forecast_hourly_{site}.parquet) en valeurs journalières."""
    h = pd.read_parquet(PROCESSED_DIR / f"forecast_hourly_{site}.parquet")
    day = h.index.normalize()
    return pd.DataFrame({
        "temperature_2m_max": h.groupby(day)["temperature_2m"].max(),
        "temperature_2m_min": h.groupby(day)["temperature_2m"].min(),
        "temperature_2m_mean": h.groupby(day)["temperature_2m"].mean(),
        "precipitation_sum": h.groupby(day)["precipitation"].sum(),
        "shortwave_radiation_sum": h.groupby(day)["shortwave_radiation"].sum() * 0.0036,  # Wh→MJ
        "et0_fao_evapotranspiration": h.groupby(day)["et0_fao_evapotranspiration"].sum(),
    })


def live_inputs(site: str, start: date | None = None, days: int = 2) -> PlanInputs:
    """Entrées du plan en exploitation : à partir de demain par défaut, prévision Open-Meteo."""
    daily = recent_daily(site)
    if start is None:
        start = date.today() + timedelta(days=1)
    return _day_inputs(daily, site, start, days,
                       model_label="modèle LightGBM (7 derniers jours)",
                       weather_label="prévision Open-Meteo")


def predict_et0_tomorrow(site: str) -> dict[str, Any]:
    """ET0 prévue pour demain : valeur (mm/j), source, et les deux références si disponibles."""
    inputs = live_inputs(site, days=1)
    return {"site": site, "date": inputs.start.isoformat(), "et0_mm": inputs.et0_mm[0],
            "source": inputs.sources[0], "mae_mm": et0_mae(site)}
