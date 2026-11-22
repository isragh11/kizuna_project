"""Profil d'un champ (sol, soleil, puits, cultures par zone) et recommandations d'équipement.

Routes (même préfixe que api/parcels.py, enregistrées AVANT lui pour que /crops ne soit pas pris pour
un identifiant de champ) :
  GET  /api/parcels/crops                       cultures proposées pour le découpage
  GET  /api/parcels/{id}/profile                profil + environnement en cache (sans appel réseau)
  PUT  /api/parcels/{id}/profile                saisies de l'agriculteur (sol, ombre, puits, zones)
  POST /api/parcels/{id}/environment            (re)télécharge sol, soleil et climat au centre du champ
  GET  /api/parcels/{id}/recommendations        panneaux solaires + capteurs (coûts = estimations)
"""

from __future__ import annotations

import json
import sqlite3
from math import sqrt
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from aquamind_core.land_profile import SOIL_CLASSES, effective_soil, environment
from aquamind_core.land_reco import crop_choices, load_equipment, sensor_recommendation, solar_recommendation
from aquamind_core.parcel_geo import LocalFrame, centroid, check_zones, validate_polygon
from api.parcels import _db, _fetch

router = APIRouter(prefix="/api/parcels", tags=["mes terres agricoles"])

MAX_ZONES = 30
MAX_WELL_DISTANCE_M = 2000
STALE_CENTROID_M = 100


class Point(BaseModel):
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class Zone(BaseModel):
    model_config = ConfigDict(extra="ignore")     # area_m2 renvoyé par l'API peut revenir tel quel : ignoré
    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=60)
    crop: str = Field(min_length=1, max_length=30)
    crop_label: str | None = Field(default=None, max_length=60)
    irrigation_method: Literal["drip", "sprinkler", "surface"] = "drip"
    geometry: dict[str, Any]

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Le nom de la zone est vide")
        return v

    @field_validator("crop")
    @classmethod
    def _crop(cls, v: str) -> str:
        if v not in {c["key"] for c in crop_choices()}:
            raise ValueError(f"Culture inconnue : {v}")
        return v


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    soil_override: str | None = None
    shading: Literal["aucun", "partiel", "fort"] | None = None
    well: Point | None = None
    well_depth_m: float | None = Field(default=None, gt=0, le=500)
    has_tank: bool | None = None
    zones: list[Zone] | None = Field(default=None, max_length=MAX_ZONES)

    @field_validator("soil_override")
    @classmethod
    def _soil(cls, v: str | None) -> str | None:
        if v is not None and v not in SOIL_CLASSES:
            raise ValueError("Type de sol inconnu")
        return v


# ------------------------------------------------------------------------------------- stockage

