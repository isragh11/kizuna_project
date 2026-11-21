"""Actionneurs : pompe, vannes, arrêt d'urgence. Toute commande passe par safety.check_command.

Réponse : 200 si acceptée, 409 si refusée par un garde-fou (le corps donne la raison), 503 si acceptée
mais non transmise (broker absent). Le champ `source` identifie l'origine : dashboard, agent, esp32…
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aquamind_core.safety import ActuatorCommand

router = APIRouter(tags=["actionneurs"])
Source = Literal["dashboard", "agent", "esp32", "api", "test"]


class Origin(BaseModel):
    source: Source = Field("dashboard", description="qui envoie : dashboard, agent, esp32, api")
    actor: str = Field("", description="identifiant lisible (utilisateur, nom de l'agent, id de carte)")
    reason: str = Field("", description="motif (obligatoire pour relâcher l'arrêt d'urgence)")


class PumpIn(Origin):
    on: bool
    duration_min: float | None = Field(None, description="durée max avant arrêt automatique (défaut 30, max 120)")


class ValveIn(Origin):
    open: bool


class EstopIn(Origin):
    engaged: bool = Field(True, description="true = arrêt d'urgence ; false = le relâcher (humain + motif)")


def _respond(entry: dict[str, Any]) -> JSONResponse:
    code = 200 if entry["accepted"] and entry.get("delivered", True) else (409 if not entry["accepted"] else 503)
    return JSONResponse(entry, status_code=code)


@router.post("/actuators/pump", summary="Pompe principale marche/arrêt (garde-fous appliqués)")
def pump(request: Request, body: PumpIn = Body(..., openapi_examples={
        "marche": {"value": {"on": True, "duration_min": 20, "source": "dashboard", "actor": "demo"}},
        "arret": {"value": {"on": False, "source": "dashboard", "actor": "demo"}}})) -> JSONResponse:
    cmd = ActuatorCommand(source=body.source, actor=body.actor, target="pump", action="on" if body.on else "off",
                          duration_min=body.duration_min, reason=body.reason)
    return _respond(request.app.state.aqua.actuators.command(cmd))


@router.post("/actuators/valve/{valve_id}", summary="Vanne d'une parcelle ouverte/fermée")
def valve(request: Request, valve_id: Literal["A", "B", "C"], body: ValveIn = Body(..., openapi_examples={
        "ouvrir": {"value": {"open": True, "source": "dashboard", "actor": "demo"}}})) -> JSONResponse:
    cmd = ActuatorCommand(source=body.source, actor=body.actor, target="valve",
                          action="open" if body.open else "close", valve_id=valve_id, reason=body.reason)
    return _respond(request.app.state.aqua.actuators.command(cmd))


@router.post("/actuators/isolate/{valve_id}",
             summary="ISOLER une parcelle : arrêt pompe → fermeture vanne → alerte (atomique)")
def isolate(request: Request, valve_id: Literal["A", "B", "C"], body: Origin = Body(Origin())) -> JSONResponse:
    st = request.app.state.aqua
    out = st.actuators.isolate(valve_id, body.source, body.actor, body.reason)
    if out["accepted"]:
        section = {"A": "S2", "B": "S3", "C": "S4"}[valve_id]
        alert = {
            "alert_id": f"iso-{out['steps'][-1]['command']['command_id']}", "ts": datetime.now().isoformat(),
            "section": section, "detector": "isolation", "score": 0.0,
            "est_loss_m3_day": 0.0, "est_loss_kwh_day": 0.0, "est_loss_tnd_day": 0.0, "confirmed_by_ai": False,
            "message": (f"Parcelle {valve_id} isolée par {body.source}{' / ' + body.actor if body.actor else ''} : "
                        f"pompe arrêtée puis vanne {valve_id} fermée. Les autres parcelles sont à l'arrêt : "
                        "les rouvrir ou revenir au plan automatique quand la fuite est maîtrisée."),
        }
        st.add_external_alert(alert)
        out["alert"] = alert
    code = 200 if out["accepted"] and out["delivered"] else (409 if not out["accepted"] else 503)
    return JSONResponse(out, status_code=code)


@router.post("/actuators/auto", summary="Retour au pilotage automatique (plan de l'optimiseur)")
def auto(request: Request, body: Origin = Body(Origin())) -> JSONResponse:
    cmd = ActuatorCommand(source=body.source, actor=body.actor, target="mode", action="auto", reason=body.reason)
    return _respond(request.app.state.aqua.actuators.command(cmd))


@router.post("/emergency-stop", summary="ARRÊT D'URGENCE (prioritaire sur tout) ou relâchement")
def emergency_stop(request: Request, body: EstopIn = Body(EstopIn())) -> JSONResponse:
    cmd = ActuatorCommand(source=body.source, actor=body.actor, target="estop",
                          action="engage" if body.engaged else "release", reason=body.reason)
    return _respond(request.app.state.aqua.actuators.command(cmd))


@router.get("/actuators/state", summary="État des actionneurs (tel que rapporté par l'appareil)")
def state(request: Request) -> dict[str, Any]:
    return request.app.state.aqua.actuators.snapshot()


@router.get("/actuators/log", summary="Journal des commandes (qui, quoi, quand, acceptée/refusée, pourquoi)")
def log(request: Request, limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    return request.app.state.aqua.actuators.journal.last(limit)
