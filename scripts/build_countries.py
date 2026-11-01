"""Contours simplifiés des pays, pour compter les pays traversés sur une affiche (Natural Earth, domaine public).

Source : Natural Earth 1:110 000 000, *admin 0 countries* (ne_110m_admin_0_countries), au format
shapefile ou GeoJSON. Le shapefile est aussi fourni par le paquet geopandas 0.14
(geopandas/datasets/naturalearth_lowres).

    pip install pyshp
    python scripts/build_countries.py chemin/vers/ne_110m_admin_0_countries.shp   (ou .geojson)

Sortie : activities/static/activities/countries-shapes.json, chargé par l'affiche seulement pour un
périple, une activité ou une zone. Format : {"countries": [[nom, ouest, sud, est, nord, "anneau anneau…"], …]},
anneaux codés comme les frontières (build_borders.encode, précision 1e-4 degré). À cette échelle, les
contours sont approximatifs (quelques kilomètres) : l'affiche ne compte un pays qu'au-delà de 15 km parcourus.
"""

import json
import sys
from pathlib import Path

from build_borders import OUT, encode


def polygons(path):
    """[(code, [anneau, …])] ; chaque anneau est une liste de (lng, lat)."""
    if path.suffix == ".geojson":
        data = json.loads(path.read_text(encoding="utf-8"))
        for f in data["features"]:
            g, p = f["geometry"], f["properties"]
            polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
            yield p.get("NAME") or p.get("name") or p.get("ISO_A3"), [ring for poly in polys for ring in poly]
        return
    import shapefile  # pyshp
    for sr in shapefile.Reader(str(path)).shapeRecords():
        s, rec = sr.shape, sr.record.as_dict()
        bounds = list(s.parts) + [len(s.points)]
        # le nom plutôt que le code ISO : certains pays (France, Norvège) ont le code « -99 » dans cette source
        yield (rec.get("name") or rec.get("NAME") or rec.get("iso_a3"),
               [s.points[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1)])


def main(source):
    rows = []
    for code, rings in polygons(Path(source)):
        rings = [r for r in rings if len(r) >= 4]
        if not rings:
            continue
        xs = [x for r in rings for x, _ in r]
        ys = [y for r in rings for _, y in r]
        rows.append([code, round(min(xs), 3), round(min(ys), 3), round(max(xs), 3), round(max(ys), 3),
                     " ".join(encode(r) for r in rings)])
    out = OUT / "countries-shapes.json"
    out.write_text(json.dumps({"countries": rows}, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{out.name} : {len(rows)} pays, {out.stat().st_size // 1024} Ko")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
