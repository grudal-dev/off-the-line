"""Génère les frontières et côtes des affiches à partir de Natural Earth (domaine public).

Sources (échelle 1:10 000 000), dans un même dossier :
https://github.com/nvkelso/natural-earth-vector/tree/master/geojson
  - ne_10m_admin_0_boundary_lines_land.geojson   → frontières des pays
  - ne_10m_admin_1_states_provinces_lines.geojson → régions, départements, États… (selon le pays)
  - ne_10m_coastline.geojson                      → côtes

    python scripts/build_borders.py chemin/vers/le/dossier

Sortie : activities/static/activities/borders-<couche>.json, chargés par le navigateur seulement
quand l'option est cochée. Format compact : [[ouest, sud, est, nord, "polyligne polyligne…"], …] ; chaque polyligne
suit le codage Google/Strava avec une précision de 1e-4 degré, après simplification
(Douglas-Peucker) pour garder des fichiers légers.
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "activities" / "static" / "activities"
LAYERS = {
    "countries": ("ne_10m_admin_0_boundary_lines_land.geojson", 0.0015),
    "regions": ("ne_10m_admin_1_states_provinces_lines.geojson", 0.003),
    "coast": ("ne_10m_coastline.geojson", 0.003),
}
PRECISION = 1e4


def simplify(points, tolerance):
    """Douglas-Peucker itératif (degrés, sans correction de latitude : suffisant ici)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    tol2 = tolerance * tolerance
    while stack:
        a, b = stack.pop()
        (ax, ay), (bx, by) = points[a], points[b]
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        best, index = -1.0, None
        for i in range(a + 1, b):
            px, py = points[i]
            if length2:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length2))
                qx, qy = ax + t * dx - px, ay + t * dy - py
            else:
                qx, qy = px - ax, py - ay
            d = qx * qx + qy * qy
            if d > best:
                best, index = d, i
        if index is not None and best > tol2:
            keep[index] = True
            stack += [(a, index), (index, b)]
    return [p for p, k in zip(points, keep) if k]


def _encode_value(v):
    v = ~(v << 1) if v < 0 else v << 1
    out = []
    while v >= 0x20:
        out.append(chr((0x20 | (v & 0x1F)) + 63))
        v >>= 5
    out.append(chr(v + 63))
    return "".join(out)


def encode(points):
    """Polyligne encodée (lat, lng), précision 1e-4."""
    out, plat, plng = [], 0, 0
    for lng, lat in points:
        a, b = round(lat * PRECISION), round(lng * PRECISION)
        if out and a == plat and b == plng:
            continue
        out.append(_encode_value(a - plat) + _encode_value(b - plng))
        plat, plng = a, b
    return "".join(out)


def lines(geometry):
    if not geometry:
        return []
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return geometry["coordinates"]
    return []


def build(folder, key):
    filename, tolerance = LAYERS[key]
    features = json.loads((Path(folder) / filename).read_text(encoding="utf-8"))["features"]
    rows = []
    for f in features:
        parts = [simplify([(round(x, 5), round(y, 5)) for x, y in coords], tolerance) for coords in lines(f.get("geometry"))]
        parts = [p for p in parts if len(p) >= 2]
        if not parts:
            continue
        xs, ys = [p[0] for pts in parts for p in pts], [p[1] for pts in parts for p in pts]
        # Une entrée par élément Natural Earth ; ses morceaux séparés par une espace (absente du codage)
        rows.append([round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2),
                     " ".join(encode(pts) for pts in parts)])
    out = OUT / f"borders-{key}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{key} : {len(rows)} lignes → {out.name} ({out.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    for layer in LAYERS:
        build(sys.argv[1], layer)
