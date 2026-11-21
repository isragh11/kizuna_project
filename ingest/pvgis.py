"""PVGIS (JRC) : profil de production photovoltaïque horaire normalisé (kW par kWc).

Sortie : data/processed/pv_hourly_{site}.parquet
  index  : horodatage local (sans fuseau), année 2023
  colonnes : site, pv_kw_per_kwc, month, hour, source ("pvgis" | "fallback_sinus")
Fallback : profil sinusoïdal 6h–19h si l'API échoue (et pas de cache).
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ingest.http_cache import ApiError, CacheMiss, get_json
from ingest.sites import Site, save_parquet

logger = logging.getLogger(__name__)

PVGIS_URL = "https://re.jrc.ec.europa.eu/api/v5_3/seriescalc"
YEAR = 2023


def fetch_pv_profile(lat: float, lon: float, tz: str) -> pd.DataFrame:
    """Série horaire PVGIS pour 1 kWc (pertes 14 %, inclinaison optimale)."""
    params = {
        "lat": lat, "lon": lon, "startyear": YEAR, "endyear": YEAR,
        "pvcalculation": 1, "peakpower": 1, "loss": 14,
        "optimalangles": 1,  # inclinaison/azimut optimaux (sinon panneau à plat)
        "outputformat": "json",
    }
    data = get_json(PVGIS_URL, params, f"pvgis_{lat:.2f}_{lon:.2f}_{YEAR}", ttl_hours=None)
    hourly = pd.DataFrame(data["outputs"]["hourly"])
    # "20230101:0009" = heure UTC (minute = centre de l'intervalle) → arrondi à l'heure, heure locale
    ts = pd.to_datetime(hourly["time"], format="%Y%m%d:%H%M", utc=True).dt.floor("h")
    df = pd.DataFrame({
        "time": ts.dt.tz_convert(tz).dt.tz_localize(None),
        "pv_kw_per_kwc": hourly["P"].to_numpy() / 1000.0,  # W pour 1 kWc → kW/kWc
    })
    df["source"] = "pvgis"
    return df


def sinus_profile(tz: str, peak: float = 0.75) -> pd.DataFrame:
    """Fallback : demi-sinusoïde entre 6h et 19h, modulée par la saison (estimation)."""
    idx = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31 23:00", freq="h")
    hour = idx.hour.to_numpy() + 0.5
    day_shape = np.where((hour > 6) & (hour < 19), np.sin(np.pi * (hour - 6) / 13), 0.0)
    season = 0.75 + 0.25 * np.cos(2 * np.pi * (idx.dayofyear.to_numpy() - 172) / 365)
    df = pd.DataFrame({"time": idx, "pv_kw_per_kwc": peak * day_shape * season})
    df["source"] = "fallback_sinus"
    return df


def run(site: Site) -> dict[str, pd.DataFrame]:
    """Profil PV du site (PVGIS ou fallback) → parquet."""
    try:
        df = fetch_pv_profile(site.lat, site.lon, site.tz)
    except (ApiError, CacheMiss, KeyError, ValueError) as exc:
        logger.warning("PVGIS indisponible pour %s (%s) → profil sinusoïdal de secours", site.name, exc)
        df = sinus_profile(site.tz)
    # conversion UTC→local : 1 h peut se dupliquer/manquer au changement d'heure → moyenne/réindexation
    df = df.groupby("time", as_index=True)["pv_kw_per_kwc"].mean().to_frame().join(
        df.groupby("time")["source"].first())
    df = df.reindex(pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31 23:00", freq="h", name="time"))
    df["pv_kw_per_kwc"] = df["pv_kw_per_kwc"].interpolate(limit=2).fillna(0.0)
    df["source"] = df["source"].ffill().bfill()
    df.insert(0, "site", site.key)
    df["month"], df["hour"] = df.index.month, df.index.hour
    save_parquet(df, f"pv_hourly_{site.key}")
    return {f"pv_hourly_{site.key}": df}
