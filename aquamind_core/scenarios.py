"""Scénarios de démonstration, rejoués sur des journées réelles de l'archive Open-Meteo.

    python -m aquamind_core.scenarios            # les 4 scénarios, KPI et explications
    python -m aquamind_core.scenarios --live     # + plan de demain avec la prévision Open-Meteo
    python -m aquamind_core.scenarios --markdown docs/SCENARIOS.md   # rapport complet en Markdown

Chaque scénario couvre 48 h. J1 : ET0 prévue par le modèle LightGBM à partir des 7 jours précédents ;
J2 : ET0 Open-Meteo du jour (archive, joue le rôle de la prévision). Ensoleillement de chaque jour =
rayonnement observé / moyenne du mois. Les dates sont choisies dans les données (voir docstrings).
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from aquamind_core.config import load_farm
from aquamind_core.forecast import PlanInputs, archive_inputs, live_inputs
from aquamind_core.optimizer import optimize
from aquamind_core.schemas import OptimizeRequest, OptimizeResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Scenario:
    """Scénario : description, ferme, date de départ (archive) et paramètres de la requête."""

    key: str
    title: str
    description: str
    farm_file: str
    site: str
    start: date
    quota_factor: float = 1.0
    params: tuple[tuple[str, object], ...] = ()

    def request(self, inputs: PlanInputs, horizon_h: int = 48, start: date | None = None,
                **overrides: object) -> OptimizeRequest:
        farm = load_farm(self.farm_file)
        days = horizon_h // 24
        return OptimizeRequest(
            farm=farm, site=self.site, date=start or self.start, horizon_h=horizon_h,
            water_quota_m3=farm.water_quota_m3_day * self.quota_factor,
            et0_mm=inputs.et0_mm[:days], rain_mm=inputs.rain_mm[:days],
            tmean_c=inputs.tmean_c[:days], tmax_c=inputs.tmax_c[:days],
            pv_day_factors=inputs.pv_day_factors[:days],
            **dict(self.params), **overrides,
        )


SCENARIOS: dict[str, Scenario] = {
    s.key: s for s in [
        Scenario(
            "tunisia_heatwave", "Canicule à Sidi Bouzid",
            "20–21 juillet 2025 : les deux jours consécutifs à plus forte ET0 de l'été 2025 dans l'archive "
            "(Tmax 42,9 et 44,8 °C, ET0 9,8 et 10,0 mm). Le besoin dépasse largement le quota.",
            "farm.json", "tunisia", date(2025, 7, 20)),
        Scenario(
            "tunisia_cloudy", "Journée voilée annoncée",
            "8–9 juin 2024 : le 9 juin, le rayonnement tombe à 52 % de la veille (vent de sable, 33 km/h) "
            "alors que l'ET0 reste forte. Le plan doit anticiper avec le réservoir.",
            "farm.json", "tunisia", date(2024, 6, 8)),
        Scenario(
            "water_cut_50", "Quota d'eau divisé par deux",
            "6–7 juillet 2025 (ET0 proche de la médiane de juillet 2025, 7,7 mm/j) avec un quota de "
            "30 m³/j au lieu de 60 (coupure, tour d'eau réduit).",
            "farm.json", "tunisia", date(2025, 7, 6), quota_factor=0.5),
        Scenario(
            "japan_rice", "Riz à Niigata (mode Japon)",
            "22–23 août 2025 à Niigata : 3 rizières de 0,5 ha en submersion, quota de 150 m³/j, puits de "
            "15 m. Tarifs et valeurs de récolte : mêmes hypothèses que la Tunisie (en TND).",
            "farm_japan.json", "japan", date(2025, 8, 22)),
    ]
}


def run_scenario(key: str) -> tuple[Scenario, OptimizeResult, dict[str, float]]:
    """Plan sur 48 h, plus la valeur de l'horizon 48 h par rapport à deux plans successifs de 24 h."""
    sc = SCENARIOS[key]
    inputs = archive_inputs(sc.site, sc.start, days=2)
    result = optimize(sc.request(inputs), inputs)
    return sc, result, horizon_value(sc, inputs, result)


