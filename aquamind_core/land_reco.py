"""Recommandations d'équipement pour un champ : panneaux solaires (pompage) et capteurs.

Toutes les valeurs de prix viennent de data/static/equipment.json (HYPOTHÈSES) : les coûts sont des
estimations à faire confirmer par un installateur.

Panneaux solaires (pompage au fil du soleil)
  1. Besoin en eau par mois et par zone : ET0 du mois × Kc du mois (calendrier cultural FAO-56)
     − pluie efficace, ÷ efficience de l'arrosage.
  2. Énergie de pompage : hauteur = profondeur du puits + pression du réseau d'arrosage.
  3. Mois le plus exigeant = celui où (énergie nécessaire ÷ production d'1 kWc) est maximale : on
     dimensionne pour lui (+ 10 % de marge), et au moins 1,3 × la puissance de la pompe.
  4. Emplacement : rangées est-ouest face à l'équateur, espacées pour ne pas se faire d'ombre au
     solstice d'hiver ; dans une partie non cultivée du champ, du côté où l'ombre ne tombe pas sur les
     cultures (au nord dans l'hémisphère nord), au plus près du puits ; sinon juste à l'extérieur.

Capteurs : 1 sonde d'humidité par zone (+1 par tranche de 2 ha), compteur et pression à la pompe et par
secteur, station météo et passerelle radio partagées entre champs voisins.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import lru_cache
from math import atan, ceil, cos, degrees, radians, sin, sqrt, tan
from typing import Any

from aquamind_core.config import STATIC_DIR, load_crop_calendar, load_crops
from aquamind_core.crop_calendar import crop_state
from aquamind_core.energy import pump_kwh_per_m3
from aquamind_core.parcel_geo import (LocalFrame, centroid, distance_to_ring, grid_points, point_in_ring,
                                      ring_area_xy, rings_overlap, spread_points, validate_polygon)
from aquamind_core.schemas import Crop

DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
EFFECTIVE_RAIN = 0.8


@lru_cache
def load_equipment() -> dict[str, Any]:
    with open(STATIC_DIR / "equipment.json", encoding="utf-8") as f:
        return json.load(f)


def crop_choices() -> list[dict[str, Any]]:
    """Cultures proposées dans le découpage du champ (+ « autre »)."""
    crops = load_crops()
    out = [{"key": k, "name": c.name, "perennial": c.perennial} for k, c in crops.items()]
    out.append({"key": "autre", "name": load_equipment()["generic_crop"]["name"], "perennial": False})
    return out


@lru_cache
def monthly_kc(crop_key: str) -> tuple[float, ...]:
    """Kc moyen de chaque mois (0 hors saison) d'après le calendrier cultural ; Kc moyen d'hypothèse
    toute l'année pour une culture inconnue."""
    crops = load_crops()
    cal = load_crop_calendar()["crops"]
    if crop_key not in crops or crop_key not in cal:
        g = load_equipment()["generic_crop"]
        return tuple([g["kc_mid"]] * 12)
    crop: Crop = crops[crop_key]
    out = []
    for m in range(1, 13):
        d0 = date(2025, m, 1)
        vals = []
        for i in range(DAYS[m - 1]):
            st = crop_state(crop, cal[crop_key], d0 + timedelta(days=i))
            vals.append(st[0] if st else 0.0)
        out.append(round(sum(vals) / len(vals), 3))
    return tuple(out)


def _zone_need(zone: dict[str, Any], climate: dict[str, Any], eff: float) -> list[float]:
    """Besoin brut (m³/jour) de la zone pour chaque mois."""
    area_ha = validate_polygon(zone["geometry"])["area_hectares"]
    kc = monthly_kc(zone.get("crop") or "autre")
    out = []
    for m in range(12):
        net = max(0.0, climate["et0_mm_day"][m] * kc[m] - EFFECTIVE_RAIN * climate["rain_mm_month"][m] / DAYS[m])
        out.append(net * area_ha * 10.0 / eff)
    return out


