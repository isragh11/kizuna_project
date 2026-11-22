"""Profil d'un champ : sol, ensoleillement et climat au centre du champ.

Mêmes sources et même cache disque que l'ingestion (ingest/) :
- sol      : SoilGrids (ISRIC) → texture USDA + réserve utile (Saxton & Rawls) ;
- soleil   : PVGIS (JRC) « PVcalc » → production annuelle et mensuelle par kWc, inclinaison optimale ;
- climat   : Open-Meteo (archive 2023-2025) → ET0 et pluie moyennes par mois.
Si une source est indisponible (hors-ligne, service en panne), une valeur de secours est utilisée et
marquée `assumption: True` : l'interface l'affiche comme une estimation.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from math import cos, pi, radians
from typing import Any

from aquamind_core.parcel_geo import centroid

logger = logging.getLogger(__name__)

PVGIS_CALC_URL = "https://re.jrc.ec.europa.eu/api/v5_3/PVcalc"
CLIMATE_START, CLIMATE_END = date(2023, 1, 1), date(2025, 12, 31)
DAYS_IN_MONTH = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

# Choix proposés à l'agriculteur s'il veut corriger le sol (réserve utile typique, mm d'eau par m de sol)
SOIL_CLASSES: dict[str, dict[str, Any]] = {
    "sable": {"label": "Sableux (léger)", "awc_mm_per_m": 70},
    "limon sableux": {"label": "Sablo-limoneux", "awc_mm_per_m": 120},
    "limon": {"label": "Limoneux (franc)", "awc_mm_per_m": 170},
    "limon argileux": {"label": "Argilo-limoneux", "awc_mm_per_m": 165},
    "argile": {"label": "Argileux (lourd)", "awc_mm_per_m": 140},
}


def soil_advice(awc_mm_per_m: float) -> str:
    """Ce que la réserve utile change pour l'arrosage, en mots simples."""
    if awc_mm_per_m < 100:
        return "Retient peu d'eau : arrosages courts et fréquents, le goutte-à-goutte est idéal."
    if awc_mm_per_m < 150:
        return "Retient l'eau moyennement : arrosages réguliers, de durée moyenne."
    return "Retient bien l'eau : arrosages plus espacés et plus longs possibles."


def sun_label(kwh_per_kwc_year: float) -> str:
    if kwh_per_kwc_year >= 1600:
        return "Excellent"
    if kwh_per_kwc_year >= 1300:
        return "Très bon"
    if kwh_per_kwc_year >= 1000:
        return "Bon"
    return "Moyen"


# ------------------------------------------------------------------------------------------- sol

def soil_at(lat: float, lon: float) -> dict[str, Any]:
    from ingest.http_cache import ApiError, CacheMiss
    from ingest.soilgrids import FALLBACK_AWC_MM_M, fetch_soil, saxton_rawls_awc, usda_texture

    try:
        df, qlat, qlon = fetch_soil(lat, lon)
        w = df["thickness_cm"]
        sand, silt, clay = (float((df[c] * w).sum() / w.sum()) for c in ("sand_pct", "silt_pct", "clay_pct"))
        awc = round(saxton_rawls_awc(sand, clay), 1)
        far = abs(qlat - lat) > 1e-6 or abs(qlon - lon) > 1e-6
        return {"texture": usda_texture(sand, silt, clay), "sand_pct": round(sand, 1), "silt_pct": round(silt, 1),
                "clay_pct": round(clay, 1), "awc_mm_per_m": awc, "advice": soil_advice(awc),
                "source": "SoilGrids (ISRIC), 0–30 cm" + (" — point voisin (zone masquée)" if far else ""),
                "assumption": False}
    except (ApiError, CacheMiss, KeyError, ValueError, OSError) as exc:
        logger.warning("SoilGrids indisponible (%s) : réserve utile par défaut", exc)
        return {"texture": "inconnue", "sand_pct": None, "silt_pct": None, "clay_pct": None,
                "awc_mm_per_m": FALLBACK_AWC_MM_M, "advice": soil_advice(FALLBACK_AWC_MM_M),
                "source": "valeur par défaut (SoilGrids indisponible)", "assumption": True}


# ------------------------------------------------------------------------------------------ soleil

def _sun_fallback(lat: float) -> dict[str, Any]:
    """Ordre de grandeur selon la latitude (estimation) si PVGIS est indisponible."""
    alat = abs(lat)
    year = max(800.0, min(2000.0, 2050.0 - 14.0 * max(0.0, alat - 20.0)))
    season = [1 + 0.25 * cos(2 * pi * (m - (6 if lat >= 0 else 12)) / 12) for m in range(1, 13)]
    k = year / sum(s * d for s, d in zip(season, DAYS_IN_MONTH))
    monthly = [round(k * s * d, 1) for s, d in zip(season, DAYS_IN_MONTH)]
    return {"kwh_per_kwc_year": round(year), "kwh_per_kwc_month": monthly,
            "optimal_tilt_deg": round(0.87 * alat + 3), "optimal_azimuth_deg": 0 if lat >= 0 else 180,
            "label": sun_label(year), "source": "estimation selon la latitude (PVGIS indisponible)", "assumption": True}


