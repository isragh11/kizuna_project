"""Alertes, historique, commandes du simulateur, points d'entrée internes."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["flux et fuites"])


class LeakIn(BaseModel):
    section: Literal["S1", "S2", "S3", "S4"] = "S3"
    rate_m3h: float = Field(1.5, gt=0, le=10)


class FlushIn(BaseModel):
    rate_m3h: float = Field(0.6, ge=0, le=5, description="0 = arrêt du rinçage")


def _publish(request: Request, rel: str, body: dict[str, Any]) -> dict[str, Any]:
    ok = request.app.state.aqua.publish(rel, body)
    if not ok:
        raise HTTPException(503, "broker MQTT indisponible : commande non transmise")
    return {"sent": f"{request.app.state.aqua.settings.mqtt_base_topic}/{rel}", "body": body}


@router.get("/alerts", summary="Dernières alertes (les plus récentes d'abord)")
def alerts(request: Request, limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    st = request.app.state.aqua
    out = st.alerts_with_status(limit)
    if len(out) < limit and st.consumer is not None:
        known = {a.get("alert_id") for a in out}
        try:
            out += [a for a in st.consumer.storage.recent_alerts(limit) if a.get("alert_id") not in known]
        except Exception:
            pass
    return out[:limit]


@router.post("/alerts/clear", summary="Effacer l'historique des alertes (après des tests)")
def alerts_clear(request: Request) -> dict[str, Any]:
    st = request.app.state.aqua
    current = st.consumer.monitor.current.alert_id if st.consumer and st.consumer.monitor.current else None
    kept = [a for a in st.alerts if current and a.get("alert_id") == current]   # garde l'alerte en cours
    st.alerts.clear()
    st.alerts.extend(kept)
    if st.consumer is not None:
        try:
            st.consumer.storage.clear_alerts()
        except Exception as exc:
            raise HTTPException(503, f"historique en base non effacé : {exc}") from exc
    st.hub.publish({"type": "alerts_cleared"})
    return {"cleared": True, "kept_active": len(kept)}


@router.post("/internal/alerts", summary="Réception d'une alerte d'un consumer externe")
def internal_alerts(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, bool]:
    return {"accepted": request.app.state.aqua.add_external_alert(body)}


@router.post("/internal/readings", summary="Secours HTTP du simulateur quand MQTT est indisponible")
def internal_readings(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, bool]:
    c = request.app.state.aqua.consumer
    if c is None:
        raise HTTPException(503, "consumer non démarré")
    c.handle(str(body.get("topic", "")), body.get("payload") or {})
    return {"accepted": True}


@router.get("/history", summary="Historique des mesures (minutes réelles)")
def history(request: Request, kind: Literal["flow", "soil", "pv", "tank", "alert"] = "flow",
            source: str | None = Query(None, description="S1..S5, A..C, pv, tank"),
            minutes: float = Query(60, gt=0, le=1440)) -> dict[str, Any]:
    st = request.app.state.aqua
    if st.consumer is not None:
        try:
            rows = st.consumer.storage.history(kind, source, minutes)
            return {"storage": st.consumer.storage.name, "count": len(rows), "rows": rows}
        except Exception as exc:
            raise HTTPException(503, f"stockage indisponible : {exc}") from exc
    rows = [{"ts": r.ts.isoformat(), "source_id": r.source_id, "values": r.values}
            for r in st.memory.readings if r.kind == kind and (source is None or r.source_id == source)]
    return {"storage": "mémoire", "count": len(rows), "rows": rows}


@router.post("/sim/leak", summary="Injecte une fuite dans le simulateur")
def sim_leak(request: Request, body: LeakIn = Body(LeakIn())) -> dict[str, Any]:
    return _publish(request, "cmd/leak", json.loads(body.model_dump_json()))


@router.post("/sim/reset", summary="Répare : fuites et rinçage arrêtés")
def sim_reset(request: Request) -> dict[str, Any]:
    return _publish(request, "cmd/reset", {})


@router.post("/sim/flush", summary="Rinçage des filtres (dérive légitime)")
def sim_flush(request: Request, body: FlushIn = Body(FlushIn())) -> dict[str, Any]:
    return _publish(request, "cmd/flush", json.loads(body.model_dump_json()))


@router.post("/sim/new_normal", summary="Bouton « nouveau normal » : la situation actuelle devient la référence")
def sim_new_normal(request: Request) -> dict[str, Any]:
    return _publish(request, "cmd/new_normal", {"source": "api"})
