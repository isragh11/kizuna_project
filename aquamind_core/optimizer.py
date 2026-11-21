"""Optimiseur eau–énergie (programme linéaire PuLP/CBC), horizon 24 ou 48 h.

Variables (t = heure de l'horizon, i = parcelle, d = jour, k = palier) :
  p[t]      m³ pompés du puits vers le réservoir        0 ≤ p[t]
  x[i,t]    m³ envoyés du réservoir vers la parcelle i  0 ≤ x[i,t]
  L[t]      niveau du réservoir en fin d'heure (m³)
  u[t]      kWh solaires utilisés                        0 ≤ u[t] ≤ pv[t]
  g[t]      kWh achetés au réseau                        0 ≤ g[t] (≤ grid_max si précisé)
  b[i,d,k]  m³ servis dans le palier k du besoin prévu  0 ≤ b ≤ (borne_k − borne_k-1)·D[i,d]
  m[i,d]    m³ servis sur la marge de sécurité           0 ≤ m ≤ M[i,d]

Paliers (crops.json, hypothèse) : 0–30 % du besoin = survie, 30–70 % = moyen, 70–100 % = confort.
Chaque palier porte une part de la valeur de récolte du jour (50 / 35 / 15 %) : la valeur par m³
décroît d'un palier à l'autre (FAO-33 rendu concave), ce qui reste un programme LINÉAIRE et fait
partager l'eau entre parcelles au lieu de tout donner à la plus rentable. Pour une culture pérenne
(olivier), le palier de survie vaut 5 fois plus : sans eau, l'arbre dépérit.

Contraintes nommées (leurs duals donnent les prix fictifs) :
  pump_t      p[t] ≤ débit max de la pompe
  energy_t    e·p[t] = u[t] + g[t]                      (e = kWh/m³ de pompage)
  balance_t   L[t] = L[t-1] + p[t] − Σi x[i,t]
  tankcap_t   L[t] ≤ capacité ;  final : L[H-1] ≥ L[-1] (niveau final ≥ niveau initial)
  outlet_t    Σi x[i,t] ≤ débit max du réseau (= débit pompe)
  deliver_i_d Σ_{t∈d} x[i,t] = Σk b[i,d,k] + m[i,d]
  water       Σt p[t] ≤ quota × jours   (ou un quota par jour si quota_mode = "daily")
  smin_i_d    Σk b[i,d,k] + écart ≥ s_min·D[i,d]        (souple : écart très pénalisé, signalé)

Objectif (TND) : max Σ c[i,d,k]·b[i,d,k] + ½ c[i,d,confort]·m[i,d] − Σ prix[t]·g[t] − ε_t·Σ p[t]
  c[i,d,k] = part_k × Ky_i × valeur journalière_i / (largeur_k × D[i,d])  (× 5 en survie si pérenne)
  ε_t : usure + très léger coût croissant avec t (pomper tôt à valeur égale).

Marge de sécurité : MAE du modèle ET0 du site, × 1,5 les jours où Tmax > 35 °C. Sur le test
2024–2025 en Tunisie, le modèle sous-estime l'ET0 de 0,32 mm/j en moyenne ces jours-là (contre
0,08 mm/j les autres jours) pour une MAE de 0,64 : 1,5 × 0,61 ≈ 0,64 + 0,32.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import pulp

from aquamind_core.agronomy import (
    daily_crop_value_tnd,
    m3_per_mm_et0,
    plot_kc,
    plot_need_m3,
    temperature_adjusted_et0,
)
from aquamind_core.baseline import traditional_plan
from aquamind_core.config import hourly_electricity_price, load_value_tranches
from aquamind_core.crop_calendar import apply_calendar
from aquamind_core.energy import pump_kwh_per_m3, pv_horizon_kwh
from aquamind_core.explain import build_explanations
from aquamind_core.forecast import PlanInputs, archive_inputs, et0_mae, live_inputs
from aquamind_core.schemas import Duals, Farm, OptimizeRequest, OptimizeResult

logger = logging.getLogger(__name__)

HOURS = 24
MARGIN_VALUE_FRACTION = 0.5
WEAR_TND_PER_M3 = 0.001          # usure de la pompe : évite de pomper pour rien
MIDDAY_TND_PER_M3 = 0.002        # évaporation : préférer irriguer hors 11h–16h, à valeur égale
LATE_PUMP_TND_PER_M3 = 0.0005    # à valeur égale, pomper tôt : garde une réserve dans la cuve
SMIN_PENALTY_TND_PER_M3 = 1000.0
MIN_DUAL_TND = 0.01              # en dessous, un prix fictif n'indique pas une ressource limitante
MIDDAY_HOURS = range(11, 16)     # 11h00–15h59
EPS = 1e-6
ARCHIVE_LAST_DAY = "2025-12-31"
HOT_DAY_EVIDENCE = ("sur 2024–2025 en Tunisie, quand Tmax > 35 °C, le modèle sous-estime l'ET0 de "
                    "0,32 mm/j en moyenne (0,08 mm/j les autres jours) ; ex. 8,0 mm prévus contre 9,8 mm "
                    "observés le 20/07/2025")


@dataclass
class PlanContext:
    """Tout ce qu'il faut pour expliquer le plan (passé à explain.build_explanations)."""

    request: OptimizeRequest
    farm: Farm
    inputs: PlanInputs
    horizon_h: int
    kwh_per_m3: float
    margin_mm: list[float]
    base_margin_mm: float
    hot_days: list[bool]
    quota_total_m3: float
    tank_initial_m3: float
    et0_used: list[float]
    need: dict[str, list[float]]
    margin: dict[str, list[float]]
    tranche_coef: dict[str, list[list[float]]]     # [jour][palier] TND/m³
    tranche_cap: dict[str, list[list[float]]]      # [jour][palier] m³
    tranche_labels: list[str]
    tranche_bounds: list[float]
    pv_available: list[float]
    price: list[float]
    sprinkler_plots: list[str]
    smin_relaxed: dict[str, float] = field(default_factory=dict)


