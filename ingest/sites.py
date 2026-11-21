"""Sites de démo (coordonnées lues depuis .env) et utilitaires de sortie."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from aquamind_core.config import PROCESSED_DIR, get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Site:
    """Site géographique : identifiant court utilisé dans les noms de fichiers."""

    key: str  # "tunisia" | "japan"
    name: str
    lat: float
    lon: float
    tz: str


def get_sites() -> dict[str, Site]:
    """Sidi Bouzid (ferme de démo) et Niigata (mode Japon)."""
    s = get_settings()
    return {
        "tunisia": Site("tunisia", "Sidi Bouzid", s.farm_lat, s.farm_lon, s.farm_tz),
        "japan": Site("japan", "Niigata", s.japan_lat, s.japan_lon, s.japan_tz),
    }


def save_parquet(df: pd.DataFrame, name: str) -> Path:
    """Écrit un DataFrame dans data/processed/<name>.parquet."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    path = PROCESSED_DIR / f"{name}.parquet"
    df.to_parquet(path)
    logger.info("Écrit %s (%d lignes)", path.relative_to(PROCESSED_DIR.parent.parent), len(df))
    return path
