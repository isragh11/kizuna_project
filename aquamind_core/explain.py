"""Explications en français du plan optimisé (textes à trous, sans LLM).

Sources : duals (prix fictifs), paliers de valeur servis par parcelle, calendrier cultural, marge de
sécurité, heures de pompage, niveau du réservoir, écart avec l'irrigation traditionnelle.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from aquamind_core.schemas import Duals

if TYPE_CHECKING:  # import circulaire évité à l'exécution
    from aquamind_core.optimizer import PlanContext

HOURS = 24
FULL = 0.995
LOW_SUN = 0.8
METHOD_FR = {"drip": "goutte-à-goutte", "sprinkler": "aspersion", "flood": "submersion"}


def fr(value: float, nd: int = 1) -> str:
    """Nombre au format français : virgule décimale, espace pour les milliers."""
    s = f"{value:,.{nd}f}".replace(",", " ").replace(".", ",")
    return s.replace("-0,0", "0,0") if nd else s


def pct(value: float) -> str:
    return f"{fr(100 * value, 0)} %"


def hour_ranges(hours: list[int]) -> str:
    """[8, 9, 10, 14, 15] → « 8h–11h, 14h–16h » (fin = heure de fin exclusive)."""
    if not hours:
        return "aucune heure"
    ranges, start, prev = [], hours[0], hours[0]
    for h in hours[1:] + [None]:
        if h is not None and h == prev + 1:
            prev = h
            continue
        ranges.append(f"{start}h–{prev + 1}h")
        if h is not None:
            start = prev = h
    return ", ".join(ranges)


def _day_hours(series: list[float], d: int, threshold: float = 0.01) -> list[int]:
    return [h for h in range(HOURS) if series[d * HOURS + h] > threshold]


def _tranche_txt(values: list[float], perennial: bool) -> str:
    parts = [fr(v, 2) for v in values]
    if perennial:
        parts[0] += " (arbre)"
    return " / ".join(parts)


def build_explanations(ctx: "PlanContext", plots: dict[str, dict[str, Any]], kpis: dict[str, float],
                       base: dict[str, float], duals: Duals, limiting: str,
                       alloc: dict[str, list[float]], pump_m3: list[float], tank: list[float],
                       pv_used: list[float], grid: list[float], grid_duals: list[float],
                       base_plots: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Liste ordonnée de phrases : situation, cultures, facteur limitant, paliers, rationnement,
    marge, énergie, réservoir, comparaison avec la référence, hypothèses."""
    req, inp = ctx.request, ctx.inputs
    n_days = ctx.horizon_h // HOURS
    labels = [f"J{d + 1} ({(inp.start + timedelta(days=d)).strftime('%d/%m')})" for d in range(n_days)]
    tl = ctx.tranche_labels
    out: list[str] = []

    # 1. Situation et cultures du jour
    et0_txt = " ; ".join(f"{labels[d]} {fr(ctx.et0_used[d], 2)} mm ({inp.sources[d]})"
                         for d in range(n_days))
    temp_txt = f", avec {req.temp_offset_c:+.1f} °C appliqués" if req.temp_offset_c else ""
    need, quota = kpis["need_m3"], ctx.quota_total_m3
    situation = (f"Besoin prévu sur {ctx.horizon_h} h : {fr(need, 0)} m³ (ET0 {et0_txt}{temp_txt}) "
                 f"pour un quota de {fr(quota, 0)} m³")
    if need > quota + 0.5:
        situation += (f" : il manque {fr(need - quota, 0)} m³, soit {pct((need - quota) / need)} "
                      "du besoin. Il faut rationner.")
    else:
        situation += f" : le quota couvre les besoins prévus ({fr(quota - need, 0)} m³ de reste)."
    out.append(situation)
    crops = " ; ".join(
        f"{i} {v['crop']} ({v['stage_note']}, Kc {fr(v['kc'], 2)}, {METHOD_FR[v['method']]})"
        for i, v in plots.items())
    out.append(f"Cultures du jour (calendrier cultural) : {crops}.")

    # 2. Facteur limitant (duals)
    active = {i: v for i, v in plots.items() if v["need_m3"] > 1e-6}
    all_full = all(v["satisfaction"] >= FULL for v in active.values())
    if limiting == "water" and all_full:
        out.append(f"Les besoins prévus sont couverts ; l'eau ne limite que la marge de sécurité : les "
                   f"{fr(quota, 0)} m³ du quota sont utilisés et 1 m³ de plus vaudrait "
                   f"{fr(duals.water, 2)} TND (prix fictif de l'eau).")
    elif limiting == "water":
        out.append(f"Facteur limitant : l'EAU. Les {fr(quota, 0)} m³ du quota sont utilisés ; "
                   f"1 m³ de quota en plus protégerait {fr(duals.water, 2)} TND de récolte "
                   "(prix fictif de l'eau, variable duale).")
    elif limiting == "pump":
        sat_h = sum(1 for v in duals.pump if abs(v) > 1e-6)
        out.append(f"Facteur limitant : le DÉBIT DE LA POMPE ({fr(req.farm.pump_max_m3h, 0)} m³/h), "
                   f"saturé pendant {sat_h} h ; 1 m³/h de capacité en plus vaudrait "
                   f"{fr(max(abs(v) for v in duals.pump), 2)} TND.")
    elif limiting == "energy":
        out.append(f"Facteur limitant : l'ÉNERGIE (puissance réseau limitée à "
                   f"{fr(req.grid_max_kw or 0, 1)} kW et solaire insuffisant) ; 1 kWh de plus vaudrait "
                   f"jusqu'à {fr(max(abs(v) for v in grid_duals), 2)} TND.")
    else:
        out.append("Aucune ressource n'est limitante : chaque parcelle reçoit 100 % de son besoin prévu.")

    # 3. Paliers de valeur : comment l'eau est partagée (seulement s'il faut rationner)
    if active and not all_full:
        table = " ; ".join(f"{i} {v['crop']} {_tranche_txt(v['tranche_values_tnd_per_m3'], v['perennial'])}"
                           for i, v in sorted(active.items(), key=lambda kv: kv[1]["priority"]))
        perennial = [v["crop"] for v in active.values() if v["perennial"]]
        bounds = [0.0, *ctx.tranche_bounds]
        ranges = " / ".join(f"{tl[k]} {fr(100 * bounds[k], 0)}–{fr(100 * bounds[k + 1], 0)} %"
                            for k in range(len(tl)))
        txt = (f"Valeur de l'eau par paliers du besoin ({ranges}), en TND par m³ : {table}. L'eau va d'abord aux paliers les plus précieux, "
               "toutes parcelles confondues : chaque culture reçoit d'abord sa part de survie avant "
               "que la plus rentable ne reçoive son confort.")
        if perennial:
            txt += (f" Pour l'{perennial[0].lower()} (culture pérenne), le palier de survie vaut 5 fois "
                    "plus : sans eau, l'arbre dépérit et il faut plusieurs saisons pour retrouver la "
                    "production.")
        out.append(txt)

    # 4. Palier marginal : à quoi sert le dernier m³
    if limiting == "water" and not all_full:
        partial = [(i, k) for i, v in active.items() for k, f in enumerate(v["tranche_fill"])
                   if 0.02 < f < 0.98]
        if partial:
            i, k = min(partial, key=lambda ik: abs(active[ik[0]]["tranche_values_tnd_per_m3"][ik[1]]
                                                   - duals.water))
            out.append(f"Le dernier m³ du quota vaut {fr(duals.water, 2)} TND : il complète le palier "
                       f"« {tl[k]} » de {i} ({active[i]['crop']}). Tout palier qui vaut moins par m³ "
                       "reste sans eau.")

    # 5. Parcelle par parcelle : servie ou rationnée, et pourquoi
    for i in sorted(plots, key=lambda j: plots[j]["priority"]):
        info = plots[i]
        name = f"Parcelle {i} ({info['crop']})"
        if info["need_m3"] <= 1e-6:
            reason = "jachère, hors saison" if info["kc"] == 0 else "pluie suffisante"
            out.append(f"{name} : aucun besoin ({reason}).")
            continue
        if info["satisfaction"] >= FULL:
            out.append(f"{name} : 100 % du besoin servi ({fr(info['base_served_m3'], 1)} m³), "
                       f"valeur protégée {fr(info['value_protected_tnd'], 0)} TND.")
            continue
        fill = info["tranche_fill"]
        vals = info["tranche_values_tnd_per_m3"]
        fill_txt = ", ".join(f"{tl[k]} {pct(f)}" for k, f in enumerate(fill))
        reasons = []
        if limiting == "water":
            served = [tl[k] for k, f in enumerate(fill) if f >= 0.98]
            unserved = [k for k, f in enumerate(fill) if f < 0.98]
            if served:
                reasons.append(f"palier(s) {', '.join(served)} servi(s) en priorité")
            if unserved:
                k0 = unserved[0]
                if fill[k0] > 0.02:
                    reasons.append(
                        f"le palier « {tl[k0]} » n'est servi qu'en partie ({pct(fill[k0])}) : c'est là que "
                        f"le quota s'épuise, en concurrence avec les autres parcelles (dernier m³ : "
                        f"{fr(duals.water, 2)} TND)")
                else:
                    reasons.append(
                        f"le palier « {tl[k0]} » vaut {fr(vals[k0], 2)} TND/m³, moins que le dernier m³ "
                        f"d'eau disponible ({fr(duals.water, 2)} TND/m³)")
        elif limiting == "pump":
            reasons.append("le débit de la pompe ne permet pas de livrer plus pendant les heures autorisées")
        elif limiting == "energy":
            reasons.append("l'énergie disponible (solaire + réseau limité) ne permet pas de pomper plus")
        else:
            reasons.append("pomper davantage coûterait plus cher en électricité que la récolte protégée")
        if info["ky"] < 1:
            reasons.append(f"la culture tolère mieux le manque d'eau (Ky {fr(info['ky'], 2)} < 1)")
        if i in ctx.sprinkler_plots and req.no_midday_sprinkler and limiting in ("pump", "none"):
            reasons.append("l'aspersion est interdite de 11h à 16h")
        out.append(
            f"{name} RATIONNÉE à {pct(info['satisfaction'])} de son besoin "
            f"({fr(info['base_served_m3'], 1)} sur {fr(info['need_m3'], 1)} m³, il manque "
            f"{fr(info['deficit_m3'], 1)} m³ ; paliers : {fill_txt}). Pourquoi : {' ; '.join(reasons)}. "
            f"Perte de récolte estimée : {fr(info['value_lost_tnd'], 0)} TND sur {ctx.horizon_h} h.")

    # 6. Marge de sécurité (augmentée les jours chauds)
    margin_total = sum(sum(v) for v in ctx.margin.values())
    served = {i: plots[i]["margin_served_m3"] for i in plots if plots[i]["margin_served_m3"] > 0.05}
    margin_txt = f"Marge de sécurité : +{fr(ctx.base_margin_mm, 2)} mm/j d'ET0 (erreur moyenne mesurée du modèle)"
    hot = [d for d in range(n_days) if ctx.hot_days[d]]
    if hot:
        from aquamind_core.optimizer import HOT_DAY_EVIDENCE

        days_txt = ", ".join(f"{labels[d]} {fr(inp.tmax_c[d] + req.temp_offset_c, 1)} °C" for d in hot)
        margin_txt += (f", portée à {fr(ctx.margin_mm[hot[0]], 2)} mm/j (×{fr(req.hot_day_margin_factor, 1)}) "
                       f"les jours chauds (Tmax > {fr(req.hot_day_tmax_c, 0)} °C : {days_txt}), car le "
                       f"modèle sous-estime les extrêmes : {HOT_DAY_EVIDENCE}")
    margin_txt += f". Soit {fr(margin_total, 1)} m³ sur l'horizon. "
    if served:
        margin_txt += "Servie pour : " + ", ".join(f"{i} ({fr(v, 1)} m³)" for i, v in served.items()) + "."
    else:
        margin_txt += "Non servie : l'eau manque déjà pour couvrir les besoins prévus."
    out.append(margin_txt)

    # 7. Pompage et énergie
    pump_txt = " ; ".join(f"{labels[d]} {hour_ranges(_day_hours(pump_m3, d))}" for d in range(n_days))
    energy = (f"Pompage : {fr(kpis['water_pumped_m3'], 0)} m³ ({pump_txt}), "
              f"{fr(kpis['pump_kwh'], 1)} kWh dont {pct(kpis['solar_share_pct'] / 100)} solaire")
    if kpis["grid_kwh"] > 0.05:
        grid_hours = [t for t in range(ctx.horizon_h) if grid[t] > 0.01]
        when = " ; ".join(f"{labels[d]} {hour_ranges([t % HOURS for t in grid_hours if t // HOURS == d])}"
                          for d in range(n_days) if any(t // HOURS == d for t in grid_hours))
        avg_price = kpis["grid_cost_tnd"] / kpis["grid_kwh"]
        tariff = ("heures creuses" if abs(avg_price - min(ctx.price)) < 0.005
                  else "le solaire ne suffit pas aux heures moins chères")
        energy += (f" ; {fr(kpis['grid_kwh'], 1)} kWh réseau ({fr(kpis['grid_cost_tnd'], 2)} TND, "
                   f"{fr(avg_price, 3)} TND/kWh en moyenne : {tariff}), aux heures {when}")
    out.append(energy + ".")

    # 8. Réservoir : tampon et anticipation d'un jour peu ensoleillé
    irrigated = [sum(alloc[i][t] for i in alloc) for t in range(ctx.horizon_h)]
    if n_days >= 2:
        d1_pump, d1_irr = sum(pump_m3[:HOURS]), sum(irrigated[:HOURS])
        sun2 = inp.pv_day_factors[1]
        if sun2 < LOW_SUN and d1_pump - d1_irr > 0.5:
            d1_grid = sum(grid[:HOURS])
            how = ("avec le solaire du J1" if d1_grid < 0.05 else
                   f"avec le solaire du J1 et {fr(d1_grid, 1)} kWh réseau ({hour_ranges(_day_hours(grid, 0))})")
            out.append(
                f"Anticipation : {labels[1]} s'annonce peu ensoleillé ({pct(sun2)} d'une journée normale). "
                f"Le plan pompe {fr(d1_pump - d1_irr, 1)} m³ de plus que l'irrigation du J1, {how}, "
                f"et les stocke dans le réservoir (niveau fin J1 : {fr(tank[HOURS - 1], 1)} m³) "
                "pour les utiliser le J2 au lieu d'acheter du réseau en journée.")
        elif sun2 < LOW_SUN:
            out.append(
                f"{labels[1]} s'annonce peu ensoleillé ({pct(sun2)} d'une journée normale), mais le plan ne "
                "peut pas stocker davantage d'avance (quota, capacité de la pompe ou du réservoir).")
    night_irr = sum(v for t, v in enumerate(irrigated) if pump_m3[t] < 0.01)
    out.append(
        f"Réservoir : {fr(ctx.tank_initial_m3, 0)} m³ au départ, entre {fr(min(tank), 0)} et "
        f"{fr(max(tank), 0)} m³ pendant l'horizon, {fr(tank[-1], 0)} m³ à la fin (≥ niveau initial). "
        f"Il découple le pompage solaire de l'irrigation : {fr(night_irr, 0)} m³ sont irrigués à des "
        "heures sans pompage.")

    # 9. Aspersion
    if req.no_midday_sprinkler:
        for i in ctx.sprinkler_plots:
            if plots[i]["delivered_m3"] < 0.05:
                continue
            hrs = " ; ".join(f"{labels[d]} {hour_ranges(_day_hours(alloc[i], d))}" for d in range(n_days))
            out.append(f"Aspersion de {i} ({plots[i]['crop']}) interdite de 11h à 16h (évaporation) : "
                       f"irrigation de {i} programmée {hrs}.")

    # 10. s_min
    if ctx.smin_relaxed:
        lst = ", ".join(f"{i} ({fr(v, 1)} m³)" for i, v in ctx.smin_relaxed.items())
        out.append(f"Le minimum demandé (s_min = {pct(req.s_min)} du besoin) est impossible avec ces "
                   f"ressources : il a été relâché pour {lst}.")
    elif req.s_min > 0:
        out.append(f"Minimum garanti respecté : au moins {pct(req.s_min)} du besoin de chaque parcelle.")

    # 11. Comparaison avec l'irrigation traditionnelle
    sat_txt = ""
    if base_plots:
        ids = [i for i in plots if plots[i]["need_m3"] > 1e-6]
        sat_txt = (", satisfaction " + "/".join(ids) + " "
                   + "/".join(fr(100 * base_plots[i]["satisfaction"], 0) for i in ids) + " → "
                   + "/".join(fr(100 * plots[i]["satisfaction"], 0) for i in ids) + " %")
    out.append(
        "Comparé à l'irrigation traditionnelle (pompe dès 7h à plein débit, doses égales jusqu'au quota du "
        f"jour, mêmes panneaux solaires) : réseau {fr(base['grid_kwh'], 1)} → {fr(kpis['grid_kwh'], 1)} kWh "
        f"({fr(-kpis['grid_kwh_avoided'], 1)} kWh, {fr(-kpis['grid_cost_saved_tnd'], 2)} TND), part solaire "
        f"{fr(base['solar_share_pct'], 0)} → {fr(kpis['solar_share_pct'], 0)} %{sat_txt}, valeur protégée "
        f"{fr(base['value_protected_tnd'], 0)} → {fr(kpis['value_protected_tnd'], 0)} TND "
        f"({kpis['value_gain_tnd']:+.0f} TND), eau pompée {fr(base['water_pumped_m3'], 0)} → "
        f"{fr(kpis['water_pumped_m3'], 0)} m³.")

    out.append("Valeurs de récolte, paliers de valeur, calendrier cultural, durées de saison et tarifs "
               "électriques : hypothèses (economics.json, crops.json, crop_calendar.json) — estimation simulée.")
    return out
