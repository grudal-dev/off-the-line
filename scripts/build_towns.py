"""Génère activities/data/towns.tsv.gz : villes et villages de plus de 1 000 habitants (GeoNames).

Source : GeoNames (CC BY 4.0), « cities1000 », par exemple tel que fourni par le paquet Python
geonamescache (geonamescache/data/cities1000.json) ou https://download.geonames.org/export/dump/.
Les noms des villes étrangères connues de Natural Earth sont remplacés par leur nom français
(cities.json du site : Genève plutôt que Geneva, Milan, Turin…).

    python scripts/build_towns.py chemin/vers/cities1000.json

Format : une ville par ligne, « nom<TAB>lat<TAB>lng<TAB>population », de la plus peuplée à la moins peuplée.
Utilisé par le serveur seulement (activities/towns.py) : le fichier est trop gros pour le navigateur.
"""

import gzip
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "activities" / "data" / "towns.tsv.gz"
NE = ROOT / "activities" / "static" / "activities" / "cities.json"


def main(source):
    rows = []
    for c in json.loads(Path(source).read_text(encoding="utf-8")).values():
        name = (c.get("name") or "").strip().replace("\t", " ")
        if name:
            rows.append([name, round(float(c["latitude"]), 4), round(float(c["longitude"]), 4), int(c.get("population") or 0)])
    # Grille de 0,2° pour retrouver vite les villes Natural Earth (noms français)
    grid = {}
    for i, (_, lat, lng, _) in enumerate(rows):
        grid.setdefault((int(lat // 0.2), int(lng // 0.2)), []).append(i)
    renamed = 0
    for name_fr, lat, lng, *_ in json.loads(NE.read_text(encoding="utf-8"))["cities"]:
        best, best_pop = None, -1
        ci, cj = int(lat // 0.2), int(lng // 0.2)
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                for i in grid.get((ci + di, cj + dj), []):
                    _, rlat, rlng, pop = rows[i]
                    km = math.hypot(rlat - lat, (rlng - lng) * math.cos(math.radians(lat))) * 111
                    if km < 12 and pop > best_pop:
                        best, best_pop = i, pop
        if best is not None and rows[best][0] != name_fr:
            rows[best][0] = name_fr
            renamed += 1
    rows.sort(key=lambda r: -r[3])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as f:
        for name, lat, lng, pop in rows:
            f.write(f"{name}\t{lat}\t{lng}\t{pop}\n")
    print(f"{len(rows)} villes ({renamed} noms français) → {OUT} ({OUT.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    main(sys.argv[1])
