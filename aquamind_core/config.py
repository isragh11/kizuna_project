"""Configuration : variables d'environnement (.env) et données statiques."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from aquamind_core.schemas import Crop, Farm

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = DATA_DIR / "cache"
STATIC_DIR = DATA_DIR / "static"
ARTIFACTS_DIR = ROOT_DIR / "models" / "artifacts"


class Settings(BaseSettings):
    """Paramètres lus depuis .env (valeurs par défaut = .env.example, sans secret)."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    influxdb_url: str = "http://localhost:8086"
    influxdb_org: str = "aquamind"
    influxdb_bucket: str = "farm"
    influxdb_token: str = ""

    # "sqlite" = secours si InfluxDB n'est pas disponible (consumer et /history)
    storage_backend: Literal["influx", "sqlite"] = "influx"
    sqlite_path: str = "data/stream.db"

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_base_topic: str = "aquamind/farm1"

    farm_lat: float = 34.98
    farm_lon: float = 9.50
    farm_tz: str = "Africa/Tunis"
    japan_lat: float = 37.90
    japan_lon: float = 139.02
    japan_tz: str = "Asia/Tokyo"

    sim_seconds_per_hour: float = 5.0
    offline_mode: bool = False

    @property
    def sqlite_file(self) -> Path:
        """Chemin absolu de la base SQLite de secours."""
        path = Path(self.sqlite_path)
        return path if path.is_absolute() else ROOT_DIR / path


@lru_cache
def get_settings() -> Settings:
    """Retourne les paramètres (mis en cache)."""
    settings = Settings()
    if settings.storage_backend == "influx" and not settings.influxdb_token:
        logger.warning("INFLUXDB_TOKEN absent : copier .env.example en .env")
    return settings


def _read_json(name: str) -> dict[str, Any]:
    with open(STATIC_DIR / name, encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def load_economics() -> dict[str, Any]:
    """Prix de récolte et tarif électrique (valeurs d'hypothèse)."""
    return _read_json("economics.json")


@lru_cache
def load_crops() -> dict[str, Crop]:
    """Cultures : Kc/Ky (crops.json) fusionnés avec la valeur de récolte (economics.json)."""
    crops = _read_json("crops.json")["crops"]
    values = load_economics()["crop_value_tnd_per_ha"]
    return {
        key: Crop(
            name=c["name"],
            kc_ini=c["kc_ini"],
            kc_mid=c["kc_mid"],
            kc_end=c["kc_end"],
            ky=c["ky"],
            value_tnd_per_ha=values[key],
        )
        for key, c in crops.items()
    }


def load_farm(name: str = "farm.json") -> Farm:
    """Charge une ferme ; chaque parcelle référence une culture par sa clé."""
    raw = _read_json(name)
    crops = load_crops()
    plots = [{**p, "crop": crops[p["crop"]]} for p in raw["plots"]]
    return Farm.model_validate({**raw, "plots": plots})


def hourly_electricity_price() -> list[float]:
    """Tarif réseau horaire (TND/kWh) sur 24 h, à partir des plages d'economics.json."""
    tariff = load_economics()["electricity_tariff"]
    prices = [tariff["periods"][tariff["default_period"]]["price_tnd_per_kwh"]] * 24
    for period in tariff["periods"].values():
        for start, end in period.get("hours", []):
            for h in range(start, end):
                prices[h] = period["price_tnd_per_kwh"]
    return prices