def _load(parcel_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    parcel = _fetch(parcel_id)                                          # 404 si absent
    with _db() as connection:
        row = connection.execute("SELECT profile, environment FROM parcels WHERE parcel_id=?", (parcel_id,)).fetchone()
    profile = json.loads(row["profile"]) if row and row["profile"] else {}
    env = json.loads(row["environment"]) if row and row["environment"] else None
    return parcel, profile, env


def _store(parcel_id: str, profile: dict[str, Any] | None = None, env: dict[str, Any] | None = None) -> None:
    with _db() as connection:
        if profile is not None:
            connection.execute("UPDATE parcels SET profile=? WHERE parcel_id=?",
                               (json.dumps(profile, ensure_ascii=False), parcel_id))
        if env is not None:
            connection.execute("UPDATE parcels SET environment=? WHERE parcel_id=?",
                               (json.dumps(env, ensure_ascii=False), parcel_id))


def on_geometry_change(connection: sqlite3.Connection, parcel_id: str, old: dict[str, Any], new: dict[str, Any]) -> None:
    """Contour redessiné : environnement à refaire s'il s'est déplacé, découpage en zones à revérifier."""
    row = connection.execute("SELECT profile FROM parcels WHERE parcel_id=?", (parcel_id,)).fetchone()
    profile = json.loads(row["profile"]) if row and row["profile"] else {}
    frame = LocalFrame(*centroid(old))
    dx, dy = frame.to_xy(*centroid(new))
    if sqrt(dx * dx + dy * dy) > STALE_CENTROID_M:
        connection.execute("UPDATE parcels SET environment=NULL WHERE parcel_id=?", (parcel_id,))
    if profile.get("zones"):
        profile["zones_review"] = bool(check_zones(new, profile["zones"]))
        connection.execute("UPDATE parcels SET profile=? WHERE parcel_id=?",
                           (json.dumps(profile, ensure_ascii=False), parcel_id))


def _view(parcel: dict[str, Any], profile: dict[str, Any], env: dict[str, Any] | None) -> dict[str, Any]:
    zones = profile.get("zones") or []
    for z in zones:
        z["area_m2"] = validate_polygon(z["geometry"])["area_m2"]
    planted = sum(z["area_m2"] for z in zones)
    issues = check_zones(parcel["geometry"], zones) if zones else []
    return {
        "parcel": parcel,
        "profile": {"soil_override": profile.get("soil_override"), "shading": profile.get("shading") or "aucun",
                    "well": profile.get("well"), "well_depth_m": profile.get("well_depth_m"),
                    "has_tank": bool(profile.get("has_tank")), "zones": zones,
                    "zones_review": bool(profile.get("zones_review")) or bool(issues)},
        "zone_issues": issues,
        "planted_m2": round(planted, 1),
        "unplanted_m2": round(max(0.0, parcel["area_m2"] - planted), 1),
        "environment": env,
        "soil": effective_soil(env, profile),
        "soil_classes": [{"key": k, **v} for k, v in SOIL_CLASSES.items()],
        "default_well_depth_m": load_equipment()["solar"]["default_well_depth_m"],
    }


# --------------------------------------------------------------------------------------- routes

@router.get("/crops", summary="Cultures proposées pour découper un champ")
def crops() -> list[dict[str, Any]]:
    return crop_choices()


@router.get("/{parcel_id}/profile", summary="Profil du champ (sans appel réseau)")
def get_profile(parcel_id: str) -> dict[str, Any]:
    return _view(*_load(parcel_id))


@router.put("/{parcel_id}/profile", summary="Enregistrer sol, ombre, puits et cultures par zone")
def put_profile(parcel_id: str, payload: ProfileUpdate) -> dict[str, Any]:
    parcel, profile, env = _load(parcel_id)
    updates = payload.model_dump(exclude_unset=True)
    if "zones" in updates and updates["zones"] is not None:
        ids = [z["id"] for z in updates["zones"]]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Deux zones ont le même identifiant")
        errors = check_zones(parcel["geometry"], updates["zones"])
        if errors:
            raise HTTPException(422, " ; ".join(errors))
        profile["zones"] = updates["zones"]
        profile["zones_review"] = False
    if updates.get("well"):
        frame = LocalFrame(*centroid(parcel["geometry"]))
        x, y = frame.to_xy(updates["well"]["lon"], updates["well"]["lat"])
        if sqrt(x * x + y * y) > MAX_WELL_DISTANCE_M:
            raise HTTPException(422, "Le puits est à plus de 2 km du champ : vérifiez sa position")
    for key in ("soil_override", "shading", "well", "well_depth_m", "has_tank"):
        if key in updates:
            profile[key] = updates[key]
    _store(parcel_id, profile=profile)
    return _view(parcel, profile, env)


@router.post("/{parcel_id}/environment", summary="Télécharger sol, soleil et climat au centre du champ")
async def refresh_environment(parcel_id: str) -> dict[str, Any]:
    parcel, profile, _ = _load(parcel_id)
    env = await run_in_threadpool(environment, parcel["geometry"])
    _store(parcel_id, env=env)
    return _view(parcel, profile, env)


@router.get("/{parcel_id}/recommendations", summary="Panneaux solaires et capteurs recommandés (estimations)")
async def recommendations(parcel_id: str) -> dict[str, Any]:
    parcel, profile, env = _load(parcel_id)
    if env is None:                                   # premier passage : on récupère l'environnement
        env = await run_in_threadpool(environment, parcel["geometry"])
        _store(parcel_id, env=env)
    profile_now = {**profile, "zones": profile.get("zones") or []}
    solar = solar_recommendation(parcel["geometry"], profile_now, env)
    with _db() as connection:
        rows = connection.execute("SELECT parcel_id, parcel_name, geometry, created_at FROM parcels WHERE parcel_id != ?",
                                  (parcel_id,)).fetchall()
    neighbours = [{"parcel_id": r["parcel_id"], "parcel_name": r["parcel_name"], "created_at": r["created_at"],
                   "centroid": centroid(json.loads(r["geometry"]))} for r in rows]
    sensors = sensor_recommendation(parcel, profile_now, solar, neighbours)
    eq = load_equipment()
    return {"solar": solar, "sensors": sensors, "assumption": True,
            "note": eq["_note"], "env_sources": {k: env[k]["source"] for k in ("soil", "sun", "climate")},
            "env_assumption": any(env[k].get("assumption") for k in ("soil", "sun", "climate"))}