def resolve_inputs(req: OptimizeRequest) -> PlanInputs:
    """Météo de l'horizon : valeurs de la requête, sinon archive (dates ≤ 2025) ou prévision en direct."""
    days = req.horizon_h // HOURS
    if None in (req.et0_mm, req.rain_mm, req.tmean_c, req.pv_day_factors):
        if req.date.isoformat() <= ARCHIVE_LAST_DAY:
            base = archive_inputs(req.site, req.date, days)
        else:
            base = live_inputs(req.site, req.date, days)
    else:
        base = PlanInputs(req.site, req.date, [], [], [], [], ["requête"] * days, [])
    tmean = list(req.tmean_c) if req.tmean_c is not None else base.tmean_c
    tmax = (list(req.tmax_c) if req.tmax_c is not None
            else base.tmax_c if base.tmax_c else list(tmean))
    return PlanInputs(
        site=req.site, start=req.date,
        et0_mm=list(req.et0_mm) if req.et0_mm is not None else base.et0_mm,
        rain_mm=list(req.rain_mm) if req.rain_mm is not None else base.rain_mm,
        tmean_c=tmean,
        pv_day_factors=(list(req.pv_day_factors) if req.pv_day_factors is not None
                        else base.pv_day_factors),
        sources=["requête"] * days if req.et0_mm is not None else base.sources,
        tmax_c=tmax,
    )


def _scale_crop_values(farm: Farm, factors: dict[str, float]) -> Farm:
    """Applique des facteurs à la valeur de récolte (clé = nom de la culture, insensible à la casse)."""
    norm = {k.lower(): v for k, v in factors.items()}
    plots = []
    for plot in farm.plots:
        f = norm.get(plot.crop.name.lower(), 1.0)
        crop = plot.crop.model_copy(update={"value_tnd_per_ha": plot.crop.value_tnd_per_ha * f})
        plots.append(plot.model_copy(update={"crop": crop}))
    return farm.model_copy(update={"plots": plots})


def _value(v: Any) -> float:
    x = pulp.value(v)
    return float(x) if x is not None else 0.0


