"""Profil d'un champ (sol, soleil, puits, zones) et recommandations panneaux / capteurs.

Aucun appel réseau : l'environnement (SoilGrids, PVGIS, Open-Meteo) est remplacé par des valeurs fixes.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from aquamind_core.land_reco import load_equipment, monthly_kc, sensor_recommendation, solar_recommendation
from aquamind_core.parcel_geo import (LocalFrame, centroid, check_zones, generate_rectangle_polygon as rect,
                                      point_in_ring, validate_polygon)

LON, LAT = 9.4995, 34.9805
DX = 1 / (111195 * 0.8194)            # degrés de longitude pour 1 m à cette latitude
FIELD = rect(LON, LAT, 160, 100)       # 1,6 ha
ENV = {
    "centroid": [LON, LAT],
    "soil": {"texture": "limon", "awc_mm_per_m": 150, "advice": "", "source": "test", "assumption": False},
    "sun": {"kwh_per_kwc_year": 1733, "optimal_tilt_deg": 34, "optimal_azimuth_deg": -5, "label": "Excellent",
            "kwh_per_kwc_month": [131.5, 128.6, 150.2, 152.0, 160.1, 156.3, 165.4, 163.0, 146.7, 140.1, 125.0, 120.0],
            "source": "test", "assumption": False},
    "climate": {"et0_mm_day": [2.56, 2.75, 4.34, 4.69, 5.68, 6.95, 7.91, 6.59, 5.08, 3.91, 2.91, 1.99],
                "rain_mm_month": [21.5, 24.4, 11.2, 28.8, 43.3, 7.3, 0.6, 8.9, 23.1, 12.6, 19.8, 32.8],
                "source": "test", "assumption": False},
}


def strip(name, crop, x_m, method="drip", width=40):
    """Bande nord-sud de 40 m × 100 m centrée à x_m mètres à l'est du centre du champ."""
    return {"id": name.lower(), "name": name, "crop": crop, "irrigation_method": method,
            "geometry": rect(LON + x_m * DX, LAT, width, 100)}


THREE = [strip("Tomates", "tomate", -60), strip("Oliviers", "olivier", -20), strip("Blé", "ble", 20, "sprinkler")]


# ------------------------------------------------------------------------------------- calculs

def test_monthly_kc_follows_crop_calendar():
    ble, tomate, olivier = monthly_kc("ble"), monthly_kc("tomate"), monthly_kc("olivier")
    assert ble[6] == 0.0 and ble[1] > 1.0            # blé d'hiver : rien en juillet, plein en février
    assert tomate[0] == 0.0 and tomate[6] > 1.0      # tomate d'été
    assert olivier[0] > 0 and olivier[6] == pytest.approx(0.70)


def test_solar_sizing_matches_hand_calculation():
    s = solar_recommendation(FIELD, {"zones": THREE, "well_depth_m": 60}, ENV)
    assert s["status"] == "ok" and s["design_month"] == "juil."
    # juillet : tomate 0,4 ha × 7,91 × Kc 1,149 / 0,9 + olivier 0,4 ha × 7,91 × 0,70 / 0,9 ; pluie ≈ 0 ; blé hors saison
    eff_rain = 0.8 * 0.6 / 31
    want = ((7.91 * 1.149 - eff_rain) + (7.91 * 0.70 - eff_rain)) * 0.4 * 10 / 0.9
    assert s["design_water_m3_day"] == pytest.approx(want, rel=0.02)
    assert s["head_m"] == 72                          # 60 m de puits + 12 m de goutte-à-goutte (le blé ne pompe pas en juillet)
    eq = load_equipment()["solar"]
    assert s["panels"] * eq["panel_wp"] / 1000 >= s["pump_kw"] * eq["pv_over_pump_power"] - 1e-9
    assert s["total_tnd"] == sum(l["total_tnd"] for l in s["cost_lines"])
    assert s["layout"]["rows"] * s["layout"]["cols"] - s["panels"] < s["layout"]["cols"]   # pas de rangée vide


