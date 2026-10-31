"""Noms de lieux (géocodage inverse) pour nommer les périples et leurs étapes.

1. Géoplateforme de l'IGN (France, sans clé, rapide) ;
2. à défaut (hors de France), Nominatim d'OpenStreetMap, limité à 1 requête/s
   par sa politique d'usage.

Chaque résultat est mis en cache en base, sur une grille d'environ 1 km :
un même lieu n'est demandé qu'une fois. Les appels sont plafonnés par un
« budget » pour qu'une page ne reste jamais bloquée longtemps.
"""

import time
from decimal import Decimal

import requests
from django.conf import settings

from .models import Place

GEOPF_URL = "https://data.geopf.fr/geocodage/reverse"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
TIMEOUT = 4


def _key(point):
    return Decimal(f"{point[0]:.2f}"), Decimal(f"{point[1]:.2f}")


class Budget:
    """Nombre d'appels réseau autorisés ; s'arrête aussi après des échecs répétés."""

    def __init__(self, lookups=20, nominatim=3):
        self.lookups, self.nominatim, self.failures = lookups, nominatim, 0
        self._last_nominatim = 0.0

    @property
    def exhausted(self):
        return self.lookups <= 0 or self.failures >= 2


def _geopf(point):
    r = requests.get(GEOPF_URL, params={"lat": point[0], "lon": point[1], "index": "address", "limit": 1},
                     timeout=TIMEOUT)
    r.raise_for_status()
    features = r.json().get("features") or []
    return (features[0].get("properties") or {}).get("city", "") if features else ""


def _nominatim(point, budget):
    wait = 1.1 - (time.monotonic() - budget._last_nominatim)
    if wait > 0:
        time.sleep(wait)
    budget._last_nominatim = time.monotonic()
    contact = getattr(settings, "NOMINATIM_CONTACT", "") or "https://github.com/grudal-dev/off-the-line"
    r = requests.get(
        NOMINATIM_URL,
        params={"lat": point[0], "lon": point[1], "format": "jsonv2", "zoom": 10, "accept-language": "fr"},
        headers={"User-Agent": f"OffTheLine/1.0 ({contact})"}, timeout=TIMEOUT,
    )
    r.raise_for_status()
    address = r.json().get("address") or {}
    for field in ("city", "town", "village", "municipality", "hamlet", "county", "state"):
        if address.get(field):
            return address[field]
    return ""


def load_cache():
    """Tout le cache des lieux en une requête : {(lat_key, lng_key): nom}."""
    return {(lat, lng): name for lat, lng, name in Place.objects.values_list("lat_key", "lng_key", "name")}


def lookup(point, budget, cache=None):
    """Nom de commune pour un point [lat, lng], ou None s'il n'est pas (encore) connu.

    `cache` (voir load_cache) évite une requête par point quand on en nomme beaucoup.
    """
    lat_key, lng_key = _key(point)
    if cache is not None:
        if (lat_key, lng_key) in cache:
            return cache[(lat_key, lng_key)] or None
    else:
        cached = Place.objects.filter(lat_key=lat_key, lng_key=lng_key).first()
        if cached:
            return cached.name or None
    if budget is None or budget.exhausted:
        return None
    budget.lookups -= 1
    try:
        name, source = _geopf(point), "ign"
        if not name and budget.nominatim > 0:
            budget.nominatim -= 1
            name, source = _nominatim(point, budget), "osm"
    except (requests.RequestException, ValueError, AttributeError, TypeError):  # réseau ou réponse inattendue
        budget.failures += 1
        return None
    Place.objects.update_or_create(lat_key=lat_key, lng_key=lng_key, defaults={"name": name, "source": source})
    if cache is not None:
        cache[(lat_key, lng_key)] = name
    return name or None


def name_journeys(journeys, budget=None):
    """Renseigne le nom de chaque périple et de ses étapes à partir des lieux traversés.

    Les points déjà en cache sont toujours utilisés ; les autres sont demandés
    dans la limite du budget (aucun appel si budget est None).
    """
    cache = load_cache() if journeys else None
    for j in journeys:
        for leg in j.legs:
            leg.start_name = lookup(leg.start, budget, cache)
            leg.end_name = lookup(leg.end, budget, cache)
        j.start_name, j.end_name = j.legs[0].start_name, j.legs[-1].end_name
    return journeys


def missing_points(journeys, cache=None):
    """Points de départ/arrivée des étapes dont le nom n'est pas encore en cache (sans doublons)."""
    cache = load_cache() if cache is None else cache
    missing = {}
    for j in journeys:
        for leg in j.legs:
            for point in (leg.start, leg.end):
                key = _key(point)
                if key not in cache and key not in missing:
                    missing[key] = point
    return list(missing.values())


def fill(points, budget):
    """Cherche le nom des points manquants dans la limite du budget (chargement en arrière-plan).

    Renvoie (noms trouvés, points encore inconnus, échec réseau).
    """
    cache = load_cache()
    found = 0
    for point in points:
        if budget.exhausted:
            break
        key = _key(point)
        if key in cache:
            continue
        lookup(point, budget, cache)
        if cache.get(key):
            found += 1
    remaining = sum(1 for p in points if _key(p) not in cache)
    return found, remaining, bool(budget.failures and remaining)