def optimize(req: OptimizeRequest, inputs: PlanInputs | None = None) -> OptimizeResult:
    """Calcule le plan optimal d'eau et d'énergie, la référence traditionnelle et les explications."""
    t0 = time.perf_counter()
    inputs = inputs or resolve_inputs(req)
    if not inputs.tmax_c:
        inputs.tmax_c = list(inputs.tmean_c)
    farm = apply_calendar(req.farm, req.date) if req.use_crop_calendar else req.farm
    if req.crop_value_factors:
        farm = _scale_crop_values(farm, req.crop_value_factors)
    H = req.horizon_h
    n_days = H // HOURS
    plots = {p.id: p for p in farm.plots}
    ids = list(plots)

    depth = req.well_depth_m if req.well_depth_m is not None else farm.well_depth_m
    e = pump_kwh_per_m3(depth, farm.pump_efficiency)
    base_margin = req.safety_margin_mm if req.safety_margin_mm is not None else et0_mae(req.site)
    hot = [inputs.tmax_c[d] + req.temp_offset_c > req.hot_day_tmax_c for d in range(n_days)]
    margin_mm = [base_margin * (req.hot_day_margin_factor if hot[d] else 1.0) for d in range(n_days)]
    et0_used = [temperature_adjusted_et0(inputs.et0_mm[d], inputs.tmean_c[d], req.temp_offset_c)
                for d in range(n_days)]
    need = {i: [plot_need_m3(plots[i], et0_used[d], inputs.rain_mm[d]) for d in range(n_days)]
            for i in ids}
    margin = {i: [margin_mm[d] * m3_per_mm_et0(plots[i]) if need[i][d] > EPS else 0.0
                  for d in range(n_days)] for i in ids}

    tr = load_value_tranches()
    bounds = [0.0, *tr["bounds"]]
    widths = [bounds[k + 1] - bounds[k] for k in range(len(tr["bounds"]))]
    K = range(len(widths))
    coef: dict[str, list[list[float]]] = {}
    caps: dict[str, list[list[float]]] = {}
    for i in ids:
        plot = plots[i]
        day_value = plot.crop.ky * daily_crop_value_tnd(plot)
        coef[i], caps[i] = [], []
        for d in range(n_days):
            D = need[i][d]
            caps[i].append([widths[k] * D for k in K])
            if D <= EPS:
                coef[i].append([0.0 for _ in K])
                continue
            row = []
            for k in K:
                c = tr["value_shares"][k] * day_value / (widths[k] * D)
                if k == 0 and plot.crop.perennial:
                    c *= tr["perennial_survival_factor"]
                row.append(c)
            coef[i].append(row)

    pv = pv_horizon_kwh(req.site, req.date.month, farm.pv_peak_kw, inputs.pv_day_factors,
                        req.pv_scale)
    price = (hourly_electricity_price() * n_days)[:H]
    cap = farm.tank_capacity_m3
    L0 = req.tank_initial_m3 if req.tank_initial_m3 is not None else 0.5 * cap
    quota_total = req.water_quota_m3 * n_days
    qmax = farm.pump_max_m3h
    sprinklers = [i for i in ids if plots[i].irrigation_method == "sprinkler"]

    # ------------------------------------------------------------------ modèle
    prob = pulp.LpProblem("aquamind_plan", pulp.LpMaximize)
    T = range(H)
    p = [pulp.LpVariable(f"p_{t}", lowBound=0) for t in T]
    u = [pulp.LpVariable(f"u_{t}", lowBound=0, upBound=pv[t]) for t in T]
    g = [pulp.LpVariable(f"g_{t}", lowBound=0) for t in T]
    L = [pulp.LpVariable(f"L_{t}", lowBound=0) for t in T]
    x = {(i, t): pulp.LpVariable(
        f"x_{i}_{t}", lowBound=0,
        upBound=0 if (req.no_midday_sprinkler and i in sprinklers and t % HOURS in MIDDAY_HOURS) else None)
        for i in ids for t in T}
    b = {(i, d, k): pulp.LpVariable(f"b_{i}_{d}_{k}", lowBound=0, upBound=caps[i][d][k])
         for i in ids for d in range(n_days) for k in K}
    m = {(i, d): pulp.LpVariable(f"m_{i}_{d}", lowBound=0, upBound=margin[i][d])
         for i in ids for d in range(n_days)}
    slack = {(i, d): pulp.LpVariable(f"s_{i}_{d}", lowBound=0)
             for i in ids for d in range(n_days)} if req.s_min > 0 else {}

    prob += (
        pulp.lpSum(coef[i][d][k] * b[i, d, k] for i in ids for d in range(n_days) for k in K)
        + pulp.lpSum(MARGIN_VALUE_FRACTION * coef[i][d][-1] * m[i, d] for i in ids for d in range(n_days))
        - pulp.lpSum(price[t] * g[t] for t in T)
        - pulp.lpSum((WEAR_TND_PER_M3 + LATE_PUMP_TND_PER_M3 * t / H) * p[t] for t in T)
        - MIDDAY_TND_PER_M3 * pulp.lpSum(x[i, t] for i in ids for t in T if t % HOURS in MIDDAY_HOURS)
        - SMIN_PENALTY_TND_PER_M3 * pulp.lpSum(slack.values())
    )

    for t in T:
        prob += p[t] <= qmax, f"pump_{t}"
        prob += e * p[t] - u[t] - g[t] == 0, f"energy_{t}"
        prev = L[t - 1] if t > 0 else L0
        prob += L[t] - prev - p[t] + pulp.lpSum(x[i, t] for i in ids) == 0, f"balance_{t}"
        prob += L[t] <= cap, f"tankcap_{t}"
        prob += pulp.lpSum(x[i, t] for i in ids) <= qmax, f"outlet_{t}"
        if req.grid_max_kw is not None:
            prob += g[t] <= req.grid_max_kw, f"grid_{t}"
    prob += L[H - 1] >= L0, "final_level"
    for i in ids:
        for d in range(n_days):
            hours = range(d * HOURS, (d + 1) * HOURS)
            served = pulp.lpSum(b[i, d, k] for k in K)
            prob += pulp.lpSum(x[i, t] for t in hours) - served - m[i, d] == 0, f"deliver_{i}_{d}"
            if req.s_min > 0:
                prob += served + slack[i, d] >= req.s_min * need[i][d], f"smin_{i}_{d}"
    if req.quota_mode == "horizon":
        prob += pulp.lpSum(p) <= quota_total, "water"
    else:
        for d in range(n_days):
            prob += pulp.lpSum(p[d * HOURS:(d + 1) * HOURS]) <= req.water_quota_m3, f"water_{d}"

    status = prob.solve(pulp.PULP_CBC_CMD(msg=False))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"optimisation non résolue : {pulp.LpStatus[status]}")

    # ------------------------------------------------------------------ résultats
    cons = prob.constraints
    dual = lambda name: float(cons[name].pi or 0.0) if name in cons else 0.0  # noqa: E731
    water_dual = (dual("water") if req.quota_mode == "horizon"
                  else max(dual(f"water_{d}") for d in range(n_days)))
    duals = Duals(
        water=round(water_dual, 4),
        energy=[round(dual(f"energy_{t}"), 4) for t in T],
        pump=[round(dual(f"pump_{t}"), 4) for t in T],
        tank=round(sum(dual(f"tankcap_{t}") for t in T), 4),
    )
    grid_duals = [dual(f"grid_{t}") for t in T] if req.grid_max_kw is not None else [0.0] * H

    alloc = {i: [round(_value(x[i, t]), 3) for t in T] for i in ids}
    pump_m3 = [round(_value(v), 3) for v in p]
    pv_used = [round(_value(v), 3) for v in u]
    grid = [round(_value(v), 3) for v in g]
    pump_kwh = [round(e * q, 3) for q in pump_m3]
    tank = [round(_value(v), 3) for v in L]
    served_k = {i: [[_value(b[i, d, k]) for k in K] for d in range(n_days)] for i in ids}
    margin_served = {i: [_value(m[i, d]) for d in range(n_days)] for i in ids}
    smin_relaxed = {i: round(sum(_value(slack[i, d]) for d in range(n_days)), 2)
                    for i in ids} if slack else {}
    smin_relaxed = {i: v for i, v in smin_relaxed.items() if v > 0.01}

    ctx = PlanContext(req, farm, inputs, H, e, margin_mm, base_margin, hot, quota_total, L0, et0_used,
                      need, margin, coef, caps, list(tr["labels"]), list(tr["bounds"]), pv, price,
                      sprinklers, smin_relaxed)
    plot_info, satisfaction = _plot_summary(ctx, served_k, margin_served, alloc)
    base = traditional_plan(ids, H, req.water_quota_m3, qmax, e, pv)
    base_info, base_sat = _plot_summary(ctx, *_fill_tranches(ctx, base.allocation), base.allocation)

    kpis = _kpis(ctx, pump_m3, alloc, pv_used, grid, pump_kwh, plot_info, satisfaction, tank)
    baseline_kpis = _kpis(ctx, base.pump_m3, base.allocation, base.pv_kwh, base.grid_kwh,
                          base.pump_kwh, base_info, base_sat, [L0] * H)
    kpis.update({
        "grid_kwh_avoided": round(baseline_kpis["grid_kwh"] - kpis["grid_kwh"], 2),
        "grid_cost_saved_tnd": round(baseline_kpis["grid_cost_tnd"] - kpis["grid_cost_tnd"], 2),
        "value_gain_tnd": round(kpis["value_protected_tnd"] - baseline_kpis["value_protected_tnd"], 2),
        "water_saved_m3": round(baseline_kpis["water_pumped_m3"] - kpis["water_pumped_m3"], 2),
    })

    # Prix fictifs significatifs seulement : les petits coûts de départage (usure, heure) créent des
    # duals de l'ordre de 0,001 TND qui ne traduisent pas une ressource rare.
    sig = lambda v: v if abs(v) >= MIN_DUAL_TND else 0.0  # noqa: E731
    shadow = {
        "water": sig(water_dual) * quota_total,
        "pump": sum(sig(v) for v in duals.pump) * qmax,
        "energy": sum(sig(gd) for gd in grid_duals) * (req.grid_max_kw or 0.0),
    }
    limiting = max(shadow, key=lambda k: abs(shadow[k]))
    limiting_factor = limiting if abs(shadow[limiting]) > 0 else "none"

    solve_ms = (time.perf_counter() - t0) * 1000
    explanations = build_explanations(ctx, plot_info, kpis, baseline_kpis, duals, limiting_factor,
                                      alloc, pump_m3, tank, pv_used, grid, grid_duals, base_info)
    return OptimizeResult(
        horizon_h=H, allocation=alloc, pump_m3=pump_m3, tank_m3=tank,
        pv_available_kwh=[round(v, 3) for v in pv], grid_kwh=grid, pv_kwh=pv_used, pump_kwh=pump_kwh,
        satisfaction=satisfaction, plots=plot_info, duals=duals, kpis=kpis, baseline_kpis=baseline_kpis,
        explanations=explanations, limiting_factor=limiting_factor, solve_time_ms=round(solve_ms, 1),
        inputs={**inputs.as_dict(), "et0_used_mm": [round(v, 3) for v in et0_used],
                "safety_margin_mm": [round(v, 3) for v in margin_mm],
                "hot_days": hot, "kwh_per_m3": round(e, 4), "tank_initial_m3": L0,
                "quota_total_m3": quota_total, "quota_mode": req.quota_mode,
                "crops": {i: {"crop": plots[i].crop.name, "kc": round(plot_kc(plots[i]), 3),
                              "stage": plots[i].growth_stage, "note": plots[i].season_note,
                              "method": plots[i].irrigation_method} for i in ids}},
    )


