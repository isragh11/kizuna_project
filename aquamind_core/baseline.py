"""Irrigation traditionnelle (référence de comparaison).

Règle : chaque jour, la pompe démarre à 7h à plein débit et envoie l'eau directement aux parcelles,
en doses égales, jusqu'à épuisement du quota journalier ; le réservoir n'est pas utilisé.
Les panneaux solaires existent aussi chez l'agriculteur traditionnel : l'énergie vient d'abord du
solaire produit à cette heure-là, le reste du réseau (référence prudente, pas un épouvantail).
"""

from __future__ import annotations

from dataclasses import dataclass

START_HOUR = 7


@dataclass
class BaselinePlan:
    """Résultat horaire de la référence traditionnelle."""

    allocation: dict[str, list[float]]
    pump_m3: list[float]
    pv_kwh: list[float]
    grid_kwh: list[float]
    pump_kwh: list[float]


def traditional_plan(plot_ids: list[str], horizon_h: int, quota_m3_day: float, pump_max_m3h: float,
                     kwh_per_m3: float, pv_available: list[float],
                     start_hour: int = START_HOUR) -> BaselinePlan:
    """Plan traditionnel sur l'horizon : 7h, plein débit, doses égales, jusqu'au quota du jour."""
    n = len(plot_ids)
    alloc = {pid: [0.0] * horizon_h for pid in plot_ids}
    pump = [0.0] * horizon_h
    for day_start in range(0, horizon_h, 24):
        remaining = quota_m3_day
        for h in range(start_hour, 24):
            if remaining <= 1e-9:
                break
            q = min(pump_max_m3h, remaining)
            remaining -= q
            pump[day_start + h] = q
            for pid in plot_ids:
                alloc[pid][day_start + h] = q / n
    pump_kwh = [kwh_per_m3 * q for q in pump]
    pv_used = [min(e, pv) for e, pv in zip(pump_kwh, pv_available)]
    grid = [e - s for e, s in zip(pump_kwh, pv_used)]
    return BaselinePlan(alloc, pump, pv_used, grid, pump_kwh)