def sun_at(lat: float, lon: float) -> dict[str, Any]:
    from ingest.http_cache import ApiError, CacheMiss, get_json

    params = {"lat": round(lat, 2), "lon": round(lon, 2), "peakpower": 1, "loss": 14,
              "optimalangles": 1, "outputformat": "json"}
    try:
        data = get_json(PVGIS_CALC_URL, params, f"pvgis_pvcalc_{lat:.2f}_{lon:.2f}", ttl_hours=None)
        fixed = data["outputs"]["totals"]["fixed"]
        months = sorted(data["outputs"]["monthly"]["fixed"], key=lambda m: m["month"])
        mount = data["inputs"]["mounting_system"]["fixed"]
        year = float(fixed["E_y"])
        az = float(mount["azimuth"]["value"])            # PVGIS : 0 = plein sud, -90 = est
        return {"kwh_per_kwc_year": round(year), "kwh_per_kwc_month": [round(float(m["E_m"]), 1) for m in months],
                "optimal_tilt_deg": round(float(mount["slope"]["value"])),
                "optimal_azimuth_deg": round(az if lat >= 0 else 180 + az),
                "label": sun_label(year), "source": "PVGIS (Commission européenne), relief inclus", "assumption": False}
    except (ApiError, CacheMiss, KeyError, ValueError, TypeError, OSError) as exc:
        logger.warning("PVGIS indisponible (%s) : estimation selon la latitude", exc)
        return _sun_fallback(lat)


# ----------------------------------------------------------------------------------------- climat

def _monthly(df) -> dict[str, list[float]]:
    df = df.copy()
    df["month"] = df.index.month
    et0 = df.groupby("month")["et0_fao_evapotranspiration"].mean().reindex(range(1, 13)).ffill().bfill()
    rain_month = df.groupby([df.index.year, "month"])["precipitation_sum"].sum().groupby(level=1).mean()
    rain_month = rain_month.reindex(range(1, 13)).fillna(0.0)
    return {"et0_mm_day": [round(float(v), 2) for v in et0], "rain_mm_month": [round(float(v), 1) for v in rain_month]}


def climate_at(lat: float, lon: float) -> dict[str, Any]:
    from ingest.http_cache import ApiError, CacheMiss

    try:
        from ingest.open_meteo import fetch_archive

        df = fetch_archive(round(lat, 2), round(lon, 2), CLIMATE_START, CLIMATE_END, tz="auto")
        return {**_monthly(df), "source": "Open-Meteo, moyenne 2023–2025", "assumption": False}
    except (ApiError, CacheMiss, KeyError, ValueError, OSError) as exc:
        logger.warning("Open-Meteo indisponible (%s) : climat du site de démo le plus proche", exc)
    import pandas as pd

    from aquamind_core.config import PROCESSED_DIR, get_settings

    s = get_settings()
    sites = {"tunisia": (s.farm_lat, s.farm_lon), "japan": (s.japan_lat, s.japan_lon)}
    near = min(sites, key=lambda k: (sites[k][0] - lat) ** 2 + (sites[k][1] - lon) ** 2)
    try:
        df = pd.read_parquet(PROCESSED_DIR / f"weather_daily_{near}.parquet")
        return {**_monthly(df), "source": f"climat du site de démo « {near} » (Open-Meteo indisponible)",
                "assumption": True}
    except (OSError, KeyError, ValueError):
        return {"et0_mm_day": [3.0, 3.5, 4.5, 5.5, 6.5, 7.5, 8.0, 7.5, 6.0, 4.5, 3.5, 3.0],
                "rain_mm_month": [20.0] * 12, "source": "valeurs par défaut (aucune donnée climatique)",
                "assumption": True}


# ---------------------------------------------------------------------------------------- ensemble

def environment(geometry: dict[str, Any]) -> dict[str, Any]:
    """Sol, soleil et climat au centre du champ (appels réseau avec cache disque)."""
    lon, lat = centroid(geometry)
    return {"centroid": [round(lon, 6), round(lat, 6)], "soil": soil_at(lat, lon), "sun": sun_at(lat, lon),
            "climate": climate_at(lat, lon), "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def effective_soil(env: dict[str, Any] | None, profile: dict[str, Any]) -> dict[str, Any] | None:
    """Sol retenu : correction de l'agriculteur si elle existe, sinon SoilGrids."""
    override = profile.get("soil_override")
    if override in SOIL_CLASSES:
        c = SOIL_CLASSES[override]
        return {"texture": override, "awc_mm_per_m": c["awc_mm_per_m"], "advice": soil_advice(c["awc_mm_per_m"]),
                "source": "indiqué par vous", "assumption": False, "sand_pct": None, "silt_pct": None, "clay_pct": None}
    return env["soil"] if env else None