def _fill_tranches(ctx: PlanContext, alloc: dict[str, list[float]]
                   ) -> tuple[dict[str, list[list[float]]], dict[str, list[float]]]:
    """Répartit l'eau d'un plan quelconque dans les paliers (dans l'ordre), puis la marge."""
    served, marg = {}, {}
    for i, series in alloc.items():
        served[i], marg[i] = [], []
        for d, caps in enumerate(ctx.tranche_cap[i]):
            left = sum(series[d * HOURS:(d + 1) * HOURS])
            row = []
            for c in caps:
                row.append(min(left, c))
                left -= row[-1]
            served[i].append(row)
            marg[i].append(min(max(0.0, left), ctx.margin[i][d]))
    return served, marg


def _plot_summary(ctx: PlanContext, served_k: dict[str, list[list[float]]],
                  margin_served: dict[str, list[float]], alloc: dict[str, list[float]]
                  ) -> tuple[dict[str, dict[str, Any]], dict[str, float]]:
    """Par parcelle : besoin, eau servie par palier, satisfaction, valeurs par m³, valeur protégée."""
    info: dict[str, dict[str, Any]] = {}
    satisfaction: dict[str, float] = {}
    n_days = ctx.horizon_h // HOURS
    n_k = len(ctx.tranche_labels)
    for plot in ctx.farm.plots:
        i = plot.id
        need = sum(ctx.need[i])
        served = sum(sum(row) for row in served_k[i])
        at_risk = sum(c * cap for d in range(n_days) for c, cap in zip(ctx.tranche_coef[i][d],
                                                                       ctx.tranche_cap[i][d]))
        protected = sum(c * s for d in range(n_days) for c, s in zip(ctx.tranche_coef[i][d],
                                                                     served_k[i][d]))
        sat = served / need if need > EPS else 1.0
        satisfaction[i] = round(min(1.0, sat), 4)
        tranche_fill = []
        for k in range(n_k):
            cap_k = sum(ctx.tranche_cap[i][d][k] for d in range(n_days))
            got = sum(served_k[i][d][k] for d in range(n_days))
            tranche_fill.append(round(got / cap_k, 3) if cap_k > EPS else 1.0)
        active = [d for d in range(n_days) if ctx.need[i][d] > EPS]
        tranche_values = ([round(sum(ctx.tranche_coef[i][d][k] for d in active) / len(active), 3)
                           for k in range(n_k)] if active else [0.0] * n_k)
        info[i] = {
            "crop": plot.crop.name, "ky": plot.crop.ky, "perennial": plot.crop.perennial,
            "method": plot.irrigation_method, "kc": round(plot_kc(plot), 3),
            "stage_note": plot.season_note or plot.growth_stage,
            "need_m3": round(need, 2), "margin_m3": round(sum(ctx.margin[i]), 2),
            "delivered_m3": round(sum(alloc[i]), 2), "base_served_m3": round(served, 2),
            "margin_served_m3": round(sum(margin_served[i]), 2),
            "deficit_m3": round(max(0.0, need - served), 2),
            "satisfaction": satisfaction[i],
            "tranche_values_tnd_per_m3": tranche_values,
            "tranche_fill": tranche_fill,
            "value_per_m3_tnd": round(at_risk / need, 4) if need > EPS else 0.0,
            "daily_value_tnd": round(plot.crop.ky * daily_crop_value_tnd(plot), 2),
            "value_at_risk_tnd": round(at_risk, 2), "value_protected_tnd": round(protected, 2),
            "value_lost_tnd": round(at_risk - protected, 2),
        }
    ranking = sorted(info, key=lambda k: -info[k]["tranche_values_tnd_per_m3"][0])
    for rank, i in enumerate(ranking, 1):
        info[i]["priority"] = rank
    return info, satisfaction


