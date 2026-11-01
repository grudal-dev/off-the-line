"""Génère activities/static/activities/cities.json à partir de Natural Earth (domaine public).

Sources :
https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_10m_populated_places.geojson
https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_50m_admin_0_countries.geojson

    python scripts/build_cities.py ne_10m_populated_places.geojson ne_50m_admin_0_countries.geojson

Format de sortie (compact, servi tel quel au navigateur) :
{"cities": [[nom, lat, lng, zoom_min, population_en_milliers, capitale, index_pays], …] triées par importance,
 "countries": [[nom, lat, lng, zoom_min, zoom_max, grand], …] triés par zoom_min}
Les zooms sont ceux que Natural Earth conseille pour afficher l'étiquette. « grand » vaut 0 pour les
micro-États (pas d'étiquette en vue large) ; index_pays renvoie à « countries » (-1 si inconnu) et sert
à nommer le pays de la zone visible quand on zoome.
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "activities" / "static" / "activities" / "cities.json"


def cities(source, country_index):
    rows = []
    for f in json.loads(Path(source).read_text(encoding="utf-8"))["features"]:
        p = f["properties"]
        name = (p.get("NAME_FR") or p.get("NAME") or "").strip()
        if not name:
            continue
        rows.append([
            name,
            round(p["LATITUDE"], 3),
            round(p["LONGITUDE"], 3),
            round(p.get("MIN_ZOOM") or 10, 1),
            round((p.get("POP_MAX") or 0) / 1000),
            1 if p.get("ADM0CAP") else 0,
            country_index.get(p.get("ADM0_A3"), -1),
        ])
    rows.sort(key=lambda r: (r[3], -r[4]))
    return rows


MIN_COUNTRY_AREA = 0.3  # degrés carrés (emprise) : écarte les micro-États (Monaco, Vatican, Jersey…)


def _bbox_area(geometry):
    """Surface de l'emprise du plus grand polygone, en degrés carrés (assez pour trier petit / grand)."""
    polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    best = 0
    for poly in polygons:
        xs = [pt[0] for pt in poly[0]]
        ys = [pt[1] for pt in poly[0]]
        best = max(best, (max(xs) - min(xs)) * (max(ys) - min(ys)))
    return best


def countries(source):
    rows = []
    for f in json.loads(Path(source).read_text(encoding="utf-8"))["features"]:
        p = f["properties"]
        name = (p.get("NAME_FR") or p.get("NAME") or "").strip()
        if not name or p.get("LABEL_Y") is None or not f.get("geometry"):
            continue
        big = 1 if _bbox_area(f["geometry"]) >= MIN_COUNTRY_AREA else 0
        rows.append(([name, round(p["LABEL_Y"], 3), round(p["LABEL_X"], 3),
                      round(p.get("MIN_LABEL") or 5, 1), round(p.get("MAX_LABEL") or 10, 1), big], p["ADM0_A3"]))
    rows.sort(key=lambda r: r[0][3])
    return [r for r, _ in rows], {code: i for i, (_, code) in enumerate(rows)}


def main(places_source, countries_source):
    country_rows, country_index = countries(countries_source)
    data = {"cities": cities(places_source, country_index), "countries": country_rows}
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(data['cities'])} villes, {len(data['countries'])} pays → {OUT} ({OUT.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
