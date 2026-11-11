"""Sorties à vérifier : celles qui ressemblent à une erreur de saisie.

Un trajet en voiture enregistré comme une sortie à vélo, ou la même sortie enregistrée deux fois
(montre et téléphone), faussent la carte, les voyages, les affiches et les livres. On les signale
dans Explorer ; l'utilisateur les retire, ou les garde (`Activity.confirmed`) et on n'en parle plus.
"""

from datetime import timedelta

from . import sports

# Vitesse moyenne (km/h, temps en mouvement) au-delà de laquelle le sport annoncé n'est pas crédible
MAX_KMH = {"run": 25, "walk": 12, "ride": 60}
ABOUT = {"run": "de la course", "walk": "de la marche", "ride": "du vélo"}
MIN_KM, MIN_SECONDS = 1, 120          # trop court pour juger
SAME_START = timedelta(seconds=120)   # doublon : même départ…
SAME_DISTANCE = 0.05                  # … et même distance à 5 % près


def speed_kmh(activity):
    if activity.distance < MIN_KM * 1000 or activity.moving_time < MIN_SECONDS:
        return None
    return activity.distance / activity.moving_time * 3.6


def suspects(activities):
    """{strava_id: raison} pour les activités douteuses, sauf celles que l'utilisateur a gardées."""
    found = {}
    for a in activities:
        if a.confirmed:
            continue
        family, speed = sports.family(a.sport_type), speed_kmh(a)
        limit = MAX_KMH.get(family)
        if limit and speed and speed > limit:
            found[a.strava_id] = f"{round(speed)} km/h de moyenne pour {ABOUT[family]} : un trajet motorisé ?"
    ordered = sorted(activities, key=lambda a: (a.start_date, a.strava_id))
    for first, second in zip(ordered, ordered[1:]):
        if second.confirmed or second.strava_id in found or second.start_date - first.start_date > SAME_START:
            continue
        longest = max(first.distance, second.distance)
        if longest and abs(first.distance - second.distance) <= SAME_DISTANCE * longest:
            found[second.strava_id] = f"Même départ et même distance que « {first.name} » : un doublon ?"
    return found