# ------------------------------------------------------------------------------------- panneaux

def _array_layout(n_panels: int, tilt_deg: float, lat: float, eq: dict[str, Any]) -> dict[str, float]:
    """Rangées est-ouest de panneaux en portrait ; écartement sans ombre au solstice d'hiver à midi."""
    L, W = eq["panel_length_m"], eq["panel_width_m"]
    sun_min = max(15.0, 90.0 - abs(lat) - 23.44)                    # hauteur du soleil, midi, 21 déc.
    t = radians(tilt_deg)
    row_depth = L * cos(t)
    pitch = row_depth + L * sin(t) / tan(radians(sun_min))
    # champ de panneaux à peu près carré, sans rangée à moitié vide
    rows = max(1, round(sqrt(n_panels * W / pitch)))
    cols = ceil(n_panels / rows)
    rows = ceil(n_panels / cols)
    margin = eq["row_margin_m"]
    width = cols * W + 2 * margin
    depth = (rows - 1) * pitch + row_depth + 2 * margin
    shadow = (L * sin(t) + 0.5) / tan(radians(sun_min))             # ombre portée derrière la dernière rangée
    return {"rows": rows, "cols": cols, "width_m": round(width, 1), "depth_m": round(depth, 1),
            "pitch_m": round(pitch, 2), "shadow_m": round(shadow, 1), "winter_sun_deg": round(sun_min, 1)}


def _rect(cx: float, cy: float, w: float, d: float) -> list[tuple[float, float]]:
    return [(cx - w / 2, cy - d / 2), (cx + w / 2, cy - d / 2), (cx + w / 2, cy + d / 2), (cx - w / 2, cy + d / 2)]


def _place_array(frame: LocalFrame, outer, zone_rings, well_xy, layout, north: bool) -> dict[str, Any]:
    """Meilleur emplacement du champ de panneaux (repère local, mètres)."""
    w, d, shadow = layout["width_m"], layout["depth_m"], layout["shadow_m"]
    sgn = 1.0 if north else -1.0
    step = max(1.0, min(w, d) / 3)
    best, best_key = None, None
    for cx, cy in grid_points(outer, step):
        rect = _rect(cx, cy, w, d)
        if not all(point_in_ring(p, outer) for p in rect):
            continue
        if any(rings_overlap(rect, z) or point_in_ring((cx, cy), z) for z in zone_rings):
            continue
        # bande d'ombre : du côté du pôle (nord dans l'hémisphère nord), sur `shadow` mètres
        sy = cy + sgn * (d / 2 + shadow / 2)
        shade = _rect(cx, sy, w, shadow)
        shades_crops = any(rings_overlap(shade, z) for z in zone_rings)
        to_well = sqrt((cx - well_xy[0]) ** 2 + (cy - well_xy[1]) ** 2) if well_xy else 0.0
        key = (shades_crops, to_well if well_xy else -sgn * cy)
        if best_key is None or key < best_key:
            best, best_key = (cx, cy, shades_crops, to_well), key
    if best:
        cx, cy, shades, to_well = best
        where = ("dans une partie non cultivée du champ" +
                 (", près du puits" if well_xy else (", côté nord" if north else ", côté sud")) +
                 (" (attention : leur ombre peut atteindre une culture en hiver)" if shades else
                  " : leur ombre ne tombe pas sur les cultures"))
        return {"inside": True, "center": (cx, cy), "where": where, "shades_crops": shades,
                "distance_to_well_m": round(to_well) if well_xy else None}
    # Pas de place libre dans le champ : juste à l'extérieur, du côté du pôle (pas d'ombre sur les cultures)
    xs, ys = [p[0] for p in outer], [p[1] for p in outer]
    cx = min(max(well_xy[0], min(xs) + w / 2), max(xs) - w / 2) if well_xy and max(xs) - min(xs) > w else sum(xs) / len(xs)
    cy = (max(ys) + d / 2 + 2.0) if north else (min(ys) - d / 2 - 2.0)
    to_well = sqrt((cx - well_xy[0]) ** 2 + (cy - well_xy[1]) ** 2) if well_xy else None
    return {"inside": False, "center": (cx, cy), "shades_crops": False,
            "where": "le champ est entièrement cultivé : à l'extérieur, juste " + ("au nord" if north else "au sud") +
                     " du champ (pas d'ombre sur les cultures). Vérifiez que ce terrain vous appartient.",
            "distance_to_well_m": round(to_well) if to_well is not None else None}


