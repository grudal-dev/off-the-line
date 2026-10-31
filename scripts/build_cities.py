"""Génère activities/static/activities/cities.json à partir de Natural Earth (domaine public).

Source : ne_10m_populated_places.geojson
https://github.com/nvkelso/natural-earth-vector/blob/master/geojson/ne_10m_populated_places.geojson

    python scripts/build_cities.py chemin/vers/ne_10m_populated_places.geojson

Format de sortie (compact, servi tel quel au navigateur) :
[[nom, lat, lng, zoom_min, population_en_milliers, capitale], …] trié par importance.
« zoom_min » est le niveau de zoom à partir duquel Natural Earth conseille d'afficher la ville.
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "activities" / "static" / "activities" / "cities.json"


def main(source):
    features = json.loads(Path(source).read_text(encoding="utf-8"))["features"]
    rows = []
    for f in features:
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
        ])
    rows.sort(key=lambda r: (r[3], -r[4]))
    OUT.write_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(rows)} villes → {OUT} ({OUT.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    main(sys.argv[1])
