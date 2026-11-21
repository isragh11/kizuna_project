"""SoilGrids (ISRIC) : texture du sol 0–30 cm → réserve utile (mm/m).

- Requête clay/sand/silt, profondeurs 0-5, 5-15, 15-30 cm, valeur moyenne.
- SoilGrids masque les zones urbaines/eau (valeurs null) : on essaie alors des
  points voisins (rayon ≈ 5 km) avant d'utiliser le fallback de 140 mm/m.
- Réserve utile = θ(-33 kPa) − θ(-1500 kPa), pédotransfert de Saxton & Rawls (2006),
  matière organique supposée à 1,5 % (hypothèse).

Sortie : data/processed/soil_{site}.parquet (une ligne par profondeur + « 0-30cm » pondérée).
Colonne `assumption` = True si la réserve utile est la valeur de secours (hypothèse).
"""

from __future__ import annotations

import logging

import pandas as pd

from ingest.http_cache import ApiError, CacheMiss, get_json
from ingest.sites import Site, save_parquet

logger = logging.getLogger(__name__)

SOILGRIDS_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
DEPTHS = ["0-5cm", "5-15cm", "15-30cm"]
THICKNESS_CM = {"0-5cm": 5, "5-15cm": 10, "15-30cm": 15}
FALLBACK_AWC_MM_M = 140.0
ORGANIC_MATTER_PCT = 1.5  # hypothèse (sols cultivés semi-arides)
# Décalages (deg) essayés si le point exact est masqué : ~1,1 km puis ~2,2 km puis ~4,4 km
OFFSETS = [(0.0, 0.0), (0.01, 0.0), (0.0, 0.01), (-0.01, 0.0), (0.0, -0.01),
           (0.02, 0.02), (-0.02, -0.02), (0.04, 0.0), (0.0, 0.04)]


def _query(lat: float, lon: float) -> dict[str, dict[str, float | None]]:
    """Retourne {propriété: {profondeur: valeur en %}} (None si masqué)."""
    params = [("lat", round(lat, 4)), ("lon", round(lon, 4)), ("value", "mean")]
    params += [("property", p) for p in ("clay", "sand", "silt")] + [("depth", d) for d in DEPTHS]
    data = get_json(SOILGRIDS_URL, params, f"soilgrids_{lat:.4f}_{lon:.4f}", ttl_hours=None)
    out: dict[str, dict[str, float | None]] = {}
    for layer in data["properties"]["layers"]:
        factor = layer["unit_measure"]["d_factor"]  # g/kg ÷ 10 → %
        out[layer["name"]] = {
            d["label"]: (None if d["values"]["mean"] is None else d["values"]["mean"] / factor)
            for d in layer["depths"]
        }
    return out


def _complete(props: dict[str, dict[str, float | None]]) -> bool:
    return all(props.get(p, {}).get(d) is not None for p in ("clay", "sand", "silt") for d in DEPTHS)


def saxton_rawls_awc(sand_pct: float, clay_pct: float, om_pct: float = ORGANIC_MATTER_PCT) -> float:
    """Réserve utile (mm/m) d'après Saxton & Rawls (2006)."""
    s, c, om = sand_pct / 100, clay_pct / 100, om_pct
    wp_t = -0.024 * s + 0.487 * c + 0.006 * om + 0.005 * s * om - 0.013 * c * om + 0.068 * s * c + 0.031
    wp = wp_t + (0.14 * wp_t - 0.02)
    fc_t = -0.251 * s + 0.195 * c + 0.011 * om + 0.006 * s * om - 0.027 * c * om + 0.452 * s * c + 0.299
    fc = fc_t + (1.283 * fc_t**2 - 0.374 * fc_t - 0.015)
    return max(fc - wp, 0.0) * 1000


def usda_texture(sand: float, silt: float, clay: float) -> str:
    """Classe texturale USDA (triangle des textures, en %)."""
    if silt + 1.5 * clay < 15:
        return "sable"
    if silt + 2 * clay < 30:
        return "sable limoneux"
    if (7 <= clay < 20 and sand > 52) or (clay < 7 and silt < 50):
        return "limon sableux"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "limon"
    if silt >= 80 and clay < 12:
        return "silt"
    if silt >= 50 and clay < 27:
        return "limon silteux"
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "limon argilo-sableux"
    if 27 <= clay < 40 and sand <= 20:
        return "limon argilo-silteux"
    if 27 <= clay < 40:
        return "limon argileux"
    if clay >= 35 and sand > 45:
        return "argile sableuse"
    if clay >= 40 and silt >= 40:
        return "argile silteuse"
    return "argile"


def fetch_soil(lat: float, lon: float) -> tuple[pd.DataFrame, float, float]:
    """Texture par profondeur au premier point non masqué. Retourne (df, lat, lon) utilisés."""
    for dlat, dlon in OFFSETS:
        plat, plon = lat + dlat, lon + dlon
        props = _query(plat, plon)
        if _complete(props):
            if (dlat, dlon) != (0.0, 0.0):
                logger.info("SoilGrids masqué au point exact ; point voisin utilisé (%.4f, %.4f)", plat, plon)
            rows = [{"depth": d, "clay_pct": props["clay"][d], "sand_pct": props["sand"][d],
                     "silt_pct": props["silt"][d], "thickness_cm": THICKNESS_CM[d]} for d in DEPTHS]
            return pd.DataFrame(rows), plat, plon
    raise CacheMiss("SoilGrids : aucune valeur dans le voisinage")


def run(site: Site) -> dict[str, pd.DataFrame]:
    """Texture + réserve utile du site → parquet (fallback 140 mm/m)."""
    try:
        df, plat, plon = fetch_soil(site.lat, site.lon)
        w = df["thickness_cm"]
        mean = {c: float((df[c] * w).sum() / w.sum()) for c in ("clay_pct", "sand_pct", "silt_pct")}
        df = pd.concat([df, pd.DataFrame([{"depth": "0-30cm", **mean, "thickness_cm": 30}])], ignore_index=True)
        df["texture"] = [usda_texture(r.sand_pct, r.silt_pct, r.clay_pct) for r in df.itertuples()]
        df["awc_mm_per_m"] = [round(saxton_rawls_awc(r.sand_pct, r.clay_pct), 1) for r in df.itertuples()]
        df["source"] = "soilgrids+saxton_rawls"
        df["query_lat"], df["query_lon"] = plat, plon
        df["assumption"] = False
    except (ApiError, CacheMiss, KeyError, ValueError) as exc:
        logger.warning("SoilGrids indisponible pour %s (%s) → réserve utile par défaut %.0f mm/m",
                       site.name, exc, FALLBACK_AWC_MM_M)
        df = pd.DataFrame([{"depth": "0-30cm", "clay_pct": None, "sand_pct": None, "silt_pct": None,
                            "thickness_cm": 30, "texture": "inconnue", "awc_mm_per_m": FALLBACK_AWC_MM_M,
                            "source": "fallback", "query_lat": site.lat, "query_lon": site.lon,
                            "assumption": True}])  # valeur d'hypothèse, pas une mesure
    df.insert(0, "site", site.key)
    save_parquet(df, f"soil_{site.key}")
    return {f"soil_{site.key}": df}
