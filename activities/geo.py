"""Petits calculs géographiques pour l'affichage des tracés."""
import math


def svg_path(coords, size=100, pad=6, max_points=120, frame=None):
    """Chemin SVG (attribut d) d'un tracé, mis à l'échelle dans un carré size×size.

    Projection équirectangulaire corrigée par cos(latitude) : suffisant à
    l'échelle d'une sortie et beaucoup plus léger qu'une vraie projection.
    `frame` : autres points à faire tenir dans le même cadre (plusieurs étapes
    d'un même voyage dessinées ensemble).
    """
    if len(coords) < 2:
        return ""
    step = max(1, len(coords) // max_points)
    pts = coords[::step]
    if pts[-1] != coords[-1]:
        pts.append(coords[-1])
    ref = frame or pts
    k = math.cos(math.radians(sum(p[0] for p in ref) / len(ref)))
    xs = [p[1] * k for p in pts]
    ys = [-p[0] for p in pts]
    fx = [p[1] * k for p in ref]
    fy = [-p[0] for p in ref]
    min_x, min_y = min(fx), min(fy)
    span = max(max(fx) - min_x, max(fy) - min_y) or 1e-9
    scale = (size - 2 * pad) / span
    off_x = (size - (max(fx) - min_x) * scale) / 2
    off_y = (size - (max(fy) - min_y) * scale) / 2
    return "M" + "L".join(
        f"{(x - min_x) * scale + off_x:.1f},{(y - min_y) * scale + off_y:.1f}" for x, y in zip(xs, ys)
    )


EARTH_RADIUS_KM = 6371.0


def distance_km(a, b):
    """Distance à vol d'oiseau (haversine) entre deux points [lat, lng], en km."""
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))
