"""Mes terres agricoles : validation géographique, formes manuelles, CRUD GeoJSON et plans cadastraux.

Les 4 premiers tests viennent de la branche master ; les suivants couvrent l'intégration (API,
auto-intersection, garde-fous d'envoi de fichiers). Base SQLite et envois en dossier temporaire.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aquamind_core.parcel_geo import (find_self_intersection, generate_rectangle_polygon,
                                      generate_segments_polygon, validate_polygon)

SQUARE = {"type": "Polygon",
          "coordinates": [[[9.49, 34.98], [9.491, 34.98], [9.491, 34.981], [9.49, 34.981], [9.49, 34.98]]]}
BOWTIE = {"type": "Polygon",   # « nœud papillon » : les bords se croisent
          "coordinates": [[[9.49, 34.98], [9.491, 34.981], [9.491, 34.98], [9.49, 34.981], [9.49, 34.98]]]}


# ----------------------------------------------------------------------------- géométrie (master)

def test_valid_polygon_measures():
    result = validate_polygon(SQUARE)
    assert result["area_m2"] > 0
    assert result["area_hectares"] > 0
    assert result["perimeter_m"] > 0


def test_polygon_rejects_only_one_distinct_vertex():
    geometry = {"type": "Polygon", "coordinates": [[[9.49, 34.98], [9.49, 34.98], [9.49, 34.98], [9.49, 34.98]]]}
    with pytest.raises(ValueError):
        validate_polygon(geometry)


def test_rectangle_dimensions():
    result = validate_polygon(generate_rectangle_polygon(9.50, 34.98, 100.0, 50.0, 0.0))
    assert 4900.0 <= result["area_m2"] <= 5100.0


def test_square_dimensions():
    result = validate_polygon(generate_rectangle_polygon(9.50, 34.98, 100.0, 100.0, 15.0))
    assert 9800.0 <= result["area_m2"] <= 10200.0


# ------------------------------------------------------------------------- géométrie (intégration)

def test_square_measures_are_accurate():
    """~91 m × 111 m à Sidi Bouzid : surface et périmètre attendus à 1 % près."""
    result = validate_polygon(SQUARE)
    assert result["area_m2"] == pytest.approx(91.2 * 111.3, rel=0.01)
    assert result["perimeter_m"] == pytest.approx(2 * (91.2 + 111.3), rel=0.01)


def test_self_intersection_rejected():
    with pytest.raises(ValueError, match="se croisent"):
        validate_polygon(BOWTIE)
    assert find_self_intersection(BOWTIE["coordinates"][0]) is not None
    assert find_self_intersection(SQUARE["coordinates"][0]) is None


def test_duplicate_consecutive_points_are_tolerated():
    """Double clic ou GPS immobile : un sommet répété ne doit pas être pris pour un croisement."""
    ring = [[9.49, 34.98], [9.491, 34.98], [9.491, 34.98], [9.491, 34.981], [9.49, 34.981], [9.49, 34.98]]
    assert validate_polygon({"type": "Polygon", "coordinates": [ring]})["area_m2"] > 0


@pytest.mark.parametrize("geometry", [
    {"type": "Point", "coordinates": [9.5, 34.9]},
    {"type": "Polygon", "coordinates": [[[200, 34.98], [9.491, 34.98], [9.491, 34.981], [200, 34.98]]]},
    {"type": "Polygon", "coordinates": [[[9.49, 34.98], [9.491, 34.98], [9.492, 34.98], [9.49, 34.98]]]},  # plat
])
def test_invalid_geometries_rejected(geometry):
    with pytest.raises(ValueError):
        validate_polygon(geometry)


def test_segments_polygon_closes_and_measures():
    """100 m nord, 80 m est, 100 m sud, 80 m ouest → rectangle de 8 000 m², tour bien fermé."""
    geometry, gap = generate_segments_polygon(9.50, 34.98, [(100, 0), (80, 90), (100, 180), (80, 270)])
    assert gap < 0.5
    ring = geometry["coordinates"][0]
    assert len(ring) == 5 and ring[0] == ring[-1]          # 4 coins, pas de sommet en double
    assert validate_polygon(geometry)["area_m2"] == pytest.approx(8000, rel=0.01)


def test_segments_polygon_reports_open_gap():
    _, gap = generate_segments_polygon(9.50, 34.98, [(100, 0), (80, 90), (100, 180)])
    assert gap == pytest.approx(80, rel=0.01)              # le 4e côté (80 m) est fermé automatiquement


# -------------------------------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from api import parcels
    from api.main import create_app

    monkeypatch.setattr(parcels, "DB_PATH", tmp_path / "parcels.db")
    monkeypatch.setattr(parcels, "UPLOAD_DIR", tmp_path / "uploads")
    with TestClient(create_app(start_background=False)) as c:
        yield c


def test_crud_roundtrip(client):
    r = client.post("/api/parcels", json={"parcel_name": "Champ A", "input_method": "map_drawing",
                                          "geometry": SQUARE})
    assert r.status_code == 200, r.text
    parcel = r.json()
    pid = parcel["parcel_id"]
    assert parcel["validation_status"] == "pending" and parcel["area_hectares"] > 0.9

    assert client.get(f"/api/parcels/{pid}").json()["parcel_name"] == "Champ A"
    assert [p["parcel_id"] for p in client.get("/api/parcels").json()] == [pid]

    r = client.put(f"/api/parcels/{pid}", json={"validation_status": "validated", "parcel_name": "Tomates"})
    assert r.json()["validation_status"] == "validated" and r.json()["parcel_name"] == "Tomates"

    # Contour modifié → la parcelle repasse « à vérifier » et les mesures sont recalculées
    rect = generate_rectangle_polygon(9.50, 34.98, 100, 50)
    r = client.put(f"/api/parcels/{pid}", json={"geometry": rect})
    assert r.json()["validation_status"] == "pending"
    assert r.json()["area_m2"] == pytest.approx(5000, rel=0.02)

    feature = client.get(f"/api/parcels/{pid}/geojson").json()
    assert feature["type"] == "Feature" and feature["geometry"]["type"] == "Polygon"
    collection = client.get("/api/parcels/export.geojson").json()
    assert collection["type"] == "FeatureCollection" and len(collection["features"]) == 1

    assert client.delete(f"/api/parcels/{pid}").json()["deleted"] is True
    assert client.get(f"/api/parcels/{pid}").status_code == 404
    assert client.delete(f"/api/parcels/{pid}").status_code == 404


def test_create_rejects_bad_geometry_and_extra_fields(client):
    base = {"parcel_name": "X", "input_method": "map_drawing"}
    assert client.post("/api/parcels", json={**base, "geometry": BOWTIE}).status_code == 422
    assert client.post("/api/parcels", json={**base, "geometry": SQUARE, "admin": True}).status_code == 422
    assert client.post("/api/parcels", json={**base, "input_method": "drone", "geometry": SQUARE}).status_code == 422


def test_validate_rectangle_and_segments_endpoints(client):
    assert client.post("/api/parcels/validate", json={"geometry": SQUARE}).json()["validation_status"] == "pending"
    assert client.post("/api/parcels/validate", json={"geometry": BOWTIE}).status_code == 422

    r = client.post("/api/parcels/rectangle", json={"longitude": 9.5, "latitude": 34.98, "length_m": 100,
                                                    "width_m": 50, "orientation_deg": 30})
    assert r.json()["area_m2"] == pytest.approx(5000, rel=0.02)
    assert client.post("/api/parcels/rectangle", json={"longitude": 9.5, "latitude": 34.98, "length_m": 0,
                                                       "width_m": 50}).status_code == 422

    r = client.post("/api/parcels/polygon-segments", json={
        "longitude": 9.5, "latitude": 34.98,
        "segments": [{"distance_m": 100, "bearing_deg": 0}, {"distance_m": 80, "bearing_deg": 90},
                     {"distance_m": 100, "bearing_deg": 180}, {"distance_m": 80, "bearing_deg": 270}]})
    assert r.status_code == 200 and r.json()["closing_gap_m"] < 0.5


def _plan_png() -> bytes:
    """Plan synthétique : un quadrilatère noir épais sur fond blanc."""
    import cv2
    import numpy as np

    img = np.full((600, 800, 3), 255, np.uint8)
    pts = np.array([[150, 120], [650, 100], [700, 480], [120, 500]], np.int32)
    cv2.polylines(img, [pts], True, (0, 0, 0), 6)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_upload_and_extract_plan(client):
    r = client.post("/api/parcels/upload-plan", files={"file": ("plan.png", _plan_png(), "image/png")})
    assert r.status_code == 200, r.text
    doc = r.json()["document_id"]

    r = client.post("/api/parcels/extract-plan", json={"document_id": doc})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["extraction_status"] == "pending_human_verification"
    assert out["boundary_coordinates_are_pixel_coordinates"] is True
    assert out["coordinate_reference_system"] is None and out["extracted_area_m2"] is None
    assert out["detected"] and 3 <= len(out["boundary_points"]) <= 8
    assert "pas des coordonnées GPS" in out["warning"]

    # La parcelle finale peut citer le document source
    r = client.post("/api/parcels", json={"parcel_name": "Plan", "input_method": "cadastral_document",
                                          "geometry": SQUARE, "source_document_id": doc})
    assert r.json()["source_document_id"] == doc and r.json()["validation_status"] == "pending"


def test_upload_guards(client):
    # Type mensonger : un exécutable déclaré comme image
    r = client.post("/api/parcels/upload-plan", files={"file": ("plan.png", b"MZ\x90\x00binaire", "image/png")})
    assert r.status_code == 415
    assert client.post("/api/parcels/upload-plan", files={"file": ("vide.png", b"", "image/png")}).status_code == 422
    big = b"%PDF-" + b"0" * (10 * 1024 * 1024)
    assert client.post("/api/parcels/upload-plan", files={"file": ("gros.pdf", big, "application/pdf")}).status_code == 413
    # Identifiant de document : UUID uniquement (pas de chemin)
    assert client.post("/api/parcels/extract-plan", json={"document_id": "../../.env"}).status_code == 422
    assert client.post("/api/parcels/extract-plan",
                       json={"document_id": "00000000-0000-4000-8000-000000000000"}).status_code == 404


# --------------------------------------------------------------------- garde-fous (robustesse)

def test_tiny_and_huge_shapes_rejected():
    tiny = {"type": "Polygon", "coordinates": [[[9.49, 34.98], [9.49002, 34.98], [9.49002, 34.98002], [9.49, 34.98]]]}
    with pytest.raises(ValueError, match="trop petit"):
        validate_polygon(tiny)                       # ~2 m² : clic accidentel
    zoomed_out = generate_rectangle_polygon(9.5, 34.98, 60000, 40000)
    with pytest.raises(ValueError, match="trop étendu"):
        validate_polygon(zoomed_out)                 # 60 km : tracé en dézoomant


def test_names_trimmed_unique_and_not_blank(client):
    body = {"input_method": "map_drawing", "geometry": SQUARE}
    r = client.post("/api/parcels", json={**body, "parcel_name": "  Oliviers   du  bas "})
    assert r.json()["parcel_name"] == "Oliviers du bas"
    pid = r.json()["parcel_id"]
    dup = client.post("/api/parcels", json={**body, "parcel_name": "OLIVIERS DU BAS"})
    assert dup.status_code == 409 and "déjà" in dup.json()["detail"]
    assert client.post("/api/parcels", json={**body, "parcel_name": "   "}).status_code == 422
    other = client.post("/api/parcels", json={**body, "parcel_name": "Tomates"}).json()["parcel_id"]
    assert client.put(f"/api/parcels/{other}", json={"parcel_name": "oliviers du bas"}).status_code == 409
    assert client.put(f"/api/parcels/{pid}", json={"parcel_name": "Oliviers du Bas"}).status_code == 200  # soi-même


def test_plan_frame_and_title_block_are_not_the_parcel():
    """Plan réaliste : cadre de page + cartouche rectangulaire + parcelle → la parcelle d'abord."""
    import cv2
    import numpy as np

    from aquamind_core.plan_processing import extract_drawing

    img = np.full((1000, 1400, 3), 255, np.uint8)
    cv2.rectangle(img, (8, 8), (1391, 991), (0, 0, 0), 4)              # cadre de page
    cv2.rectangle(img, (40, 40), (1360, 960), (0, 0, 0), 3)            # cadre intérieur (> 50 % de la page)
    cv2.rectangle(img, (1000, 780), (1330, 930), (0, 0, 0), 3)         # cartouche
    parcel = np.array([[250, 200], [800, 170], [880, 620], [520, 700], [220, 560]], np.int32)
    cv2.polylines(img, [parcel], True, (0, 0, 0), 8)                   # parcelle à 5 bornes, trait épais
    out = extract_drawing(img)
    assert out["detected"]
    first = [(p["x"], p["y"]) for p in out["candidates"][0]["boundary_points"]]
    assert len(first) == 5
    assert all(min(abs(x - px) + abs(y - py) for x, y in first) < 25 for px, py in parcel)
    # le trait épais ne produit pas deux fois le même candidat
    boxes = [cv2.boundingRect(np.array([[p["x"], p["y"]] for p in c["boundary_points"]])) for c in out["candidates"]]
    assert len(boxes) == len(set(boxes))


