"""Plan d'irrigation suivi par le simulateur : dernier plan de l'optimiseur.

Ordre de recherche : API (GET /optimize/last, Phase 6) → optimiseur local (prévision du jour ou
scénario) → dernier plan enregistré (data/plan_last.json) → plan par défaut. Le plan couvre 24 ou
48 h ; le simulateur le rejoue en boucle tant qu'aucun plan plus récent n'est disponible.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from aquamind_core.config import DATA_DIR, get_settings, load_farm
from aquamind_core.schemas import Farm

logger = logging.getLogger(__name__)

PLAN_FILE = DATA_DIR / "plan_last.json"


@dataclass
class PlanSchedule:
    """Plan horaire : pompage puits → cuve, irrigation cuve → parcelles, PV disponible, ET0 par jour."""

    start: str                               # ISO, minuit du premier jour (heure locale)
    horizon_h: int
    pump_m3: list[float]
    allocation: dict[str, list[float]]       # parcelle → m³/h
    pv_day_factors: list[float]
    et0_mm: list[float]
    tank_initial_m3: float
    source: str
    water_value_tnd_per_m3: float = 0.0      # prix fictif de l'eau du plan (dual), 0 si inconnu
    farm_file: str = "farm.json"
    site: str = "tunisia"
    meta: dict = field(default_factory=dict)

    @property
    def start_dt(self) -> datetime:
        return datetime.fromisoformat(self.start)

    def hour_index(self, ts: datetime) -> int:
        """Heure du plan correspondant à ts (le plan est rejoué en boucle au-delà de l'horizon)."""
        return int((ts - self.start_dt).total_seconds() // 3600) % self.horizon_h

    def day_index(self, ts: datetime) -> int:
        return self.hour_index(ts) // 24

    def save(self) -> None:
        PLAN_FILE.parent.mkdir(parents=True, exist_ok=True)
        PLAN_FILE.write_text(json.dumps(asdict(self), ensure_ascii=False), encoding="utf-8")


def _from_result(res, start: date, source: str, farm_file: str, site: str) -> PlanSchedule:
    """OptimizeResult (objet ou dict JSON) → PlanSchedule."""
    get = res.get if isinstance(res, dict) else lambda k, d=None: getattr(res, k, d)
    inputs = get("inputs", {}) or {}
    duals = get("duals", {}) or {}
    water = duals.get("water", 0.0) if isinstance(duals, dict) else duals.water
    return PlanSchedule(
        start=datetime.combine(start, datetime.min.time()).isoformat(), horizon_h=get("horizon_h", 24),
        pump_m3=list(get("pump_m3") or [0.0] * get("horizon_h", 24)), allocation=dict(get("allocation")),
        pv_day_factors=list(inputs.get("pv_day_factors", [1.0])),
        et0_mm=list(inputs.get("et0_used_mm", inputs.get("et0_mm", [5.0]))),
        tank_initial_m3=float(inputs.get("tank_initial_m3", 40.0)), source=source,
        water_value_tnd_per_m3=float(water or 0.0), farm_file=farm_file, site=site,
        meta={"limiting_factor": get("limiting_factor"), "kpis": get("kpis")},
    )


def plan_from_api(api_url: str, timeout: float = 2.0) -> PlanSchedule | None:
    """GET {api}/optimize/last (Phase 6). None si l'API ne répond pas."""
    import requests

    try:
        r = requests.get(f"{api_url.rstrip('/')}/optimize/last", timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        start = date.fromisoformat(data.get("inputs", {}).get("start", date.today().isoformat()))
        return _from_result(data, start, "API /optimize/last", "farm.json",
                            data.get("inputs", {}).get("site", "tunisia"))
    except Exception as exc:  # API absente (Phase 6 pas lancée) : repli silencieux
        logger.info("API indisponible pour le plan (%s)", type(exc).__name__)
        return None


def plan_from_optimizer(scenario: str | None = None) -> PlanSchedule:
    """Calcule le plan localement : scénario de démonstration, ou prévision à partir de demain."""
    from aquamind_core.forecast import live_inputs
    from aquamind_core.optimizer import optimize
    from aquamind_core.scenarios import SCENARIOS, archive_inputs
    from aquamind_core.schemas import OptimizeRequest

    if scenario:
        sc = SCENARIOS[scenario]
        inputs = archive_inputs(sc.site, sc.start, days=2)
        res = optimize(sc.request(inputs), inputs)
        return _from_result(res, sc.start, f"optimiseur, scénario {scenario}", sc.farm_file, sc.site)
    farm = load_farm()
    inputs = live_inputs("tunisia", days=2)
    req = OptimizeRequest(farm=farm, site="tunisia", date=inputs.start, water_quota_m3=farm.water_quota_m3_day,
                          et0_mm=inputs.et0_mm, rain_mm=inputs.rain_mm, tmean_c=inputs.tmean_c,
                          tmax_c=inputs.tmax_c, pv_day_factors=inputs.pv_day_factors)
    return _from_result(optimize(req, inputs), inputs.start, "optimiseur, prévision Open-Meteo",
                        "farm.json", "tunisia")


def default_plan(farm: Farm, start: date) -> PlanSchedule:
    """Plan de secours : pompage solaire 8h–16h, irrigation de chaque parcelle à tour de rôle."""
    pump = [0.0] * 24
    alloc = {p.id: [0.0] * 24 for p in farm.plots}
    ids = list(alloc)
    for h in range(8, 16):
        pump[h] = farm.pump_max_m3h * 0.75
        alloc[ids[h % len(ids)]][h] = farm.pump_max_m3h * 0.75
    return PlanSchedule(datetime.combine(start, datetime.min.time()).isoformat(), 24, pump, alloc, [1.0],
                        [5.0], farm.tank_capacity_m3 / 2, "plan par défaut")


def load_plan(mode: str = "auto") -> PlanSchedule:
    """mode : auto | api | live | default | scenario:<clé>. Enregistre le plan obtenu."""
    plan: PlanSchedule | None = None
    if mode in ("auto", "api"):
        plan = plan_from_api(get_settings().api_url)
    if plan is None and (mode in ("auto", "live") or mode.startswith("scenario:")):
        try:
            plan = plan_from_optimizer(mode.split(":", 1)[1] if mode.startswith("scenario:") else None)
        except Exception as exc:
            logger.warning("Optimiseur local indisponible (%s) : repli", exc)
    if plan is None and PLAN_FILE.exists() and mode != "default":
        plan = PlanSchedule(**json.loads(PLAN_FILE.read_text(encoding="utf-8")))
        plan.source += " (fichier)"
    if plan is None:
        plan = default_plan(load_farm(), date.today() + timedelta(days=1))
    plan.save()
    logger.info("Plan : %s, départ %s, %d h", plan.source, plan.start, plan.horizon_h)
    return plan
