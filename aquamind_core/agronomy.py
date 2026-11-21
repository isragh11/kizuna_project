"""Agronomie : coefficients culturaux (FAO-56), besoins bruts et ET0 de secours (Hargreaves).

Unités : mm/j pour l'ET, m³ pour les volumes. 1 mm sur 1 ha = 10 m³.
"""

from __future__ import annotations

import math

from aquamind_core.schemas import Crop, GrowthStage, Plot

M3_PER_MM_HA = 10.0
EFFECTIVE_RAIN_FRACTION = 0.8


def kc(crop: Crop, stage: GrowthStage) -> float:
    """Coefficient cultural Kc de la culture au stade donné (FAO-56, tableau 12)."""
    return {"ini": crop.kc_ini, "mid": crop.kc_mid, "end": crop.kc_end}[stage]


def etc(et0_mm: float, kc_value: float) -> float:
    """Évapotranspiration de la culture ETc = Kc × ET0 (mm/j)."""
    return max(0.0, et0_mm) * kc_value


def gross_need_m3(etc_mm: float, area_ha: float, efficiency: float, rain_mm: float = 0.0) -> float:
    """Besoin brut en eau d'irrigation (m³) : (ETc − pluie efficace) × surface × 10 / efficience.

    Pluie efficace = 0,8 × pluie. Le besoin ne peut pas être négatif."""
    if efficiency <= 0:
        raise ValueError("l'efficience d'irrigation doit être > 0")
    net_mm = max(0.0, etc_mm - EFFECTIVE_RAIN_FRACTION * max(0.0, rain_mm))
    return net_mm * area_ha * M3_PER_MM_HA / efficiency


def plot_kc(plot: Plot) -> float:
    """Kc effectif : celui du calendrier cultural s'il est connu, sinon celui du stade."""
    return plot.kc_value if plot.kc_value is not None else kc(plot.crop, plot.growth_stage)


def plot_need_m3(plot: Plot, et0_mm: float, rain_mm: float = 0.0) -> float:
    """Besoin brut d'une parcelle pour une journée (m³)."""
    return gross_need_m3(etc(et0_mm, plot_kc(plot)), plot.area_ha, plot.irrigation_efficiency, rain_mm)


def m3_per_mm_et0(plot: Plot) -> float:
    """m³ de besoin brut par mm d'ET0 (sans pluie) : sert à traduire une erreur d'ET0 en m³."""
    return plot_kc(plot) * plot.area_ha * M3_PER_MM_HA / plot.irrigation_efficiency


def daily_crop_value_tnd(plot: Plot) -> float:
    """Valeur de récolte de la parcelle répartie sur la saison irriguée (TND par jour, hypothèse)."""
    return plot.crop.value_tnd_per_ha * plot.area_ha / plot.crop.season_days


def extraterrestrial_radiation(lat_deg: float, doy: int) -> float:
    """Rayonnement extraterrestre Ra (MJ/m²/j), FAO-56 éq. 21."""
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    delta = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    return 24 * 60 / math.pi * 0.0820 * dr * (
        ws * math.sin(phi) * math.sin(delta) + math.cos(phi) * math.cos(delta) * math.sin(ws)
    )


def hargreaves_et0(tmax_c: float, tmin_c: float, lat_deg: float, doy: int) -> float:
    """ET0 de Hargreaves-Samani (mm/j), FAO-56 éq. 52 : secours quand il n'y a ni modèle ni prévision."""
    ra = extraterrestrial_radiation(lat_deg, doy)
    return 0.0023 * 0.408 * ra * ((tmax_c + tmin_c) / 2 + 17.8) * math.sqrt(max(0.0, tmax_c - tmin_c))


def temperature_adjusted_et0(et0_mm: float, tmean_c: float, offset_c: float) -> float:
    """Ajuste l'ET0 à un écart de température (scénario « Et si ? »).

    Sensibilité de Hargreaves : ET0 ∝ (T + 17,8). Approximation de premier ordre."""
    if offset_c == 0:
        return et0_mm
    base = tmean_c + 17.8
    if base <= 0:
        return et0_mm
    return max(0.0, et0_mm * (base + offset_c) / base)