def test_more_sun_shade_or_deeper_well_changes_panel_count():
    base = solar_recommendation(FIELD, {"zones": THREE, "well_depth_m": 60}, ENV)["panels"]
    deeper = solar_recommendation(FIELD, {"zones": THREE, "well_depth_m": 120}, ENV)["panels"]
    shaded = solar_recommendation(FIELD, {"zones": THREE, "well_depth_m": 60, "shading": "fort"}, ENV)["panels"]
    assert deeper > base and shaded > base


def test_panels_placed_in_free_area_north_side_without_shading_crops():
    s = solar_recommendation(FIELD, {"zones": THREE, "well_depth_m": 60}, ENV)
    frame = LocalFrame(*centroid(FIELD))
    fp = frame.ring_xy(s["footprint"])
    outer = frame.ring_xy(FIELD)
    assert s["inside"] and not s["shades_crops"]
    assert all(point_in_ring(p, outer) or min(abs(p[0] - 80), abs(p[1] - 50), abs(p[1] + 50)) < 0.6 for p in fp)
    assert min(p[0] for p in fp) >= 40 - 0.6         # dans la bande libre à l'est (x > 40 m), pas sur les cultures
    assert max(p[1] for p in fp) > 30                # côté nord : l'ombre part hors du champ


def test_panels_go_near_the_well_when_known():
    well = {"lon": LON + 75 * DX, "lat": LAT - 45 / 111195}        # coin sud-est
    s = solar_recommendation(FIELD, {"zones": THREE, "well_depth_m": 60, "well": well}, ENV)
    assert s["inside"] and s["distance_to_well_m"] < 15


def test_fully_planted_field_puts_panels_outside_to_the_north():
    full = [strip("A", "tomate", -40, width=80), strip("B", "olivier", 40, width=80)]
    s = solar_recommendation(FIELD, {"zones": full}, ENV)
    frame = LocalFrame(*centroid(FIELD))
    fp = frame.ring_xy(s["footprint"])
    assert not s["inside"] and min(p[1] for p in fp) > 50     # entièrement au nord du champ
    assert "appartient" in s["where"]


def test_no_zones_or_no_need():
    assert solar_recommendation(FIELD, {"zones": []}, ENV)["status"] == "no_zones"
    wet = {**ENV, "climate": {**ENV["climate"], "rain_mm_month": [400.0] * 12}}
    assert solar_recommendation(FIELD, {"zones": THREE}, wet)["status"] == "no_need"


def test_sensor_plan_counts_and_positions():
    solar = solar_recommendation(FIELD, {"zones": THREE, "well_depth_m": 60}, ENV)
    plan = sensor_recommendation({"geometry": FIELD, "created_at": "2026-01-01"}, {"zones": THREE, "has_tank": True}, solar, [])
    by = {i["key"]: i for i in plan["items"]}
    assert by["soil_moisture"]["count"] == 3                    # 1 par zone (0,4 ha chacune)
    assert by["flow_meter"]["count"] == 4                       # pompe + 3 secteurs
    assert by["pressure"]["count"] == 4                         # pompe + bout des 3 secteurs sous pression
    assert by["weather_station"]["count"] == 1 and by["gateway"]["count"] == 1 and by["tank_level"]["count"] == 1
    assert plan["devices"] == sum(i["count"] for i in plan["items"])
    assert plan["total_tnd"] == plan["equipment_tnd"] + plan["installation_tnd"]
    frame = LocalFrame(*centroid(FIELD))
    for z, p in zip(THREE, by["soil_moisture"]["points"]):     # chaque sonde dans sa zone
        assert point_in_ring(frame.to_xy(p["lon"], p["lat"]), frame.ring_xy(z["geometry"]))
    assert "30 et 60 cm" in by["soil_moisture"]["points"][1]["label"]    # olivier : racines profondes


