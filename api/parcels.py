"""« Mes terres agricoles » : CRUD et validation des parcelles (GeoJSON, SQLite en local).

Intégré depuis la branche master (module de cartographie des terres), adapté au projet :
- base SQLite et pièces jointes sous data/ (ignorées par git, comme data/stream.db) ;
- garde-fous sur les fichiers envoyés : 10 Mo maximum, type vérifié sur le contenu, identifiant
  de document strictement UUID (aucun chemin choisi par le client) ;
- contrôle d'auto-intersection côté serveur (aquamind_core.parcel_geo) ;
- un plan cadastral extrait reste « pending_human_verification » : ses points sont en pixels.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, field_validator

from aquamind_core.config import DATA_DIR
from aquamind_core.parcel_geo import generate_rectangle_polygon, generate_segments_polygon, validate_polygon

router = APIRouter(prefix="/api/parcels", tags=["mes terres agricoles"])

DB_PATH = DATA_DIR / "parcels.db"
UPLOAD_DIR = DATA_DIR / "uploads"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

InputMethod = Literal["map_drawing", "gps_tracking", "manual_dimensions", "cadastral_document"]
PLAN_WARNING = ("Les points détectés sont en pixels du dessin, pas des coordonnées GPS. Placez le contour "
                "sur la carte, vérifiez l'échelle, la position et l'orientation, puis validez vous-même.")


# ------------------------------------------------------------------------------------------ modèles

class ParcelCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    farm_id: str | None = Field(default=None, max_length=100)
    parcel_name: str = Field(min_length=1, max_length=120)
    input_method: InputMethod
    geometry: dict[str, Any]
    source_document_id: uuid.UUID | None = None

    @field_validator("parcel_name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _clean_name(v)


class ParcelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parcel_name: str | None = Field(default=None, min_length=1, max_length=120)
    geometry: dict[str, Any] | None = None
    validation_status: Literal["pending", "validated"] | None = None

    @field_validator("parcel_name")
    @classmethod
    def _name(cls, v: str | None) -> str | None:
        return None if v is None else _clean_name(v)


def _clean_name(v: str) -> str:
    """Espaces superflus retirés ; un nom vide ou fait d'espaces est refusé."""
    v = " ".join(v.split())
    if not v:
        raise ValueError("Le nom du champ est vide")
    return v


class ValidateRequest(BaseModel):
    geometry: dict[str, Any]


class PolygonSegment(BaseModel):
    distance_m: float = Field(gt=0, le=100000)
    bearing_deg: float = Field(ge=-360, le=360, description="0° = nord, 90° = est")