def test_realistic_plan_with_neighbours_and_text():
    """Parcelles voisines accolées + cotes écrites le long des bords + cartouche : la parcelle
    principale (la plus compacte et la plus grande) arrive en premier, avec ses 5 bornes."""
    import cv2
    import numpy as np

    from aquamind_core.plan_processing import extract_drawing

    img = np.full((1130, 1600, 3), 255, np.uint8)
    cv2.rectangle(img, (20, 20), (1579, 1109), (0, 0, 0), 4)
    cv2.rectangle(img, (1130, 900), (1540, 1070), (0, 0, 0), 2)
    for i, t in enumerate(["Plan parcellaire", "Echelle 1/2000"]):
        cv2.putText(img, t, (1145, 940 + 40 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
    for poly in ([[150, 220], [440, 230], [450, 590], [170, 560]], [[860, 200], [1180, 190], [1150, 470], [900, 450]]):
        cv2.polylines(img, [np.array(poly, np.int32)], True, (90, 90, 90), 2)          # voisins accolés
    parcel = np.array([[440, 230], [860, 200], [900, 610], [640, 650], [450, 590]], np.int32)
    cv2.polylines(img, [parcel], True, (0, 0, 0), 6)
    cv2.putText(img, "PARCELLE 112", (560, 400), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
    cv2.putText(img, "210,4 m", (640, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)
    out = extract_drawing(img)
    first = [(p["x"], p["y"]) for p in out["candidates"][0]["boundary_points"]]
    assert len(first) == 5
    cx, cy = np.mean(first, axis=0)
    assert 600 < cx < 720 and 380 < cy < 470          # centre de la parcelle 112 (~ 666, 427)