def test_big_zone_gets_more_probes_and_station_is_shared_with_neighbour():
    big = rect(LON, LAT, 300, 200)                              # 6 ha, une seule culture
    zones = [{"id": "z", "name": "Blé", "crop": "ble", "irrigation_method": "sprinkler", "geometry": big}]
    near = [{"parcel_id": "x", "parcel_name": "Oliviers du haut", "created_at": "2025-01-01", "centroid": (LON + 0.01, LAT)}]
    plan = sensor_recommendation({"geometry": big, "created_at": "2026-01-01"}, {"zones": zones}, None, near)
    by = {i["key"]: i for i in plan["items"]}
    assert by["soil_moisture"]["count"] == 3                    # 6 ha / 2 ha
    assert by["weather_station"]["count"] == 0 and "Oliviers du haut" in by["weather_station"]["where"]
    # voisin à 0,91 km (< 3 km) et plus ancien : la passerelle est partagée aussi
    assert by["gateway"]["count"] == 0 and by["gateway"]["shared_with"] == "Oliviers du haut"
    far = [{**near[0], "centroid": (LON + 0.1, LAT)}]          # ~9 km : rien de partagé
    by_far = {i["key"]: i for i in sensor_recommendation({"geometry": big, "created_at": "2026-01-01"},
                                                         {"zones": zones}, None, far)["items"]}
    assert by_far["weather_station"]["count"] == 1 and by_far["gateway"]["count"] == 1


def test_check_zones_detects_overlap_and_outside():
    assert check_zones(FIELD, THREE) == []
    bad = THREE + [strip("Hors", "tomate", 100)]
    assert any("dépasse" in e for e in check_zones(FIELD, bad))
    over = [strip("A", "tomate", 0, width=60), strip("B", "olivier", 20, width=60)]
    assert any("recouvrent" in e for e in check_zones(FIELD, over))


# -------------------------------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from api import parcel_profile, parcels
    from api.main import create_app

    monkeypatch.setattr(parcels, "DB_PATH", tmp_path / "parcels.db")
    monkeypatch.setattr(parcels, "UPLOAD_DIR", tmp_path / "uploads")
    calls = []
    monkeypatch.setattr(parcel_profile, "environment", lambda g: calls.append(1) or json.loads(json.dumps(ENV)))
    with TestClient(create_app(start_background=False)) as c:
        c.env_calls = calls
        yield c


def _new(client, name="Champ test", geometry=FIELD):
    r = client.post("/api/parcels", json={"parcel_name": name, "input_method": "map_drawing", "geometry": geometry})
    assert r.status_code == 200, r.text
    return r.json()["parcel_id"]


def test_crops_route_not_taken_for_a_parcel_id(client):
    keys = [c["key"] for c in client.get("/api/parcels/crops").json()]
    assert {"tomate", "olivier", "ble", "autre"} <= set(keys)


def test_profile_roundtrip_and_validation(client):
    pid = _new(client)
    p = client.get(f"/api/parcels/{pid}/profile").json()
    assert p["environment"] is None and p["profile"]["zones"] == [] and p["unplanted_m2"] == pytest.approx(16000, rel=0.01)

    r = client.put(f"/api/parcels/{pid}/profile", json={"zones": THREE, "well_depth_m": 45, "shading": "partiel",
                                                         "soil_override": "sable", "has_tank": True,
                                                         "well": {"lon": LON + 75 * DX, "lat": LAT}})
    assert r.status_code == 200, r.text
    v = r.json()
    assert len(v["profile"]["zones"]) == 3 and v["planted_m2"] == pytest.approx(12000, rel=0.01)
    assert v["soil"]["texture"] == "sable" and v["soil"]["source"] == "indiqué par vous"
    # renvoyer les zones telles que reçues (avec area_m2) est accepté
    assert client.put(f"/api/parcels/{pid}/profile", json={"zones": v["profile"]["zones"]}).status_code == 200

    def err(body):
        r = client.put(f"/api/parcels/{pid}/profile", json=body)
        return r.status_code, json.dumps(r.json(), ensure_ascii=False)
    assert err({"zones": THREE + [strip("Hors", "tomate", 100)]})[0] == 422
    code, msg = err({"zones": [strip("A", "tomate", 0, width=60), strip("B", "olivier", 20, width=60)]})
    assert code == 422 and "recouvrent" in msg
    assert err({"zones": [strip("A", "banane volante", 0)]})[0] == 422
    assert err({"zones": [THREE[0], THREE[0]]})[0] == 422                       # même identifiant
    assert err({"soil_override": "lave"})[0] == 422
    assert err({"well_depth_m": -3})[0] == 422
    code, msg = err({"well": {"lon": LON + 0.1, "lat": LAT}})
    assert code == 422 and "2 km" in msg


