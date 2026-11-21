"""Open-Meteo : archive météo journalière (2015–2025) et prévision horaire 7 jours.

Sorties : data/processed/weather_daily_{site}.parquet, forecast_hourly_{site}.parquet.
Aucune clé API. Tous les appels passent par ingest.http_cache.get_json.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import pandas as pd

from ingest.http_cache import ApiError, get_json
from ingest.sites import Site, save_parquet

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

DAILY_VARS = [
    "temperature_2m_max", "temperature_2m_min", "temperature_2m_mean", "precipitation_sum",
    "shortwave_radiation_sum", "wind_speed_10m_max", "relative_humidity_2m_mean",
    "et0_fao_evapotranspiration",
]
HOURLY_VARS = [
    "temperature_2m", "relative_humidity_2m", "shortwave_radiation", "et0_fao_evapotranspiration",
    "soil_moisture_0_to_1cm", "soil_moisture_3_to_9cm", "soil_moisture_9_to_27cm", "precipitation",
]
ARCHIVE_START = date(2015, 1, 1)
ARCHIVE_END = date(2025, 12, 31)
CHUNK_YEARS = 3
FORECAST_TTL_H = 1.0

_BAD_VAR = re.compile(r"invalid String value (\w+)")


def _year_chunks(start: date, end: date, years: int = CHUNK_YEARS) -> list[tuple[date, date]]:
    """Découpe [start, end] en tranches de `years` années civiles."""
    chunks, y = [], start.year
    while y <= end.year:
        c_start = max(start, date(y, 1, 1))
        c_end = min(end, date(y + years - 1, 12, 31))
        chunks.append((c_start, c_end))
        y += years
    return chunks


def _get_with_var_drop(url: str, params: dict, block: str, variables: list[str], key: str,
                       ttl_hours: float | None) -> tuple[dict, list[str]]:
    """Appelle l'API ; si une variable est refusée (HTTP 400), la retire et réessaie."""
    variables = list(variables)
    dropped: list[str] = []
    while variables:
        p = {**params, block: ",".join(variables)}
        try:
            # même clé quelles que soient les variables : seules les réponses valides sont cachées,
            # donc le mode hors-ligne retrouve la réponse même si une variable a été retirée
            return get_json(url, p, key, ttl_hours), dropped
        except ApiError as exc:
            match = _BAD_VAR.search(exc.reason)
            if exc.status != 400 or not match or match.group(1) not in variables:
                raise
            bad = match.group(1)
            logger.warning("Open-Meteo refuse la variable '%s' : retirée (%s)", bad, exc.reason)
            variables.remove(bad)
            dropped.append(bad)
    raise ApiError(400, "toutes les variables ont été refusées", url)


def fetch_archive(lat: float, lon: float, start: date = ARCHIVE_START, end: date = ARCHIVE_END,
                  tz: str = "UTC") -> pd.DataFrame:
    """Météo journalière historique (index = date locale), par tranches de 3 ans."""
    frames, all_dropped = [], set()
    for c_start, c_end in _year_chunks(start, end):
        params = {"latitude": lat, "longitude": lon, "start_date": c_start.isoformat(),
                  "end_date": c_end.isoformat(), "timezone": tz}
        key = f"openmeteo_archive_{lat:.2f}_{lon:.2f}_{c_start:%Y%m%d}_{c_end:%Y%m%d}"
        data, dropped = _get_with_var_drop(ARCHIVE_URL, params, "daily", DAILY_VARS, key, ttl_hours=None)
        all_dropped.update(dropped)
        frames.append(pd.DataFrame(data["daily"]))
    if all_dropped:
        logger.warning("Variables absentes de l'archive : %s", sorted(all_dropped))
    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df.pop("time"))
    return df.set_index("date").sort_index()


def fetch_forecast(lat: float, lon: float, tz: str = "UTC") -> pd.DataFrame:
    """Prévision horaire sur 7 jours (index = heure locale, sans fuseau)."""
    params = {"latitude": lat, "longitude": lon, "timezone": tz, "forecast_days": 7}
    key = f"openmeteo_forecast_{lat:.2f}_{lon:.2f}"
    data, dropped = _get_with_var_drop(FORECAST_URL, params, "hourly", HOURLY_VARS, key,
                                       ttl_hours=FORECAST_TTL_H)
    if dropped:
        logger.warning("Variables absentes de la prévision : %s", dropped)
    df = pd.DataFrame(data["hourly"])
    df["time"] = pd.to_datetime(df["time"])
    return df.set_index("time").sort_index()


def run(site: Site) -> dict[str, pd.DataFrame]:
    """Télécharge archive + prévision pour un site et écrit les parquet."""
    daily = fetch_archive(site.lat, site.lon, tz=site.tz)
    daily.insert(0, "site", site.key)
    save_parquet(daily, f"weather_daily_{site.key}")

    hourly = fetch_forecast(site.lat, site.lon, tz=site.tz)
    hourly.insert(0, "site", site.key)
    save_parquet(hourly, f"forecast_hourly_{site.key}")
    return {f"weather_daily_{site.key}": daily, f"forecast_hourly_{site.key}": hourly}
