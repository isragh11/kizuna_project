"""Contrat de données AquaMind (Pydantic v2).

Tous les modules (ingestion, optimiseur, streaming, API, frontend) se conforment
à ces schémas. Unités : m³, kWh, TND, heures locales 0..23.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GrowthStage = Literal["ini", "mid", "end"]
SensorKind = Literal["soil", "flow", "pv", "tank"]
Detector = Literal["iforest", "river"]
LimitingFactor = Literal["water", "energy", "pump", "none"]
IrrigationMethod = Literal["drip", "sprinkler", "flood"]

HOURS = 24


def _check_24(values: list[float], name: str) -> list[float]:
    """Vérifie qu'une série horaire contient exactement 24 valeurs."""
    if len(values) != HOURS:
        raise ValueError(f"{name} doit contenir {HOURS} valeurs (reçu {len(values)})")
    return values


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Crop(_Base):
    """Culture : coefficients FAO-56 (Kc) et FAO-33 (Ky), valeur de récolte."""

    name: str
    kc_ini: float = Field(gt=0)
    kc_mid: float = Field(gt=0)
    kc_end: float = Field(gt=0)
    ky: float = Field(gt=0, description="Coefficient de réponse du rendement à l'eau (FAO-33)")
    value_tnd_per_ha: float = Field(ge=0, description="Valeur de récolte (hypothèse)")


class Plot(_Base):
    """Parcelle irriguée."""

    id: str
    crop: Crop
    area_ha: float = Field(gt=0)
    growth_stage: GrowthStage
    irrigation_efficiency: float = Field(gt=0, le=1)
    irrigation_method: IrrigationMethod = "drip"
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Farm(_Base):
    """Exploitation : parcelles, pompage, solaire, quota et stockage."""

    id: str
    name: str
    plots: list[Plot] = Field(min_length=1)
    well_depth_m: float = Field(ge=0)
    pump_efficiency: float = Field(gt=0, le=1)
    pump_max_m3h: float = Field(gt=0)
    pv_peak_kw: float = Field(ge=0)
    water_quota_m3_day: float = Field(ge=0)
    tank_capacity_m3: float = Field(ge=0)

    @field_validator("plots")
    @classmethod
    def _unique_plot_ids(cls, plots: list[Plot]) -> list[Plot]:
        ids = [p.id for p in plots]
        if len(ids) != len(set(ids)):
            raise ValueError(f"identifiants de parcelles dupliqués : {ids}")
        return plots


class SensorReading(_Base):
    """Mesure d'un capteur IoT (message MQTT validé)."""

    ts: datetime
    kind: SensorKind
    source_id: str
    values: dict[str, float]


class Alert(_Base):
    """Alerte de fuite avec estimation des pertes (estimation simulée)."""

    ts: datetime
    section: str
    score: float
    detector: Detector
    est_loss_m3_day: float = Field(ge=0)
    est_loss_kwh_day: float = Field(ge=0)
    est_loss_tnd_day: float = Field(ge=0)
    message: str


class OptimizeRequest(_Base):
    """Paramètres d'un calcul de plan sur 24 h (scénario « Et si ? »)."""

    farm: Farm
    water_quota_m3: float = Field(ge=0)
    pv_scale: float = Field(default=1.0, ge=0, le=1.5)
    temp_offset_c: float = Field(default=0.0, ge=-10, le=10)
    well_depth_m: float | None = Field(default=None, ge=0)
    s_min: float = Field(default=0.0, ge=0, le=1)
    date: Date
    no_midday_sprinkler: bool = Field(
        default=True, description="Interdit l'aspersion entre 11h et 16h"
    )


class Duals(_Base):
    """Variables duales (prix fictifs) des contraintes de l'optimiseur."""

    water: float
    energy: list[float]
    pump: list[float]

    @model_validator(mode="after")
    def _lengths(self) -> "Duals":
        _check_24(self.energy, "duals.energy")
        _check_24(self.pump, "duals.pump")
        return self


class OptimizeResult(_Base):
    """Plan optimisé, comparaison avec la référence et explications."""

    allocation: dict[str, list[float]]
    grid_kwh: list[float]
    pv_kwh: list[float]
    pump_kwh: list[float]
    satisfaction: dict[str, float]
    duals: Duals
    kpis: dict[str, float]
    baseline_kpis: dict[str, float]
    explanations: list[str]
    limiting_factor: LimitingFactor
    solve_time_ms: float = Field(ge=0)

    @model_validator(mode="after")
    def _lengths(self) -> "OptimizeResult":
        _check_24(self.grid_kwh, "grid_kwh")
        _check_24(self.pv_kwh, "pv_kwh")
        _check_24(self.pump_kwh, "pump_kwh")
        for pid, series in self.allocation.items():
            _check_24(series, f"allocation[{pid}]")
        return self
