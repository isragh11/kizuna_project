"""Optimisation et scénarios."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request

from aquamind_core.optimizer import optimize as run_optimize
from aquamind_core.scenarios import SCENARIOS, run_scenario
from aquamind_core.schemas import OptimizeRequest, OptimizeResult

router = APIRouter(tags=["optimisation"])

EXAMPLE = {
    "site": "tunisia", "date": "2025-07-20", "water_quota_m3": 60, "horizon_h": 48, "pv_scale": 1.0,
    "temp_offset_c": 0, "s_min": 0, "quota_mode": "horizon",
}


@router.post("/optimize", response_model=OptimizeResult, summary="Calcule un plan (scénario « Et si ? »)")
def optimize(request: Request, req: OptimizeRequest = Body(openapi_examples={
        "canicule": {"summary": "Canicule du 20/07/2025 (archive)", "value": EXAMPLE},
        "quota_divise": {"summary": "Quota ÷ 2", "value": {**EXAMPLE, "water_quota_m3": 30}},
        "nuageux": {"summary": "Ensoleillement 50 %", "value": {**EXAMPLE, "pv_scale": 0.5}}}),
        apply: bool = Query(False, description="true : devient le plan suivi par la ferme (/optimize/last)")
        ) -> OptimizeResult:
    """Dates ≤ 2025 : météo réelle de l'archive ; après : prévision Open-Meteo (7 derniers jours requis).
    Par défaut le plan n'est PAS appliqué : les curseurs « Et si ? » ne changent pas l'irrigation réelle."""
    try:
        result = run_optimize(req)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, f"données météo indisponibles pour cette date/site : {exc}") from exc
    if apply:
        request.app.state.aqua.set_plan(result, f"POST /optimize ({req.site}, {req.date})", "manual")
    return result


@router.get("/optimize/last", response_model=OptimizeResult, summary="Dernier plan appliqué (suivi par la ferme)")
def optimize_last(request: Request) -> OptimizeResult:
    plan = request.app.state.aqua.last_plan
    if plan is None:
        raise HTTPException(404, "aucun plan appliqué pour l'instant (calcul automatique en cours ?)")
    return plan


@router.post("/optimize/refresh", summary="Recalcule le plan automatique (prévision de demain)")
def optimize_refresh(request: Request) -> dict[str, Any]:
    st = request.app.state.aqua
    ok = st.refresh_auto_plan(force=True)
    if not ok:
        raise HTTPException(503, "prévision ou optimisation indisponible (voir les journaux)")
    return {"plan": st.plan_meta, "kpis": st.last_plan.kpis}


@router.get("/scenarios", summary="Scénarios de démonstration")
def scenarios() -> list[dict[str, Any]]:
    return [{"key": s.key, "title": s.title, "description": s.description, "site": s.site,
             "start": s.start.isoformat(), "quota_factor": s.quota_factor} for s in SCENARIOS.values()]


@router.get("/scenarios/{key}", summary="Rejoue un scénario (KPI, explications, valeur de l'horizon 48 h)")
def scenario(request: Request, key: str, apply: bool = Query(False, description="true : la ferme suit ce plan")
             ) -> dict[str, Any]:
    if key not in SCENARIOS:
        raise HTTPException(404, f"scénario inconnu ; disponibles : {list(SCENARIOS)}")
    sc, result, hv = run_scenario(key)
    if apply:
        request.app.state.aqua.set_plan(result, f"scénario {key}", "manual")
    return {"scenario": {"key": sc.key, "title": sc.title, "description": sc.description},
            "horizon_value": hv, "result": result.model_dump()}