def horizon_value(sc: Scenario, inputs: PlanInputs, result: OptimizeResult) -> dict[str, float]:
    """Compare au plan « myope » : J1 puis J2 planifiés séparément sur 24 h (sans voir le lendemain).

    Chaque plan de 24 h doit rendre le réservoir au niveau initial et respecter le quota du jour :
    c'est exactement ce que l'horizon de 48 h permet de dépasser."""
    totals = {"grid_kwh": 0.0, "grid_cost_tnd": 0.0, "value_protected_tnd": 0.0}
    for d in range(2):
        day_inputs = PlanInputs(sc.site, sc.start + timedelta(days=d), [inputs.et0_mm[d]],
                                [inputs.rain_mm[d]], [inputs.tmean_c[d]], [inputs.pv_day_factors[d]],
                                [inputs.sources[d]], [inputs.tmax_c[d]])
        day = optimize(sc.request(day_inputs, horizon_h=24, start=day_inputs.start), day_inputs)
        for k in totals:
            totals[k] += day.kpis[k]
    return {
        "myopic_grid_kwh": round(totals["grid_kwh"], 2),
        "myopic_value_protected_tnd": round(totals["value_protected_tnd"], 2),
        "horizon48_grid_kwh_saved": round(totals["grid_kwh"] - result.kpis["grid_kwh"], 2),
        "horizon48_net_gain_tnd": round(
            (result.kpis["value_protected_tnd"] - result.kpis["grid_cost_tnd"])
            - (totals["value_protected_tnd"] - totals["grid_cost_tnd"]), 2),
    }


KPI_ROWS: list[tuple[str, str, Callable[[float], str]]] = [
    ("need_m3", "Besoin prévu (m³)", lambda v: f"{v:.0f}"),
    ("quota_m3", "Quota (m³)", lambda v: f"{v:.0f}"),
    ("water_pumped_m3", "Eau pompée (m³)", lambda v: f"{v:.0f}"),
    ("pump_kwh", "Énergie de pompage (kWh)", lambda v: f"{v:.1f}"),
    ("pv_kwh", "  dont solaire (kWh)", lambda v: f"{v:.1f}"),
    ("grid_kwh", "  dont réseau (kWh)", lambda v: f"{v:.1f}"),
    ("solar_share_pct", "Part solaire (%)", lambda v: f"{v:.0f}"),
    ("grid_cost_tnd", "Coût réseau (TND)", lambda v: f"{v:.2f}"),
    ("satisfaction_A_pct", "Satisfaction A (%)", lambda v: f"{v:.0f}"),
    ("satisfaction_B_pct", "Satisfaction B (%)", lambda v: f"{v:.0f}"),
    ("satisfaction_C_pct", "Satisfaction C (%)", lambda v: f"{v:.0f}"),
    ("satisfaction_min_pct", "Satisfaction minimale (%)", lambda v: f"{v:.0f}"),
    ("value_protected_tnd", "Valeur protégée (TND)", lambda v: f"{v:.0f}"),
    ("value_lost_tnd", "Valeur perdue (TND)", lambda v: f"{v:.0f}"),
    ("tank_max_m3", "Réservoir max (m³)", lambda v: f"{v:.0f}"),
]


def format_report(sc: Scenario, res: OptimizeResult, hv: dict[str, float]) -> str:
    """Tableau KPI (optimisé vs traditionnel) + gains + explications, en texte."""
    lines = [f"\n{'=' * 100}", f"SCÉNARIO {sc.key} — {sc.title}", sc.description,
             f"ET0 utilisée : {res.inputs['et0_used_mm']} mm/j ({' ; '.join(res.inputs['sources'])}) | "
             f"pluie {res.inputs['rain_mm']} mm | ensoleillement {res.inputs['pv_day_factors']} | "
             f"marge {res.inputs['safety_margin_mm']} mm/j | {res.inputs['kwh_per_m3']} kWh/m³",
             "Cultures : " + " ; ".join(f"{i} {c['crop']} ({c['note']}, Kc {c['kc']})"
                                        for i, c in res.inputs["crops"].items()),
             f"Facteur limitant : {res.limiting_factor} | résolu en {res.solve_time_ms:.0f} ms",
             "", f"{'KPI (48 h)':32s}{'Optimisé':>12s}{'Traditionnel':>14s}"]
    for key, label, f in KPI_ROWS:
        if key in res.kpis:
            lines.append(f"{label:32s}{f(res.kpis[key]):>12s}{f(res.baseline_kpis[key]):>14s}")
    k = res.kpis
    lines += ["",
              f"kWh réseau évités vs traditionnel : {k['grid_kwh_avoided']:+.1f} kWh "
              f"({k['grid_cost_saved_tnd']:+.2f} TND) | valeur protégée : {k['value_gain_tnd']:+.0f} TND "
              f"| eau économisée : {k['water_saved_m3']:+.0f} m³",
              f"Horizon 48 h vs deux plans de 24 h : {hv['horizon48_grid_kwh_saved']:+.1f} kWh réseau "
              f"évités, gain net {hv['horizon48_net_gain_tnd']:+.2f} TND", "", "Explications :"]
    lines += [f"  {n}. {txt}" for n, txt in enumerate(res.explanations, 1)]
    return "\n".join(lines)


