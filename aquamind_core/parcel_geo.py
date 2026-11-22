"""Validation géographique et mesures locales des parcelles (« Mes terres agricoles »).

Intégré depuis la branche master (module de cartographie des terres), complété par :
- le contrôle d'auto-intersection côté serveur (les bords d'un champ ne doivent pas se croiser) ;
- la construction d'un polygone par « distance + direction » (relevé à la boussole ou au décamètre).

Les surfaces sont calculées sur une projection locale (équirectangulaire autour du champ) :
écart < 0,1 % pour un champ de quelques hectares, largement suffisant pour l'irrigation.
"""

from __future__ import annotations

from math import asin, atan2, cos, degrees, pi, radians, sin, sqrt
from typing import Any

# Rayon terrestre moyen (IUGG), le même que @turf/area côté navigateur : une forme construite ici
# (« 2,5 ha ») s'affiche exactement « 2,5 ha » sur la carte. 1° de latitude = 111 195 m.
EARTH_RADIUS_M = 6371008.8
M_PER_DEG_LAT = EARTH_RADIUS_M * pi / 180.0

# Garde-fous de bon sens (mêmes valeurs dans web/src/lib/parcels.ts)
MIN_AREA_M2 = 10.0                # en dessous : clic accidentel, pas un champ
MAX_AREA_M2 = 100_000_000.0       # 10 000 ha : au-delà, contour tracé en dézoomant par erreur
MAX_SPAN_M = 20_000.0             # aucun côté du rectangle englobant au-delà de 20 km


def _project(ring: list[list[float]]) -> list[tuple[float, float]]:
    """Longitude/latitude → mètres (x vers l'est, y vers le nord) autour de la latitude moyenne."""
    lat0 = sum(p[1] for p in ring) / len(ring)
    m_per_deg_lon = M_PER_DEG_LAT * cos(radians(lat0))
    return [(p[0] * m_per_deg_lon, p[1] * M_PER_DEG_LAT) for p in ring]


def _ring_area_m2(coordinates: list[list[float]]) -> float:
    """Surface d'un anneau (formule du lacet sur la projection locale)."""
    if len(coordinates) < 3:
        return 0.0
    projected = _project(coordinates)
    if projected[0] != projected[-1]:
        projected.append(projected[0])
    doubled = sum(projected[i][0] * projected[i + 1][1] - projected[i + 1][0] * projected[i][1]
                  for i in range(len(projected) - 1))
    return abs(doubled) / 2.0


