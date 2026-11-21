"""Tests de la Phase 4 : agronomie, énergie, prévision, optimiseur, référence, explications."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from aquamind_core.agronomy import gross_need_m3, hargreaves_et0, kc, plot_need_m3
from aquamind_core.config import load_farm
from aquamind_core.energy import pump_kwh_per_m3, pv_hourly_kwh
from aquamind_core.forecast import build_features, et0_mae, load_archive, load_et0_model
from aquamind_core.optimizer import optimize
from aquamind_core.schemas import OptimizeRequest

TOL = 1e-3


def request(**kw) -> OptimizeRequest:
    """Requête sans réseau : météo donnée explicitement (2 jours d'été moyens par défaut)."""
    farm = load_farm()
    base = dict(farm=farm, date=date(2025, 7, 6), water_quota_m3=farm.water_quota_m3_day,
                et0_mm=[7.0, 7.0], rain_mm=[0.0, 0.0], tmean_c=[30.0, 30.0], tmax_c=[33.0, 33.0],
                pv_day_factors=[1.0, 1.0])
    base.update(kw)
    return OptimizeRequest(**base)


# --------------------------------------------------------------------- agronomie / énergie

def test_need_and_energy_formulas():
    assert gross_need_m3(5.0, 1.0, 1.0) == pytest.approx(50.0)
    assert gross_need_m3(5.0, 1.0, 1.0, rain_mm=10.0) == 0.0          # pluie efficace 8 mm > 5 mm
    assert gross_need_m3(5.0, 0.5, 0.9, rain_mm=1.0) == pytest.approx((5 - 0.8) * 5 / 0.9)
    assert pump_kwh_per_m3(60, 0.45) == pytest.approx(9.81 * 60 / (3600 * 0.45) * 1.1)
    assert pv_hourly_kwh([0.0, 0.5], 5.0, 0.5) == [0.0, 1.25]
    farm = load_farm()
    tomato = farm.plots[0]
    assert kc(tomato.crop, "mid") == 1.15
    assert plot_need_m3(tomato, 1.0) == pytest.approx(1.15 * 0.5 * 10 / 0.9)
    assert 5 < hargreaves_et0(35, 20, 34.98, 200) < 9                  # ordre de grandeur estival


# --------------------------------------------------------------------- prévision

@pytest.mark.parametrize("site", ["tunisia", "japan"])
def test_forecast_reproduces_notebook_test_mae(site):
    """Les variables reconstruites par forecast.py redonnent la MAE de test du notebook 02."""
    arch = load_archive(site)
    feats = build_features(arch, site)
    y = arch["et0_fao_evapotranspiration"].shift(-1).reindex(feats.index)
    target = feats.index + pd.Timedelta(days=1)
    mask = (target >= "2024-01-01") & (target <= "2025-12-31") & y.notna()
    X = feats[mask].dropna()
    mae = float(np.mean(np.abs(load_et0_model().predict(X) - y[X.index])))
    assert mae == pytest.approx(et0_mae(site), abs=5e-4)


def test_leak_artifacts_load():
    from aquamind_core.leaks import load_iforest, load_leak_threshold, load_river_config, make_river

    assert hasattr(load_iforest(), "score_samples")
    assert load_leak_threshold()["use_for_alerting"] is False
    assert load_river_config()["recommended_mode"] == "rule_adaptive"
    model = make_river()
    model.learn_one({"imbalance": 0.5})
    assert 0.0 <= model.score_one({"imbalance": 0.5}) <= 1.0


# --------------------------------------------------------------------- optimiseur : critères du prompt

def test_zero_quota_gives_zero_allocation():
    res = optimize(request(water_quota_m3=0.0))
    assert sum(sum(v) for v in res.allocation.values()) == pytest.approx(0.0, abs=TOL)
    assert sum(res.pump_m3) == pytest.approx(0.0, abs=TOL)


def test_zero_pv_goes_through_grid():
    res = optimize(request(pv_scale=0.0))
    assert sum(res.pv_kwh) == pytest.approx(0.0, abs=TOL)
    assert sum(res.grid_kwh) == pytest.approx(sum(res.pump_kwh), abs=TOL)
    assert sum(res.pump_kwh) > 0


def test_demand_below_quota_gives_full_satisfaction():
    res = optimize(request(et0_mm=[2.0, 2.0]))   # besoin ≈ 36 m³/j < 60
    assert all(s == pytest.approx(1.0, abs=TOL) for s in res.satisfaction.values())
    assert res.limiting_factor == "none"


def test_solve_time_under_two_seconds():
    res = optimize(request())
    assert res.solve_time_ms < 2000


# --------------------------------------------------------------------- optimiseur : physique et priorités

def test_tank_balance_energy_and_quota():
    req = request()
    res = optimize(req)
    cap = req.farm.tank_capacity_m3
    level = 0.5 * cap
    for t in range(res.horizon_h):
        out = sum(res.allocation[i][t] for i in res.allocation)
        level = level + res.pump_m3[t] - out
        assert res.tank_m3[t] == pytest.approx(level, abs=0.02)
        assert -TOL <= res.tank_m3[t] <= cap + TOL
        assert res.pump_m3[t] <= req.farm.pump_max_m3h + TOL
        assert res.pump_kwh[t] == pytest.approx(res.pv_kwh[t] + res.grid_kwh[t], abs=0.01)
        assert res.pv_kwh[t] <= res.pv_available_kwh[t] + TOL
    assert res.tank_m3[-1] >= 0.5 * cap - TOL                      # niveau final ≥ initial
    assert sum(res.pump_m3) <= 2 * req.water_quota_m3 + 0.05     # somme de 48 valeurs arrondies


def test_daily_quota_mode_respected_each_day():
    res = optimize(request(quota_mode="daily"))
    for d in range(2):
        assert sum(res.pump_m3[d * 24:(d + 1) * 24]) <= 60 + TOL


def test_midday_sprinkler_ban():
    # stades fixes de farm.json : C = blé en aspersion (le calendrier mettrait du piment en juillet)
    res = optimize(request(et0_mm=[2.0, 2.0], use_crop_calendar=False))
    assert sum(res.allocation["C"]) > 1
    for d in range(2):
        assert all(res.allocation["C"][d * 24 + h] == pytest.approx(0.0, abs=TOL) for h in range(11, 16))


def test_tranches_share_water_in_scarcity_and_are_explained():
    """Forte pénurie : chaque parcelle reçoit au moins son palier de survie (30 %) avant tout confort."""
    res = optimize(request(et0_mm=[10.0, 10.0], tmax_c=[44.0, 44.0]))
    assert res.limiting_factor == "water" and res.duals.water > 0
    assert all(s >= 0.29 for s in res.satisfaction.values())
    assert max(res.satisfaction.values()) < 0.99                    # personne n'a tout
    for info in res.plots.values():                                 # paliers remplis dans l'ordre
        fill = info["tranche_fill"]
        assert all(fill[k] >= fill[k + 1] - 1e-6 for k in range(len(fill) - 1))
    text = " ".join(res.explanations)
    for i in res.plots:
        assert f"Parcelle {i} ({res.plots[i]['crop']}) RATIONNÉE" in text
    assert "paliers" in text and "survie" in text and "Pourquoi" in text
    assert "arbre" in text                                          # olivier pérenne


def test_linear_value_would_starve_plots_but_tranches_do_not(monkeypatch):
    """Comparaison avec l'ancienne valeur linéaire : un seul palier = tout à la parcelle la plus rentable."""
    import aquamind_core.optimizer as opt

    req = request(et0_mm=[10.0, 10.0], tmax_c=[44.0, 44.0])
    shared = optimize(req)
    linear_tranches = {"bounds": [1.0], "value_shares": [1.0], "labels": ["tout"],
                       "perennial_survival_factor": 1.0}
    monkeypatch.setattr(opt, "load_value_tranches", lambda: linear_tranches)
    linear = optimize(req)
    assert min(linear.satisfaction.values()) < 0.05                 # une parcelle sacrifiée
    assert min(shared.satisfaction.values()) >= 0.29


def test_crop_calendar_by_date():
    from aquamind_core.crop_calendar import apply_calendar

    summer = {p.id: p for p in apply_calendar(load_farm(), date(2025, 7, 20)).plots}
    winter = {p.id: p for p in apply_calendar(load_farm(), date(2026, 1, 15)).plots}
    assert summer["C"].crop.name == "Piment" and summer["C"].irrigation_method == "drip"
    assert winter["C"].crop.name == "Blé" and winter["C"].irrigation_method == "sprinkler"
    assert summer["A"].crop.name == "Tomate" and summer["A"].kc_value == pytest.approx(1.15)
    assert winter["A"].kc_value == 0.0 and "jachère" in winter["A"].season_note
    assert winter["B"].growth_stage == "ini" and summer["B"].growth_stage == "mid"
    res = optimize(request(date=date(2026, 1, 15)))
    assert res.plots["A"]["need_m3"] == 0.0
    assert any("jachère" in e for e in res.explanations)


def test_hot_day_margin_is_increased():
    cool = optimize(request(tmax_c=[30.0, 30.0]))
    hot = optimize(request(tmax_c=[30.0, 40.0]))
    base = et0_mae("tunisia")
    assert cool.inputs["safety_margin_mm"] == pytest.approx([base, base], abs=1e-3)
    assert hot.inputs["safety_margin_mm"] == pytest.approx([base, 1.5 * base], abs=1e-3)
    assert any("jours chauds" in e for e in hot.explanations)


def test_infeasible_smin_is_relaxed_and_reported():
    res = optimize(request(et0_mm=[9.0, 9.0], s_min=0.9))
    assert any("relâché" in e for e in res.explanations)


def test_safety_margin_defaults_to_model_error():
    res = optimize(request())
    assert res.inputs["safety_margin_mm"][0] == pytest.approx(et0_mae("tunisia"), abs=1e-3)
    no_margin = optimize(request(et0_mm=[2.0, 2.0], safety_margin_mm=0.0))
    with_margin = optimize(request(et0_mm=[2.0, 2.0]))
    assert sum(with_margin.pump_m3) > sum(no_margin.pump_m3)       # quota non limitant : marge servie


def test_cloudy_day_two_uses_tank_to_anticipate():
    """J2 très nuageux et quota non limitant : on pompe plus le J1 au solaire et on stocke."""
    res = optimize(request(et0_mm=[3.0, 3.0], pv_day_factors=[1.0, 0.1]))
    day1_pump = sum(res.pump_m3[:24])
    day1_irr = sum(sum(v[:24]) for v in res.allocation.values())
    assert day1_pump > day1_irr + 1
    assert res.tank_m3[23] > res.tank_m3[0]


def test_baseline_starts_at_7h_with_equal_doses():
    res = optimize(request())
    assert res.baseline_kpis["water_pumped_m3"] == pytest.approx(120.0, abs=TOL)
    assert "kWh" in res.explanations[-2]
    assert res.kpis["grid_kwh_avoided"] == pytest.approx(
        res.baseline_kpis["grid_kwh"] - res.kpis["grid_kwh"], abs=0.02)