class CustomPolygonRequest(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    segments: list[PolygonSegment] = Field(min_length=3, max_length=500)


class RectangleRequest(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)
    length_m: float = Field(gt=0, le=100000)
    width_m: float = Field(gt=0, le=100000)
    orientation_deg: float = Field(default=0.0, ge=-360, le=360)


class ExtractRequest(BaseModel):
    document_id: uuid.UUID


# ----------------------------------------------------------------------------------------- stockage

@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Connexion SQLite ouverte, validée (commit) puis fermée à chaque requête."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE IF NOT EXISTS parcels ("
            "parcel_id TEXT PRIMARY KEY, farm_id TEXT, parcel_name TEXT NOT NULL, "
            "input_method TEXT NOT NULL, geometry TEXT NOT NULL, area_m2 REAL, "
            "area_hectares REAL, perimeter_m REAL, validation_status TEXT NOT NULL, "
            "source_document_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        # Profil du champ (sol, soleil, cultures, puits) : colonnes ajoutées après coup → migration douce
        existing = {r[1] for r in connection.execute("PRAGMA table_info(parcels)").fetchall()}
        for column in ("profile", "environment"):
            if column not in existing:
                connection.execute(f"ALTER TABLE parcels ADD COLUMN {column} TEXT")
        with connection:                  # transaction : commit si tout va bien, rollback sinon
            yield connection


def _serialize(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "parcel_id": row["parcel_id"],
        "farm_id": row["farm_id"],
        "parcel_name": row["parcel_name"],
        "input_method": row["input_method"],
        "geometry": json.loads(row["geometry"]),
        "area_m2": row["area_m2"],
        "area_hectares": row["area_hectares"],
        "perimeter_m": row["perimeter_m"],
        "validation_status": row["validation_status"],
        "source_document_id": row["source_document_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _feature(parcel: dict[str, Any]) -> dict[str, Any]:
    """Parcelle → Feature GeoJSON (propriétés = tout sauf la géométrie)."""
    return {"type": "Feature", "id": parcel["parcel_id"], "geometry": parcel["geometry"],
            "properties": {k: v for k, v in parcel.items() if k != "geometry"}}


def _measure(geometry: dict[str, Any]) -> dict[str, float]:
    try:
        return validate_polygon(geometry)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_unique_name(connection: sqlite3.Connection, name: str, exclude_id: str | None = None) -> None:
    """Deux champs ne portent pas le même nom (comparaison sans casse ni accents différents de casse)."""
    for row in connection.execute("SELECT parcel_id, parcel_name FROM parcels").fetchall():
        if row["parcel_id"] != exclude_id and row["parcel_name"].casefold() == name.casefold():
            raise HTTPException(status_code=409, detail=f"Un champ s'appelle déjà « {row['parcel_name']} » : "
                                                        "choisissez un autre nom")


def _fetch(parcel_id: str) -> dict[str, Any]:
    with _db() as connection:
        row = connection.execute("SELECT * FROM parcels WHERE parcel_id=?", (parcel_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Parcelle introuvable")
    return _serialize(row)


# ------------------------------------------------------------------------- géométrie (sans stockage)

@router.post("/validate", summary="Valider un contour GeoJSON et calculer surface et périmètre")
def validate(request: ValidateRequest) -> dict[str, Any]:
    return {**_measure(request.geometry), "validation_status": "pending"}


@router.post("/rectangle", summary="Rectangle ou carré à partir d'un centre et de dimensions")
def rectangle(request: RectangleRequest) -> dict[str, Any]:
    geometry = generate_rectangle_polygon(request.longitude, request.latitude, request.length_m,
                                          request.width_m, request.orientation_deg)
    return {"geometry": geometry, **_measure(geometry)}


@router.post("/polygon-segments", summary="Polygone côté par côté (distance + direction depuis un coin)")
def polygon_segments(request: CustomPolygonRequest) -> dict[str, Any]:
    try:
        geometry, gap = generate_segments_polygon(
            request.longitude, request.latitude, [(s.distance_m, s.bearing_deg) for s in request.segments])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"geometry": geometry, "closing_gap_m": gap, **_measure(geometry)}


# ------------------------------------------------------------------------------------------- CRUD

@router.post("", summary="Enregistrer une parcelle (contour validé, statut « à vérifier »)")
def create_parcel(payload: ParcelCreate) -> dict[str, Any]:
    measurements = _measure(payload.geometry)
    parcel_id, now = str(uuid.uuid4()), _now()
    with _db() as connection:
        _ensure_unique_name(connection, payload.parcel_name)
        connection.execute(
            "INSERT INTO parcels (parcel_id, farm_id, parcel_name, input_method, geometry, area_m2, area_hectares, "
            "perimeter_m, validation_status, source_document_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (parcel_id, payload.farm_id, payload.parcel_name, payload.input_method,
             json.dumps(payload.geometry, ensure_ascii=False), measurements["area_m2"],
             measurements["area_hectares"], measurements["perimeter_m"], "pending",
             str(payload.source_document_id) if payload.source_document_id else None, now, now),
        )
    return _fetch(parcel_id)


@router.get("", summary="Lister les parcelles enregistrées")
def list_parcels() -> list[dict[str, Any]]:
    with _db() as connection:
        rows = connection.execute("SELECT * FROM parcels ORDER BY created_at DESC").fetchall()
    return [_serialize(row) for row in rows]


@router.get("/export.geojson", summary="Toutes les parcelles en FeatureCollection GeoJSON")
def export_geojson() -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": [_feature(p) for p in list_parcels()]}


@router.get("/{parcel_id}", summary="Lire une parcelle")
def get_parcel(parcel_id: str) -> dict[str, Any]:
    return _fetch(parcel_id)


@router.get("/{parcel_id}/geometry", summary="Contour seul (Polygon GeoJSON)")
def parcel_geometry(parcel_id: str) -> dict[str, Any]:
    return _fetch(parcel_id)["geometry"]


@router.get("/{parcel_id}/geojson", summary="Parcelle en Feature GeoJSON")
def parcel_feature(parcel_id: str) -> dict[str, Any]:
    return _feature(_fetch(parcel_id))


@router.put("/{parcel_id}", summary="Renommer, redessiner ou valider une parcelle")
def update_parcel(parcel_id: str, payload: ParcelUpdate) -> dict[str, Any]:
    _fetch(parcel_id)                     # 404 si absente
    updates = payload.model_dump(exclude_unset=True)
    values: dict[str, Any] = {}
    if updates.get("parcel_name") is not None:
        with _db() as connection:
            _ensure_unique_name(connection, updates["parcel_name"], exclude_id=parcel_id)
        values["parcel_name"] = updates["parcel_name"]
    if updates.get("geometry") is not None:
        values.update(_measure(updates["geometry"]))
        values["geometry"] = json.dumps(updates["geometry"], ensure_ascii=False)
        values["validation_status"] = "pending"   # contour modifié → à revérifier
    if updates.get("validation_status") is not None:
        values["validation_status"] = updates["validation_status"]

    if values:
        assignments = ", ".join(f"{key}=?" for key in values)   # clés fixes, jamais issues du client
        with _db() as connection:
            before = connection.execute("SELECT geometry FROM parcels WHERE parcel_id=?", (parcel_id,)).fetchone()
            connection.execute(f"UPDATE parcels SET {assignments}, updated_at=? WHERE parcel_id=?",
                               (*values.values(), _now(), parcel_id))
            if "geometry" in values and before is not None:
                from api.parcel_profile import on_geometry_change   # import tardif (dépendance circulaire)

                on_geometry_change(connection, parcel_id, json.loads(before["geometry"]), updates["geometry"])
    return _fetch(parcel_id)


@router.delete("/{parcel_id}", summary="Supprimer une parcelle")
def delete_parcel(parcel_id: str) -> dict[str, Any]:
    with _db() as connection:
        cursor = connection.execute("DELETE FROM parcels WHERE parcel_id=?", (parcel_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Parcelle introuvable")
    return {"deleted": True, "parcel_id": parcel_id}


# ------------------------------------------------------------------------------- plans cadastraux

def _sniff(content: bytes) -> str | None:
    """Type réel du fichier d'après ses premiers octets (on ne croit pas l'extension)."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"%PDF-"):
        return ".pdf"
    return None


@router.post("/upload-plan", summary="Envoyer un plan cadastral (JPG, PNG ou PDF, 10 Mo maximum)")
async def upload_plan(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="Fichier vide")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Fichier trop lourd : 10 Mo maximum")
    suffix = _sniff(content)
    if suffix is None:
        raise HTTPException(status_code=415, detail="Format autorisé : JPG, PNG ou PDF")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    document_id = str(uuid.uuid4())
    (UPLOAD_DIR / f"{document_id}{suffix}").write_bytes(content)
    return {"document_id": document_id, "filename": file.filename, "content_type": file.content_type,
            "size_bytes": len(content), "status": "uploaded"}


def _run_extraction(path) -> tuple[dict[str, Any], dict[str, Any]]:
    from aquamind_core.plan_processing import extract_drawing, extract_text, load_document

    drawing = extract_drawing(load_document(path))
    return drawing, extract_text(path)


@router.post("/extract-plan", summary="Détecter le contour d'un plan envoyé (vérification humaine requise)")
async def extract_plan(payload: ExtractRequest) -> dict[str, Any]:
    document_id = str(payload.document_id)          # UUID validé : aucun chemin arbitraire possible
    matches = [p for p in (UPLOAD_DIR / f"{document_id}{s}" for s in (".png", ".jpg", ".pdf")) if p.exists()]
    if not matches:
        raise HTTPException(status_code=404, detail="Document introuvable")
    try:
        drawing, ocr = await run_in_threadpool(_run_extraction, matches[0])
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Lecture des plans indisponible : installer opencv-python-"
                                                    "headless et PyMuPDF (requirements.txt)") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "document_id": document_id,
        "document_type": drawing["document_type"],
        "coordinate_reference_system": drawing["coordinate_reference_system"],
        "extraction_status": "pending_human_verification",
        "boundary_points": drawing["boundary_points"],
        "candidates": drawing["candidates"],
        "boundary_coordinates_are_pixel_coordinates": True,
        "image_width": drawing["image_width"],
        "image_height": drawing["image_height"],
        "extraction_confidence": drawing["extraction_confidence"],
        "extracted_area_m2": None,
        "ocr_confidence": ocr["ocr_confidence"],
        "ocr_status": ocr.get("ocr_status", "ok"),
        "ocr_text": ocr["text"][:50],
        "detected": drawing["detected"],
        "warning": PLAN_WARNING,
    }