def _orient(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> bool:
    return min(a[0], b[0]) - 1e-9 <= p[0] <= max(a[0], b[0]) + 1e-9 and \
        min(a[1], b[1]) - 1e-9 <= p[1] <= max(a[1], b[1]) + 1e-9


def _segments_cross(p1, p2, p3, p4) -> bool:
    """Vrai si [p1p2] et [p3p4] se touchent (croisement, contact ou recouvrement)."""
    d1, d2 = _orient(p3, p4, p1), _orient(p3, p4, p2)
    d3, d4 = _orient(p1, p2, p3), _orient(p1, p2, p4)
    eps = 1e-9
    if ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and \
            ((d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps)):
        return True
    return ((abs(d1) <= eps and _on_segment(p3, p4, p1)) or (abs(d2) <= eps and _on_segment(p3, p4, p2))
            or (abs(d3) <= eps and _on_segment(p1, p2, p3)) or (abs(d4) <= eps and _on_segment(p1, p2, p4)))


def find_self_intersection(ring: list[list[float]]) -> tuple[int, int] | None:
    """Indices de deux bords non voisins qui se croisent, ou None. `ring` est fermé."""
    pts = _project(ring)
    n = len(pts) - 1                      # nombre de bords
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue                  # bords voisins : partagent un sommet, normal
            if _segments_cross(pts[i], pts[i + 1], pts[j], pts[j + 1]):
                return i, j
    return None


def _clean_ring(normalized: list[list[float]]) -> list[list[float]]:
    """Retire les sommets répétés consécutifs (double clic, GPS immobile) et ferme l'anneau."""
    out: list[list[float]] = []
    for p in normalized:
        if not out or (round(p[0], 9), round(p[1], 9)) != (round(out[-1][0], 9), round(out[-1][1], 9)):
            out.append(p)
    if len(out) > 1 and out[0] == out[-1]:
        out.pop()
    return out + [out[0].copy()] if out else out


def validate_polygon(geometry: dict[str, Any]) -> dict[str, float]:
    """Valide un Polygon GeoJSON et retourne sa surface (m², ha) et son périmètre (m).

    Lève ValueError avec un message en français si le contour est inutilisable.
    """
    if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
        raise ValueError("La géométrie doit être de type Polygon")

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("Coordonnées de polygone manquantes")

    ring = coordinates[0]
    if not isinstance(ring, list) or len(ring) < 4:
        raise ValueError("Le polygone doit contenir au moins quatre points fermés")
    if len(ring) > 5000:
        raise ValueError("Trop de sommets (5 000 maximum)")

    normalized: list[list[float]] = []
    for point in ring:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            raise ValueError("Chaque sommet doit contenir longitude et latitude")
        try:
            longitude, latitude = float(point[0]), float(point[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("Coordonnée non numérique") from exc
        if not (-180.0 <= longitude <= 180.0 and -90.0 <= latitude <= 90.0):
            raise ValueError("Coordonnée géographique hors limites")
        normalized.append([longitude, latitude])

    normalized = _clean_ring(normalized)
    unique = len({(round(p[0], 8), round(p[1], 8)) for p in normalized})
    if unique < 3:
        raise ValueError("Le polygone doit contenir au moins trois sommets distincts")

    # Croisement vérifié AVANT la surface : un « nœud papillon » symétrique a une surface algébrique
    # nulle, et le message « bords qui se croisent » est le seul utile à l'agriculteur.
    if find_self_intersection(normalized) is not None:
        raise ValueError("Les bords du champ se croisent (polygone auto-intersecté) : "
                         "déplacez les sommets pour que le contour fasse le tour sans se recouper")

    xy = _project(normalized)
    span = max(max(p[0] for p in xy) - min(p[0] for p in xy), max(p[1] for p in xy) - min(p[1] for p in xy))
    if span > MAX_SPAN_M:
        raise ValueError(f"Contour trop étendu ({span / 1000:.0f} km de long) : il a sans doute été tracé "
                         "en dézoomant. Zoomez sur votre champ et recommencez")

    area_m2 = _ring_area_m2(normalized)
    if area_m2 < MIN_AREA_M2:
        raise ValueError(f"Champ trop petit ({area_m2:.1f} m²) : les coins sont presque alignés ou "
                         "confondus. Vérifiez le contour")
    if area_m2 > MAX_AREA_M2:
        raise ValueError(f"Contour démesuré ({area_m2 / 10000:.0f} ha) : vérifiez le zoom et les coins")

    perimeter_m = 0.0
    for i in range(len(normalized) - 1):
        lon1, lat1 = normalized[i]
        lon2, lat2 = normalized[i + 1]
        lat_mid = radians((lat1 + lat2) / 2.0)
        dx = (lon2 - lon1) * M_PER_DEG_LAT * cos(lat_mid)
        dy = (lat2 - lat1) * M_PER_DEG_LAT
        perimeter_m += sqrt(dx * dx + dy * dy)

    return {
        "area_m2": round(area_m2, 2),
        "area_hectares": round(area_m2 / 10000.0, 6),
        "perimeter_m": round(perimeter_m, 2),
    }


def generate_rectangle_polygon(longitude: float, latitude: float,
                               length_m: float, width_m: float,
                               orientation_deg: float = 0.0) -> dict[str, Any]:
    """Rectangle (ou carré) centré sur une position ; la longueur suit l'est quand orientation = 0°,
    l'orientation tourne le champ dans le sens inverse des aiguilles d'une montre."""
    if length_m <= 0 or width_m <= 0:
        raise ValueError("La longueur et la largeur doivent être positives")

    angle = radians(orientation_deg)
    half_length, half_width = length_m / 2.0, width_m / 2.0
    meters_lon = M_PER_DEG_LAT * cos(radians(latitude))
    corners = [(-half_length, -half_width), (half_length, -half_width),
               (half_length, half_width), (-half_length, half_width)]
    result: list[list[float]] = []
    for east_m, north_m in corners:
        rotated_east = east_m * cos(angle) - north_m * sin(angle)
        rotated_north = east_m * sin(angle) + north_m * cos(angle)
        result.append([round(longitude + rotated_east / meters_lon, 8),
                       round(latitude + rotated_north / M_PER_DEG_LAT, 8)])
    result.append(result[0].copy())
    return {"type": "Polygon", "coordinates": [result]}


def destination_point(longitude: float, latitude: float, distance_m: float,
                      bearing_deg: float) -> tuple[float, float]:
    """Point atteint en marchant `distance_m` mètres dans la direction `bearing_deg`
    (0° = nord, 90° = est), sur une sphère."""
    lat, lon = radians(latitude), radians(longitude)
    bearing = radians(bearing_deg)
    ang = distance_m / EARTH_RADIUS_M
    lat2 = asin(sin(lat) * cos(ang) + cos(lat) * sin(ang) * cos(bearing))
    lon2 = lon + atan2(sin(bearing) * sin(ang) * cos(lat), cos(ang) - sin(lat) * sin(lat2))
    lon2 = (lon2 + 3 * pi) % (2 * pi) - pi
    return degrees(lon2), degrees(lat2)


def generate_segments_polygon(longitude: float, latitude: float,
                              segments: list[tuple[float, float]]) -> tuple[dict[str, Any], float]:
    """Polygone construit côté par côté depuis un coin de départ.

    `segments` = [(distance_m, direction_deg), …]. Retourne (géométrie, écart_de_fermeture_m) :
    l'écart est la distance entre le dernier point et le départ (0 si le tour se referme bien).
    Le dernier côté est fermé automatiquement vers le point de départ.
    """
    if len(segments) < 2:
        raise ValueError("Au moins deux côtés sont nécessaires")
    ring: list[list[float]] = [[round(longitude, 8), round(latitude, 8)]]
    lon, lat = longitude, latitude
    for distance_m, bearing_deg in segments:
        if distance_m <= 0:
            raise ValueError("Chaque distance doit être positive")
        lon, lat = destination_point(lon, lat, distance_m, bearing_deg)
        ring.append([round(lon, 8), round(lat, 8)])

    dx = (lon - longitude) * M_PER_DEG_LAT * cos(radians(latitude))
    dy = (lat - latitude) * M_PER_DEG_LAT
    closing_gap_m = sqrt(dx * dx + dy * dy)
    if closing_gap_m < 0.5:               # le dernier côté revient au départ : pas de sommet en double
        ring.pop()
    ring.append(ring[0].copy())
    return {"type": "Polygon", "coordinates": [ring]}, round(closing_gap_m, 2)


# ------------------------------------------------------------------------------- outils pour le profil
# Repère local en mètres (x vers l'est, y vers le nord) autour d'une origine : suffisant à l'échelle d'une
# exploitation (quelques km). Sert au placement des panneaux et des capteurs, et au contrôle des zones.

XY = tuple[float, float]


class LocalFrame:
    """Repère local en mètres centré sur (lon0, lat0)."""

    def __init__(self, lon0: float, lat0: float) -> None:
        self.lon0, self.lat0 = lon0, lat0
        self.kx = M_PER_DEG_LAT * cos(radians(lat0))
        self.ky = M_PER_DEG_LAT

    def to_xy(self, lon: float, lat: float) -> XY:
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * self.ky

    def to_lonlat(self, x: float, y: float) -> list[float]:
        return [round(self.lon0 + x / self.kx, 8), round(self.lat0 + y / self.ky, 8)]

    def ring_xy(self, geometry: dict[str, Any]) -> list[XY]:
        """Anneau extérieur OUVERT (sans le point de fermeture), en mètres."""
        ring = [self.to_xy(float(p[0]), float(p[1])) for p in geometry["coordinates"][0]]
        if len(ring) > 1 and ring[0] == ring[-1]:
            ring.pop()
        return ring

    def polygon(self, ring: list[XY]) -> dict[str, Any]:
        """Anneau en mètres → Polygon GeoJSON fermé."""
        coords = [self.to_lonlat(x, y) for x, y in ring]
        return {"type": "Polygon", "coordinates": [coords + [coords[0][:]]]}


def centroid(geometry: dict[str, Any]) -> tuple[float, float]:
    """Centre de gravité (lon, lat) d'un Polygon GeoJSON."""
    pts = geometry["coordinates"][0]
    frame = LocalFrame(float(pts[0][0]), float(pts[0][1]))
    ring = frame.ring_xy(geometry)
    a = cx = cy = 0.0
    for i in range(len(ring)):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % len(ring)]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    if abs(a) < 1e-9:                     # dégénéré : moyenne des sommets
        x, y = sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
    else:
        x, y = cx / (3 * a), cy / (3 * a)
    lon, lat = frame.to_lonlat(x, y)
    return lon, lat


def point_in_ring(p: XY, ring: list[XY]) -> bool:
    """Lancer de rayon (anneau ouvert, en mètres)."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > p[1]) != (yj > p[1]) and p[0] < (xj - xi) * (p[1] - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _dist_seg(p: XY, a: XY, b: XY) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ((dx * dx + dy * dy) or 1e-12)))
    return sqrt((p[0] - a[0] - t * dx) ** 2 + (p[1] - a[1] - t * dy) ** 2)


def distance_to_ring(p: XY, ring: list[XY]) -> float:
    """Distance (m) d'un point au bord de l'anneau."""
    return min(_dist_seg(p, ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))


def ring_area_xy(ring: list[XY]) -> float:
    n = len(ring)
    return abs(sum(ring[i][0] * ring[(i + 1) % n][1] - ring[(i + 1) % n][0] * ring[i][1] for i in range(n))) / 2.0


def grid_points(ring: list[XY], step: float) -> list[XY]:
    """Points d'une grille régulière tombant dans l'anneau."""
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    out = []
    y = min(ys) + step / 2
    while y < max(ys):
        x = min(xs) + step / 2
        while x < max(xs):
            if point_in_ring((x, y), ring):
                out.append((x, y))
            x += step
        y += step
    return out


def spread_points(ring: list[XY], k: int, avoid: list[list[XY]] | None = None) -> list[XY]:
    """k points bien répartis à l'intérieur de l'anneau, loin des bords (échantillonnage le plus éloigné).

    Le premier point est le plus éloigné des bords ; les suivants maximisent la distance aux points déjà
    choisis. Les points situés dans un anneau de `avoid` (panneaux, autre zone) sont exclus."""
    step = max(1.0, sqrt(ring_area_xy(ring) / 600.0))   # ~600 points candidats quelle que soit la taille
    cands = [p for p in grid_points(ring, step) if not any(point_in_ring(p, a) for a in (avoid or []))]
    if not cands:
        cands = grid_points(ring, step) or [(sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))]
    edge = {p: distance_to_ring(p, ring) for p in cands}
    margin = min(5.0, max(edge.values()) / 2)     # de préférence à plus de 5 m du bord
    good = [p for p in cands if edge[p] >= margin] or cands
    chosen = [max(good, key=lambda p: edge[p])]
    while len(chosen) < k:
        chosen.append(max(good, key=lambda p: min((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 for c in chosen)))
    return chosen


def _proper_cross(p1: XY, p2: XY, p3: XY, p4: XY, eps: float = 1e-6) -> bool:
    d1, d2 = _orient(p3, p4, p1), _orient(p3, p4, p2)
    d3, d4 = _orient(p1, p2, p3), _orient(p1, p2, p4)
    return ((d1 > eps and d2 < -eps) or (d1 < -eps and d2 > eps)) and \
        ((d3 > eps and d4 < -eps) or (d3 < -eps and d4 > eps))


def rings_overlap(a: list[XY], b: list[XY], tol: float = 0.5) -> bool:
    """Deux surfaces se recouvrent-elles au-delà d'un bord commun (tolérance `tol` m) ?"""
    def deep(p: XY, ring: list[XY]) -> bool:
        return point_in_ring(p, ring) and distance_to_ring(p, ring) > tol

    for i in range(len(a)):
        for j in range(len(b)):
            p1, p2, p3, p4 = a[i], a[(i + 1) % len(a)], b[j], b[(j + 1) % len(b)]
            if not _proper_cross(p1, p2, p3, p4):
                continue
            d = (p1[0] - p2[0]) * (p3[1] - p4[1]) - (p1[1] - p2[1]) * (p3[0] - p4[0])
            if abs(d) < 1e-12:
                continue
            t = ((p1[0] - p3[0]) * (p3[1] - p4[1]) - (p1[1] - p3[1]) * (p3[0] - p4[0])) / d
            x = (p1[0] + t * (p2[0] - p1[0]), p1[1] + t * (p2[1] - p1[1]))
            if all(sqrt((v[0] - x[0]) ** 2 + (v[1] - x[1]) ** 2) > tol for v in (p1, p2, p3, p4)):
                return True                # croisement franc, pas une simple accroche de bords communs
    ca = (sum(p[0] for p in a) / len(a), sum(p[1] for p in a) / len(a))
    cb = (sum(p[0] for p in b) / len(b), sum(p[1] for p in b) / len(b))
    return any(deep(p, b) for p in a) or any(deep(p, a) for p in b) or \
        (deep(ca, a) and deep(ca, b)) or (deep(cb, b) and deep(cb, a))


def check_zones(parcel: dict[str, Any], zones: list[dict[str, Any]], tol_m: float = 1.0) -> list[str]:
    """Erreurs de découpage : zone invalide, hors du champ, zones qui se recouvrent. Vide si tout va bien."""
    frame = LocalFrame(*centroid(parcel))
    outer = frame.ring_xy(parcel)
    rings: list[list[XY] | None] = []
    errors: list[str] = []
    for z in zones:
        name = z.get("name") or "zone"
        try:
            validate_polygon(z["geometry"])
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"« {name} » : {exc}")
            rings.append(None)
            continue
        ring = frame.ring_xy(z["geometry"])
        outside = [p for p in ring if not point_in_ring(p, outer) and distance_to_ring(p, outer) > tol_m]
        if outside:
            errors.append(f"« {name} » dépasse du champ ({len(outside)} coin(s) à l'extérieur)")
        rings.append(ring)
    for i in range(len(rings)):
        for j in range(i + 1, len(rings)):
            ri, rj = rings[i], rings[j]
            if ri and rj and rings_overlap(ri, rj):
                errors.append(f"« {zones[i].get('name')} » et « {zones[j].get('name')} » se recouvrent")
    return errors