def solar_recommendation(parcel_geom: dict[str, Any], profile: dict[str, Any], env: dict[str, Any]) -> dict[str, Any]:
    eq = load_equipment()["solar"]
    zones = profile.get("zones") or []
    if not zones:
        return {"status": "no_zones", "message": "Indiquez d'abord ce qui est planté (onglet « Cultures ») : "
                                                 "le nombre de panneaux dépend de l'eau à pomper."}
    climate, sun = env["climate"], env["sun"]
    lon0, lat0 = centroid(parcel_geom)
    depth_m = float(profile.get("well_depth_m") or eq["default_well_depth_m"])
    shading = eq["shading_factor"].get(profile.get("shading") or "aucun", 1.0)

    # Chaque zone a sa pression (goutte-à-goutte, aspersion…) et n'est arrosée que pendant sa saison :
    # l'énergie se calcule zone par zone, mois par mois.
    per_zone, heads = [], []
    for z in zones:
        method = z.get("irrigation_method") or "drip"
        eff = eq["irrigation_efficiency"].get(method, 0.8)
        heads.append(depth_m + eq["pressure_head_m"].get(method, 10))
        per_zone.append(_zone_need(z, climate, eff))
    need = [sum(z[m] for z in per_zone) for m in range(12)]            # m³/jour
    if max(need) <= 0.01:
        return {"status": "no_need", "message": "Avec ces cultures et ce climat, aucun arrosage n'est nécessaire : "
                                                "pas besoin de pompage solaire."}
    energy_day = [sum(pz[m] * pump_kwh_per_m3(h, eq["pump_efficiency"]) for pz, h in zip(per_zone, heads))
                  for m in range(12)]                                        # kWh/jour
    yield_day = [sun["kwh_per_kwc_month"][m] / DAYS[m] * shading for m in range(12)]   # kWh/kWc/jour
    ratios = [energy_day[m] / yield_day[m] if yield_day[m] > 0 else 0.0 for m in range(12)]
    design = max(range(12), key=lambda m: ratios[m])
    # hauteur la plus forte parmi les zones arrosées le mois le plus exigeant
    head = max(h for pz, h in zip(per_zone, heads) if pz[design] > 0.01)
    kwh_m3 = energy_day[design] / need[design]
    q_peak = need[design] / eq["pumping_hours_per_day"]                     # m³/h
    pump_kw = 9.81 * (q_peak / 3600) * head * 1.10 / eq["pump_efficiency"]
    kwc_needed = max(ratios[design] * (1 + eq["sizing_margin"]), pump_kw * eq["pv_over_pump_power"])
    n_panels = max(1, ceil(kwc_needed * 1000 / eq["panel_wp"]))
    kwc = n_panels * eq["panel_wp"] / 1000

    # coûts
    inverter_kw = max(eq["pump_inverter_min_kw"], ceil(pump_kw * 1.25 * 2) / 2)
    lines = [
        {"item": f"Panneaux solaires {eq['panel_wp']} W", "qty": n_panels, "unit_tnd": eq["panel_price_tnd"],
         "total_tnd": n_panels * eq["panel_price_tnd"]},
        {"item": "Supports (structure au sol)", "qty": n_panels, "unit_tnd": eq["structure_per_panel_tnd"],
         "total_tnd": n_panels * eq["structure_per_panel_tnd"]},
        {"item": f"Variateur de pompe solaire {inverter_kw:g} kW", "qty": 1,
         "unit_tnd": round(inverter_kw * eq["pump_inverter_tnd_per_kw"]),
         "total_tnd": round(inverter_kw * eq["pump_inverter_tnd_per_kw"])},
        {"item": "Câbles et protections", "qty": 1, "unit_tnd": round(kwc * eq["cabling_protection_tnd_per_kwc"]),
         "total_tnd": round(kwc * eq["cabling_protection_tnd_per_kwc"])},
    ]
    subtotal = sum(l["total_tnd"] for l in lines)
    install = round(subtotal * eq["installation_share"])
    lines.append({"item": "Pose et mise en service", "qty": 1, "unit_tnd": install, "total_tnd": install})
    total = subtotal + install

    # bilan annuel
    monthly = []
    used_total = need_total_kwh = water_total = 0.0
    for m in range(12):
        need_kwh = energy_day[m] * DAYS[m]
        pv_kwh = kwc * sun["kwh_per_kwc_month"][m] * shading
        used = min(need_kwh, pv_kwh)
        used_total += used
        need_total_kwh += need_kwh
        water_total += need[m] * DAYS[m]
        monthly.append({"month": MONTHS[m], "need_kwh": round(need_kwh), "pv_kwh": round(pv_kwh),
                        "water_m3": round(need[m] * DAYS[m])})
    savings = used_total * eq["grid_price_tnd_per_kwh"]
    payback = total / savings if savings > 0 else None

    # emplacement
    frame = LocalFrame(lon0, lat0)
    outer = frame.ring_xy(parcel_geom)
    zone_rings = [frame.ring_xy(z["geometry"]) for z in zones]
    well = profile.get("well")
    well_xy = frame.to_xy(well["lon"], well["lat"]) if well else None
    layout = _array_layout(n_panels, sun["optimal_tilt_deg"], lat0, eq)
    place = _place_array(frame, outer, zone_rings, well_xy, layout, north=lat0 >= 0)
    cx, cy = place.pop("center")
    footprint = frame.polygon(_rect(cx, cy, layout["width_m"], layout["depth_m"]))
    az = sun["optimal_azimuth_deg"]
    facing = ("plein sud" if lat0 >= 0 else "plein nord") if abs(az % 180) <= 10 or abs(az % 180) >= 170 else \
        f"{'sud' if lat0 >= 0 else 'nord'} ({az:+d}°)"

    return {
        "status": "ok",
        "panels": n_panels, "panel_wp": eq["panel_wp"], "kwc": round(kwc, 2),
        "pump_kw": round(pump_kw, 2), "inverter_kw": inverter_kw, "head_m": round(head, 1),
        "kwh_per_m3": round(kwh_m3, 3), "design_month": MONTHS[design],
        "design_water_m3_day": round(need[design], 1),
        "water_m3_year": round(water_total), "energy_kwh_year": round(need_total_kwh),
        "solar_share_pct": round(100 * used_total / need_total_kwh) if need_total_kwh else 0,
        "tilt_deg": sun["optimal_tilt_deg"], "facing": facing,
        "layout": layout, "footprint": footprint, "area_m2": round(layout["width_m"] * layout["depth_m"]),
        **place,
        "cost_lines": lines, "total_tnd": total, "savings_tnd_year": round(savings),
        "payback_years": round(payback, 1) if payback else None,
        "monthly": monthly, "shading_factor": shading, "well_depth_m": depth_m,
        "well_assumed": not profile.get("well_depth_m"),
    }


