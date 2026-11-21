"""Contrat de données AquaMind (Pydantic v2).

Tous les modules (ingestion, optimiseur, streaming, API, frontend) se conforment
à ces schémas. Unités : m³, kWh, TND, heures locales 0..23.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

GrowthStage = Literal["ini", "mid", "end"]
SensorKind = Literal["soil", "flow", "pv", "tank"]
Detector = Literal["iforest", "river", "rule_adaptive"]
LimitingFactor = Literal["water", "energy", "pump", "none"]
IrrigationMethod = Literal["drip", "sprinkler", "flood"]
SiteKey = Literal["tunisia", "japan"]
QuotaMode = Literal["horizon", "daily"]

HOURS = 24
HORIZONS = (24, 48)


def _check_len(values: list[float], n: int, name: str) -> list[float]:
    """Vérifie qu'une série horaire contient exactement n valeurs (n = horizon en heures)."""
    if len(values) != n:
        raise ValueError(f"{name} doit contenir {n} valeurs (reçu {len(values)})")
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
    season_days: int = Field(
        default=150, gt=0, description="Durée de la saison irriguée (j) : répartit la valeur par jour"
    )
    perennial: bool = Field(default=False, description="Culture pérenne : le palier de survie protège l'arbre")


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
    kc_value: float | None = Field(
        default=None, ge=0, description="Kc du jour (calendrier cultural) ; sinon Kc du stade"
    )
    season_note: str | None = Field(default=None, description="ex. « mi-saison, jour 85/135 », « jachère »")


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
    alert_id: str | None = None
    est_loss_m3h: float | None = Field(default=None, ge=0, description="débit perdu estimé")
    confirmed_by_ai: bool = Field(default=False, description="River confirme (indice, ne bloque jamais)")
    river_score: float | None = None


class OptimizeRequest(_Base):
    """Paramètres d'un calcul de plan sur 24 ou 48 h (scénario « Et si ? »).

    Les champs météo optionnels (et0_mm, rain_mm, tmean_c, pv_day_factors : une valeur par jour)
    remplacent la prévision ; s'ils sont absents, ils sont lus par aquamind_core.forecast."""

    farm: Farm
    water_quota_m3: float = Field(ge=0, description="Quota d'eau du puits, m³ par jour")
    site: SiteKey = "tunisia"
    horizon_h: int = Field(default=48, description="24 ou 48 h")
    pv_scale: float = Field(default=1.0, ge=0, le=1.5)
    temp_offset_c: float = Field(default=0.0, ge=-10, le=10)
    well_depth_m: float | None = Field(default=None, ge=0)
    s_min: float = Field(default=0.0, ge=0, le=1)
    date: Date
    no_midday_sprinkler: bool = Field(
        default=True, description="Interdit l'aspersion entre 11h et 16h"
    )
    crop_value_factors: dict[str, float] | None = Field(
        default=None, description="Multiplie la valeur de récolte d'une culture (nom → facteur), « Et si ? »"
    )
    use_crop_calendar: bool = Field(
        default=True, description="Culture active et stade déduits de la date (crop_calendar.json)"
    )
    hot_day_tmax_c: float = Field(default=35.0, description="Seuil de jour chaud pour la marge")
    hot_day_margin_factor: float = Field(
        default=1.5, ge=1.0, description="Marge × ce facteur si Tmax > seuil (sous-estimation des extrêmes)"
    )
    quota_mode: QuotaMode = Field(
        default="horizon",
        description="horizon : quota × nb de jours sur l'horizon (le réservoir permet d'anticiper) ; "
        "daily : quota strict chaque jour",
    )
    tank_initial_m3: float | None = Field(default=None, ge=0, description="défaut : 50 % de la cuve")
    safety_margin_mm: float | None = Field(
        default=None, ge=0, description="marge sur l'ET0 (mm/j) ; défaut : MAE du modèle ET0 du site"
    )
    grid_max_kw: float | None = Field(default=None, ge=0, description="puissance réseau maximale")
    et0_mm: list[float] | None = None
    rain_mm: list[float] | None = None
    tmean_c: list[float] | None = None
    tmax_c: list[float] | None = None
    pv_day_factors: list[float] | None = Field(
        default=None, description="ensoleillement de chaque jour / moyenne du mois (1 = normal)"
    )

    @model_validator(mode="after")
    def _check_horizon(self) -> "OptimizeRequest":
        if self.horizon_h not in HORIZONS:
            raise ValueError(f"horizon_h doit valoir 24 ou 48 (reçu {self.horizon_h})")
        days = self.horizon_h // HOURS
        for name in ("et0_mm", "rain_mm", "tmean_c", "tmax_c", "pv_day_factors"):
            values = getattr(self, name)
            if values is not None and len(values) != days:
                raise ValueError(f"{name} doit contenir {days} valeur(s), une par jour")
        if self.tank_initial_m3 is not None and self.tank_initial_m3 > self.farm.tank_capacity_m3:
            raise ValueError("tank_initial_m3 dépasse la capacité du réservoir")
        return self


class Duals(_Base):
    """Variables duales (prix fictifs, TND par unité) des contraintes de l'optimiseur.

    water : valeur d'un m³ de quota en plus ; energy[t] : valeur d'un kWh en plus à l'heure t ;
    pump[t] : valeur d'un m³/h de capacité de pompe en plus ; tank : valeur d'un m³ de cuve en plus."""

    water: float
    energy: list[float]
    pump: list[float]
    tank: float = 0.0

    @model_validator(mode="after")
    def _lengths(self) -> "Duals":
        _check_len(self.pump, len(self.energy), "duals.pump")
        return self


class OptimizeResult(_Base):
    """Plan optimisé (horizon 24 ou 48 h), comparaison avec la référence et explications.

    allocation : m³ du réservoir vers chaque parcelle, heure par heure ; pump_m3 : m³ pompés du
    puits vers le réservoir ; tank_m3 : niveau de la cuve en fin d'heure."""

    horizon_h: int = 24
    allocation: dict[str, list[float]]
    pump_m3: list[float] | None = None
    tank_m3: list[float] | None = None
    pv_available_kwh: list[float] | None = None
    grid_kwh: list[float]
    pv_kwh: list[float]
    pump_kwh: list[float]
    satisfaction: dict[str, float]
    plots: dict[str, dict[str, Any]] = Field(default_factory=dict)
    duals: Duals
    kpis: dict[str, float]
    baseline_kpis: dict[str, float]
    explanations: list[str]
    limiting_factor: LimitingFactor
    solve_time_ms: float = Field(ge=0)
    inputs: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _lengths(self) -> "OptimizeResult":
        n = self.horizon_h
        if n not in HORIZONS:
            raise ValueError(f"horizon_h doit valoir 24 ou 48 (reçu {n})")
        for name in ("grid_kwh", "pv_kwh", "pump_kwh", "pump_m3", "tank_m3", "pv_available_kwh"):
            values = getattr(self, name)
            if values is not None:
                _check_len(values, n, name)
        for pid, series in self.allocation.items():
            _check_len(series, n, f"allocation[{pid}]")
        _check_len(self.duals.energy, n, "duals.energy")
        return self
