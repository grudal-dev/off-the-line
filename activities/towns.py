"""Villes et villages de plus de 1 000 habitants (GeoNames, CC BY 4.0) : activities/data/towns.tsv.gz.

Chargées une fois en mémoire (~15 Mo) et rangées dans une grille de 0,25° pour trouver vite :
- les villes proches d'un tracé (affiche : « Proches du parcours ») ;
- la ville la plus proche d'un point (nom du départ et de l'arrivée) ;
- les villes dont le nom correspond à une recherche (ajouter une ville).
Fichier construit par scripts/build_towns.py.
"""

import gzip
import math
import threading
import unicodedata
from array import array
from pathlib import Path

PATH = Path(__file__).resolve().parent / "data" / "towns.tsv.gz"
CELL = 0.25  # degrés

_lock = threading.Lock()
_data = None


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", text or "") if not unicodedata.combining(c)).lower()


class _Towns:
    def __init__(self, path):
        self.names, self.folded = [], []
        self.lat, self.lng, self.pop = array("f"), array("f"), array("i")
        self.grid = {}
        if not path.exists():
            return
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for i, line in enumerate(f):
                name, lat, lng, pop = line.rstrip("\n").split("\t")
                lat, lng = float(lat), float(lng)
                self.names.append(name)
                self.folded.append(fold(name))
                self.lat.append(lat); self.lng.append(lng); self.pop.append(int(pop))
                self.grid.setdefault(_cell(lat, lng), array("i")).append(i)  # i croissant = plus peuplée d'abord

    def row(self, i):
        return [self.names[i], round(self.lat[i], 4), round(self.lng[i], 4), self.pop[i]]


def _cell(lat, lng):
    return int(math.floor(lat / CELL)), int(math.floor(lng / CELL))


def _km(lat1, lng1, lat2, lng2):
    return math.hypot(lat2 - lat1, (lng2 - lng1) * math.cos(math.radians((lat1 + lat2) / 2))) * 111.2


def data():
    global _data
    if _data is None:
        with _lock:
            if _data is None:
                _data = _Towns(PATH)
    return _data


def _around(lat, lng, km):
    """Indices des villes des cases autour d'un point (rayon km)."""
    t = data()
    dlat = km / 111.2 / CELL
    dlng = km / (111.2 * max(0.05, math.cos(math.radians(lat)))) / CELL
    ci, cj = _cell(lat, lng)
    for i in range(ci - math.ceil(dlat), ci + math.ceil(dlat) + 1):
        for j in range(cj - math.ceil(dlng), cj + math.ceil(dlng) + 1):
            yield from t.grid.get((i, j), ())


def near_track(points, km=10, limit=400):
    """Villes à moins de km d'un des points [(lat, lng)…], des plus peuplées aux moins peuplées."""
    t = data()
    found = set()
    for lat, lng in points:
        for i in _around(lat, lng, km):
            if i not in found and _km(lat, lng, t.lat[i], t.lng[i]) <= km:
                found.add(i)
    return [t.row(i) for i in sorted(found)[:limit]]


def nearest(lat, lng, max_km=8):
    """Ville la plus proche du point (pondérée : un bourg à côté plutôt qu'une ville à 7 km), ou ""."""
    t = data()
    best, score = "", None
    for i in _around(lat, lng, max_km):
        d = _km(lat, lng, t.lat[i], t.lng[i])
        if d <= max_km:
            s = d / (1 + math.log10(max(1000, t.pop[i])) - 3)  # les grandes villes « attirent » un peu plus loin
            if score is None or s < score:
                best, score = t.names[i], s
    return best


def search(query, lat=None, lng=None, limit=8):
    """Villes dont le nom contient la recherche ; d'abord celles qui commencent par elle, puis les plus proches."""
    q = fold(query.strip())
    if len(q) < 2:
        return []
    t = data()
    hits = [i for i, name in enumerate(t.folded) if q in name]
    def key(i):
        starts = 0 if t.folded[i].startswith(q) else 1
        dist = _km(lat, lng, t.lat[i], t.lng[i]) if lat is not None else 0
        return (starts, dist, i)
    out = []
    for i in sorted(hits, key=key)[:limit]:
        row = t.row(i)
        if lat is not None:
            row.append(round(_km(lat, lng, t.lat[i], t.lng[i])))
        out.append(row)
    return out