# ------------------------------------------------------------------------------------- capteurs

def _ll(frame: LocalFrame, p, label: str) -> dict[str, Any]:
    lon, lat = frame.to_lonlat(*p)
    return {"lon": lon, "lat": lat, "label": label}


def _inside_near(ring, target, inset: float = 2.0):
    """Point du bord de la zone le plus proche de `target`, ramené de `inset` m vers l'intérieur."""
    v = min(ring, key=lambda p: (p[0] - target[0]) ** 2 + (p[1] - target[1]) ** 2)
    c = (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
    dist = sqrt((c[0] - v[0]) ** 2 + (c[1] - v[1]) ** 2) or 1.0
    return (v[0] + (c[0] - v[0]) * inset / dist, v[1] + (c[1] - v[1]) * inset / dist)


def sensor_recommendation(parcel: dict[str, Any], profile: dict[str, Any], solar: dict[str, Any] | None,
                          neighbours: list[dict[str, Any]]) -> dict[str, Any]:
    """`neighbours` : autres champs [{parcel_id, parcel_name, centroid: (lon, lat), created_at}]."""
    eq = load_equipment()["sensors"]
    crops = load_crops()
    geom = parcel["geometry"]
    lon0, lat0 = centroid(geom)
    frame = LocalFrame(lon0, lat0)
    outer = frame.ring_xy(geom)
    zones = profile.get("zones") or []
    zone_rings = [frame.ring_xy(z["geometry"]) for z in zones]
    if not zones:                                        # pas encore de découpage : le champ entier
        zones = [{"name": "Champ entier", "crop": None, "irrigation_method": "drip"}]
        zone_rings = [outer]
    solar_ring = frame.ring_xy(solar["footprint"]) if solar and solar.get("status") == "ok" and solar.get("inside") else None
    avoid = [solar_ring] if solar_ring else []

    well = profile.get("well")
    if well:
        pump = frame.to_xy(well["lon"], well["lat"])
        pump_where = "à la sortie de la pompe (puits)"
    elif solar_ring:
        pump = (sum(p[0] for p in solar_ring) / 4, sum(p[1] for p in solar_ring) / 4)
        pump_where = "à la sortie de la pompe (placez le puits sur la carte pour plus de précision)"
    else:
        pump = _inside_near(outer, (0.0, 0.0), 0.0)
        pump_where = "à la sortie de la pompe (position du puits inconnue)"

    items: list[dict[str, Any]] = []

    def add(key: str, count: int, why: str, where: str, points: list[dict[str, Any]], shared: str | None = None):
        spec = eq[key]
        items.append({"key": key, "label": spec["label"], "count": count, "unit_tnd": spec["unit_tnd"],
                      "total_tnd": count * spec["unit_tnd"], "why": why, "where": where, "points": points,
                      "shared_with": shared})

    # 1. Humidité du sol : une sonde par zone homogène, + 1 par tranche de 2 ha
    pts, total = [], 0
    for z, ring in zip(zones, zone_rings):
        area_ha = ring_area_xy(ring) / 10_000
        k = max(1, ceil(round(area_ha / eq["soil_moisture"]["ha_per_probe"], 3)))   # 6,000001 ha → 3, pas 4
        crop = crops.get(z.get("crop") or "")
        depths = "30 et 60 cm" if crop and crop.perennial else "15 et 30 cm"
        for i, p in enumerate(spread_points(ring, k, avoid=avoid)):
            pts.append(_ll(frame, p, f"{z.get('name')} — sonde {i + 1} ({depths})"))
        total += k
    add("soil_moisture", total,
        "Mesure l'eau réellement disponible pour les racines : on arrose quand le sol sèche, pas au calendrier. "
        "Une par culture (chaque culture boit différemment), et une de plus par tranche de 2 ha.",
        "au milieu de chaque zone, loin des bords, sur une ligne de goutteurs, à la profondeur des racines",
        pts)

    # 2. Compteurs d'eau : un à la pompe, un par secteur si plusieurs zones
    pts = [_ll(frame, pump, "Compteur principal (pompe)")]
    if len(zones) > 1:
        pts += [_ll(frame, _inside_near(r, pump), f"Compteur — {z.get('name')}") for z, r in zip(zones, zone_rings)]
    add("flow_meter", len(pts),
        "Compte l'eau envoyée à chaque culture : vérifie que le plan d'arrosage est respecté et révèle une fuite "
        "(l'eau sortie de la pompe ≠ la somme des secteurs).",
        pump_where + (" et à l'entrée (vanne) de chaque zone" if len(zones) > 1 else ""), pts)

    # 3. Pression : à la pompe et au bout de chaque secteur sous pression (goutteurs bouchés, fuite)
    pts = [_ll(frame, pump, "Pression — sortie de pompe")]
    for z, r in zip(zones, zone_rings):
        if (z.get("irrigation_method") or "drip") in ("drip", "sprinkler"):
            far = max(r, key=lambda p: (p[0] - pump[0]) ** 2 + (p[1] - pump[1]) ** 2)
            pts.append(_ll(frame, _inside_near(r, far), f"Pression — bout de {z.get('name')}"))
    add("pressure", len(pts),
        "Une pression qui baisse au bout de la ligne signale des goutteurs bouchés ou une fuite ; à la pompe, "
        "elle protège la pompe (marche à sec, vanne fermée).",
        "à la sortie de la pompe et au point le plus éloigné de chaque secteur", pts)

    # 4. Station météo et passerelle radio : partagées entre champs proches
    def rank(p: dict[str, Any]) -> tuple[str, str]:
        # ordre de création, puis identifiant : deux champs créés la même seconde ont quand même un ordre
        return (p.get("created_at") or "", p.get("parcel_id") or "")

    def owner(radius_km: float) -> dict[str, Any] | None:
        """Champ voisin (plus ancien) qui porte déjà l'équipement partagé, ou None."""
        mine = rank(parcel)
        for n in sorted(neighbours, key=rank):
            nx, ny = frame.to_xy(*n["centroid"])
            if sqrt(nx * nx + ny * ny) <= radius_km * 1000 and rank(n) < mine:
                return n
        return None

    st_owner = owner(eq["weather_station"]["share_radius_km"])
    if st_owner:
        add("weather_station", 0, "Pluie, température, vent et humidité de l'air : affinent le calcul des besoins.",
            f"déjà comptée pour « {st_owner['parcel_name']} » (à moins de {eq['weather_station']['share_radius_km']} km)",
            [], st_owner["parcel_name"])
    else:
        open_rings = [r for r in zone_rings if r is not outer] + avoid
        spot = spread_points(outer, 1, avoid=open_rings)[0]
        add("weather_station", 1,
            "Pluie, température, vent et humidité de l'air sur place : affinent le calcul des besoins (la météo "
            "de la ville voisine peut être très différente). Une seule suffit pour vos champs à moins de 5 km.",
            "dans un endroit dégagé, à 2 m de haut, loin des arbres, des murs et des panneaux",
            [_ll(frame, spot, "Station météo")])

    gw_owner = owner(eq["gateway"]["share_radius_km"])
    if gw_owner:
        add("gateway", 0, "Relaie les mesures des capteurs vers AquaMind.",
            f"déjà comptée pour « {gw_owner['parcel_name']} » (portée ~{eq['gateway']['share_radius_km']} km)",
            [], gw_owner["parcel_name"])
    else:
        add("gateway", 1,
            "Relaie par radio (LoRa, quelques km) les mesures de tous les capteurs vers AquaMind, sans câble.",
            "près de la pompe ou des panneaux (alimentation électrique), en hauteur",
            [_ll(frame, pump, "Passerelle radio")])

    if profile.get("has_tank"):
        add("tank_level", 1, "Indique en permanence l'eau disponible dans la cuve (évite la marche à sec).",
            "dans la cuve", [_ll(frame, pump, "Niveau de cuve")])

    devices = sum(i["count"] for i in items)
    equipment = sum(i["total_tnd"] for i in items)
    install = devices * eq["installation_per_device_tnd"]
    return {"items": items, "devices": devices, "equipment_tnd": equipment, "installation_tnd": install,
            "total_tnd": equipment + install, "zones_assumed": not profile.get("zones")}
