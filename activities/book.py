"""Livre de voyage : préparation des données et mise en forme des périples.

Le livre réutilise les périples détectés (journeys.py) et les complète, pour
les seules étapes concernées, avec ce que la liste d'activités Strava ne donne
pas : la description saisie sur Strava et le profil d'altitude. Ces données
sont mises en cache sur l'activité : un livre régénéré ne coûte plus d'appel.
"""

from django.utils import formats, timezone

from . import polyline, strava

PROFILE_POINTS = 160
PHOTOS_ON_STAGE_PAGE = 3
PHOTOS_PER_PAGE = 6


def _downsample(distance, altitude):
    pairs = [(d, a) for d, a in zip(distance, altitude) if d is not None and a is not None]
    if len(pairs) < 2:
        return None
    step = max(1, len(pairs) // PROFILE_POINTS)
    sample = pairs[::step]
    if sample[-1] != pairs[-1]:
        sample.append(pairs[-1])
    return [[round(d / 1000, 3), round(a, 1)] for d, a in sample]


def prepare(account, journeys, max_calls=60):
    """Récupère descriptions et profils manquants. Renvoie (étapes complétées, erreur éventuelle).

    Deux appels par étape ; on s'arrête au plafond ou à la première erreur
    (limite de requêtes) : le livre se génère quand même avec ce qu'on a.
    """
    done, calls = 0, 0
    for j in journeys:
        for activity in j.activities:
            if activity.details_fetched_at:
                continue
            if calls + 2 > max_calls:
                return done, "Plafond d'appels atteint : certaines étapes n'ont pas encore leur profil. Régénère le livre dans quelques minutes."
            try:
                detail = strava.activity_detail(account, activity.strava_id)
                calls += 1
                streams = strava.activity_streams(account, activity.strava_id)
                calls += 1
            except strava.StravaError as exc:
                return done, f"Données Strava incomplètes ({exc}). Le livre est généré avec ce qui est disponible."
            activity.description = (detail.get("description") or "").strip()
            activity.profile = _downsample((streams.get("distance") or {}).get("data") or [],
                                           (streams.get("altitude") or {}).get("data") or [])
            activity.details_fetched_at = timezone.now()
            activity.save(update_fields=["description", "profile", "details_fetched_at"])
            done += 1
    return done, None


def profile_svg(points, width=1000, height=160, pad=4):
    """Profil d'altitude en SVG : chemin de la courbe et de l'aire, altitudes min/max."""
    if not points or len(points) < 2:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    max_x = xs[-1] or 1e-9
    lo, hi = min(ys), max(ys)
    span = max(hi - lo, 50)  # évite d'exagérer le relief d'une étape plate
    def xy(x, y):
        return f"{x / max_x * width:.1f},{height - pad - (y - lo) / span * (height - 2 * pad):.1f}"
    line = "M" + "L".join(xy(x, y) for x, y in zip(xs, ys))
    return {"line": line, "area": f"{line}L{width},{height}L0,{height}Z",
            "min": round(lo), "max": round(hi), "km": round(max_x, 1), "width": width, "height": height}


def _chunks(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


def build(journey, colors, date_range):
    """Contenu d'un périple pour le livre : synthèse, puis une page par étape."""
    stages, combined, offset = [], [], 0.0
    for i, (leg, color) in enumerate(zip(journey.legs, colors)):
        a = leg.activity
        photos = list(a.photos.all())
        if a.profile:
            combined += [[offset + km, alt] for km, alt in a.profile]
            offset += a.profile[-1][0]
        else:
            offset += a.distance / 1000
        stages.append({
            "n": i + 1, "id": a.strava_id, "color": color, "activity_name": a.name,
            "label": leg.label or a.name,
            "date": formats.date_format(a.start_date_local or a.start_date, "l j F Y"),
            "km": a.distance_km, "elevation": round(a.total_elevation_gain), "duration": a.duration_display,
            "speed": a.speed_display, "description": a.description,
            "coords": polyline.decode(a.summary_polyline), "profile": profile_svg(a.profile),
            "lead_photos": photos[:PHOTOS_ON_STAGE_PAGE],
            "photo_pages": _chunks(photos[PHOTOS_ON_STAGE_PAGE:], PHOTOS_PER_PAGE),
            "night_after": i in journey.nights,
        })
    return {
        "journey": journey, "dates": date_range, "stages": stages,
        "profile": profile_svg(combined), "nights": len(journey.nights),
        "photo_count": sum(len(s["lead_photos"]) + sum(len(p) for p in s["photo_pages"]) for s in stages),
        "cover_photo": next((p for s in stages for p in s["lead_photos"]), None),
        "highest": max((s["profile"]["max"] for s in stages if s["profile"]), default=None),
    }