def test_recommendations_fetch_environment_once(client):
    pid = _new(client)
    client.put(f"/api/parcels/{pid}/profile", json={"zones": THREE, "well_depth_m": 60})
    r = client.get(f"/api/parcels/{pid}/recommendations").json()
    assert r["solar"]["status"] == "ok" and r["solar"]["panels"] > 0 and r["sensors"]["devices"] > 0
    assert r["assumption"] is True and len(client.env_calls) == 1
    client.get(f"/api/parcels/{pid}/recommendations")
    assert len(client.env_calls) == 1                                # en cache
    client.post(f"/api/parcels/{pid}/environment")
    assert len(client.env_calls) == 2                                # rafraîchi à la demande


def test_station_shared_between_two_nearby_fields(client):
    first = _new(client, "Premier")
    second = _new(client, "Second", rect(LON + 0.003, LAT, 100, 100))
    s1 = {i["key"]: i for i in client.get(f"/api/parcels/{first}/recommendations").json()["sensors"]["items"]}
    s2 = {i["key"]: i for i in client.get(f"/api/parcels/{second}/recommendations").json()["sensors"]["items"]}
    # une seule station pour les deux champs (≈ 270 m d'écart) ; l'autre champ indique qui la porte
    counts = sorted([s1["weather_station"]["count"], s2["weather_station"]["count"]])
    assert counts == [0, 1]
    sharer = s1 if s1["weather_station"]["count"] == 0 else s2
    assert sharer["weather_station"]["shared_with"] in ("Premier", "Second")


def test_redrawing_the_field_flags_zones_and_moving_it_drops_environment(client):
    pid = _new(client)
    client.put(f"/api/parcels/{pid}/profile", json={"zones": THREE})
    client.post(f"/api/parcels/{pid}/environment")
    # contour rétréci sur place : les zones dépassent → à revérifier ; environnement conservé
    client.put(f"/api/parcels/{pid}", json={"geometry": rect(LON, LAT, 100, 100)})
    p = client.get(f"/api/parcels/{pid}/profile").json()
    assert p["profile"]["zones_review"] and p["zone_issues"] and p["environment"] is not None
    # champ déplacé de ~1 km : l'environnement sera refait
    client.put(f"/api/parcels/{pid}", json={"geometry": rect(LON + 0.011, LAT, 100, 100)})
    assert client.get(f"/api/parcels/{pid}/profile").json()["environment"] is None


def test_old_database_is_migrated(tmp_path, monkeypatch):
    from api import parcels
    from api.main import create_app

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE parcels (parcel_id TEXT PRIMARY KEY, farm_id TEXT, parcel_name TEXT NOT NULL, "
                "input_method TEXT NOT NULL, geometry TEXT NOT NULL, area_m2 REAL, area_hectares REAL, perimeter_m REAL, "
                "validation_status TEXT NOT NULL, source_document_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    con.execute("INSERT INTO parcels VALUES ('old', NULL, 'Ancien', 'map_drawing', ?, 1, 0.0001, 4, 'pending', NULL, '2026', '2026')",
                (json.dumps(FIELD),))
    con.commit(); con.close()
    monkeypatch.setattr(parcels, "DB_PATH", db)
    with TestClient(create_app(start_background=False)) as c:
        assert c.get("/api/parcels/old/profile").status_code == 200
        assert c.post("/api/parcels", json={"parcel_name": "Neuf", "input_method": "map_drawing", "geometry": FIELD}).status_code == 200
        assert len(c.get("/api/parcels").json()) == 2