def format_markdown(runs: list[tuple[Scenario, OptimizeResult, dict[str, float]]]) -> str:
    """Rapport complet en Markdown : tableau de synthèse, puis chaque scénario (KPI + explications)."""
    keys = [("need_m3", "Besoin prévu (m³)"), ("quota_m3", "Quota (m³)"),
            ("water_pumped_m3", "Eau pompée (m³)"), ("pv_kwh", "kWh solaire"), ("grid_kwh", "kWh réseau"),
            ("solar_share_pct", "Part solaire (%)"), ("grid_kwh_avoided", "kWh réseau évités vs trad."),
            ("satisfaction_A_pct", "Satisfaction A (%)"), ("satisfaction_B_pct", "Satisfaction B (%)"),
            ("satisfaction_C_pct", "Satisfaction C (%)"), ("value_protected_tnd", "Valeur protégée (TND)"),
            ("value_gain_tnd", "Gain de valeur vs trad. (TND)")]
    lines = ["# Scénarios du moteur de décision (Phase 4)", "",
             "*Généré par `python -m aquamind_core.scenarios --markdown docs/SCENARIOS.md`. Horizon 48 h, "
             "journées réelles de l'archive Open-Meteo. Valeurs de récolte, paliers, calendrier et tarifs : "
             "hypothèses — **estimation simulée**.*", "",
             "## Méthode en bref", "",
             "- **Programme linéaire** (PuLP/CBC) sur 48 h : pompage puits → réservoir limité par l'énergie "
             "(solaire + réseau), irrigation réservoir → parcelles, bilan de la cuve heure par heure, niveau "
             "final ≥ niveau initial, quota cumulé sur l'horizon (`quota_mode=\"daily\"` pour un quota strict "
             "par jour).",
             "- **Valeur de l'eau par paliers** : 0–30 % du besoin = survie (50 % de la valeur du jour), "
             "30–70 % = moyen (35 %), 70–100 % = confort (15 %). Pour l'olivier (pérenne), la survie vaut "
             "5 fois plus. L'eau est donc partagée : chaque culture reçoit sa survie avant le confort d'une "
             "autre.",
             "- **Calendrier cultural** : culture et stade dépendent de la date (piment en été, blé en "
             "hiver-printemps, tomate d'avril à août, jachère hors saison).",
             "- **ET0** : J1 = modèle LightGBM (7 jours précédents), J2 = Open-Meteo. **Marge de sécurité** "
             "= MAE du modèle (0,61 mm/j), ×1,5 les jours où Tmax > 35 °C (sous-estimation moyenne mesurée "
             "de 0,32 mm/j ces jours-là sur 2024–2025).",
             "- **Référence traditionnelle** : pompe dès 7h à plein débit, doses égales jusqu'au quota du "
             "jour, mêmes panneaux solaires.", "",
             "## Synthèse (optimisé, entre parenthèses : irrigation traditionnelle)", "",
             "| KPI (48 h) | " + " | ".join(sc.title for sc, _, _ in runs) + " |",
             "|---|" + "---|" * len(runs)]
    for key, label in keys:
        cells = []
        for _, res, _ in runs:
            v = res.kpis.get(key)
            b = res.baseline_kpis.get(key)
            cells.append("—" if v is None else f"{v:.0f}" + (f" ({b:.0f})" if b is not None else ""))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("| Facteur limitant | " + " | ".join(r.limiting_factor for _, r, _ in runs) + " |")
    for sc, res, hv in runs:
        lines += ["", f"## {sc.title} (`{sc.key}`)", "", sc.description, "",
                  "```", format_report(sc, res, hv).strip("\n= "), "```"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Scénarios AquaMind (estimation simulée)")
    parser.add_argument("--live", action="store_true", help="ajoute le plan de demain (prévision)")
    parser.add_argument("--markdown", metavar="FICHIER", help="écrit aussi le rapport complet en Markdown")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    runs = [run_scenario(key) for key in SCENARIOS]
    for run in runs:
        print(format_report(*run))
    if args.markdown:
        from pathlib import Path

        Path(args.markdown).write_text(format_markdown(runs), encoding="utf-8")
        print(f"\nRapport écrit : {args.markdown}")
    if args.live:
        farm = load_farm()
        inputs = live_inputs("tunisia", days=2)
        req = OptimizeRequest(farm=farm, site="tunisia", date=inputs.start,
                              water_quota_m3=farm.water_quota_m3_day, et0_mm=inputs.et0_mm,
                              rain_mm=inputs.rain_mm, tmean_c=inputs.tmean_c, tmax_c=inputs.tmax_c,
                              pv_day_factors=inputs.pv_day_factors)
        live = Scenario("live", "Plan de demain (prévision Open-Meteo)",
                        f"Départ {inputs.start} ; météo des 7 derniers jours et prévision.",
                        "farm.json", "tunisia", inputs.start)
        res = optimize(req, inputs)
        print(format_report(live, res, horizon_value(live, inputs, res)))


if __name__ == "__main__":
    main()
