"""Petits calculs géographiques pour l'affichage des tracés."""
import math


def svg_path(coords, size=100, pad=6, max_points=120):
    """Chemin SVG (attribut d) d'un tracé, mis à l'échelle dans un carré size×size.

    Projection équirectangulaire corrigée par cos(latitude) : suffisant à
    l'échelle d'une sortie et beaucoup plus léger qu'une vraie projection.
    """
    if len(coords) < 2:
        return ""
    step = max(1, len(coords) // max_points)
    pts = coords[::step]
    if pts[-1] != coords[-1]:
        pts.append(coords[-1])
    k = math.cos(math.radians(sum(p[0] for p in pts) / len(pts)))
    xs = [p[1] * k for p in pts]
    ys = [-p[0] for p in pts]
    min_x, min_y = min(xs), min(ys)
    span = max(max(xs) - min_x, max(ys) - min_y) or 1e-9
    scale = (size - 2 * pad) / span
    off_x = (size - (max(xs) - min_x) * scale) / 2
    off_y = (size - (max(ys) - min_y) * scale) / 2
    return "M" + "L".join(
        f"{(x - min_x) * scale + off_x:.1f},{(y - min_y) * scale + off_y:.1f}" for x, y in zip(xs, ys)
    )