def _kpis(ctx: PlanContext, pump_m3: list[float], alloc: dict[str, list[float]],
          pv_used: list[float], grid: list[float], pump_kwh: list[float],
          plot_info: dict[str, dict[str, Any]], satisfaction: dict[str, float],
          tank: list[float]) -> dict[str, float]:
    """Indicateurs sur l'horizon (sommes sur 24 ou 48 h)."""
    total_kwh = sum(pump_kwh)
    pv_kwh = sum(pv_used)
    active = [i for i in satisfaction if plot_info[i]["need_m3"] > EPS] or list(satisfaction)
    return {
        "horizon_h": float(ctx.horizon_h),
        "need_m3": round(sum(sum(v) for v in ctx.need.values()), 2),
        "quota_m3": round(ctx.quota_total_m3, 2),
        "water_pumped_m3": round(sum(pump_m3), 2),
        "water_irrigated_m3": round(sum(sum(v) for v in alloc.values()), 2),
        "pump_kwh": round(total_kwh, 2),
        "pv_kwh": round(pv_kwh, 2),
        "grid_kwh": round(sum(grid), 2),
        "solar_share_pct": round(100 * pv_kwh / total_kwh, 1) if total_kwh > EPS else 0.0,
        "pv_available_kwh": round(sum(ctx.pv_available), 2),
        "grid_cost_tnd": round(sum(pr * gk for pr, gk in zip(ctx.price, grid)), 2),
        "value_protected_tnd": round(sum(v["value_protected_tnd"] for v in plot_info.values()), 2),
        "value_lost_tnd": round(sum(v["value_lost_tnd"] for v in plot_info.values()), 2),
        "satisfaction_mean_pct": round(100 * sum(satisfaction[i] for i in active) / len(active), 1),
        "satisfaction_min_pct": round(100 * min(satisfaction[i] for i in active), 1),
        **{f"satisfaction_{i}_pct": round(100 * s, 1) for i, s in satisfaction.items()},
        "tank_final_m3": round(tank[-1], 2),
        "tank_max_m3": round(max(tank), 2),
    }
