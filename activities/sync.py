"""Synchronisation des activités Strava vers la base locale."""

from django.utils.dateparse import parse_datetime

from . import strava
from .models import Activity

PER_PAGE = 200  # maximum autorisé par Strava
MAX_PAGES = 50  # garde-fou : 10 000 activités


def _to_fields(data):
    latlng = data.get("start_latlng") or [None, None]
    return {
        "name": data.get("name", "")[:255],
        "sport_type": data.get("sport_type") or data.get("type", ""),
        "start_date": parse_datetime(data["start_date"]),
        "start_date_local": parse_datetime(data["start_date_local"]) if data.get("start_date_local") else None,
        "distance": data.get("distance") or 0,
        "moving_time": data.get("moving_time") or 0,
        "elapsed_time": data.get("elapsed_time") or 0,
        "total_elevation_gain": data.get("total_elevation_gain") or 0,
        "average_speed": data.get("average_speed") or 0,
        "summary_polyline": (data.get("map") or {}).get("summary_polyline") or "",
        "start_lat": latlng[0] if len(latlng) == 2 else None,
        "start_lng": latlng[1] if len(latlng) == 2 else None,
        "raw": data,
    }


def sync_activities(account, full=False):
    """Importe les activités. Par défaut, seulement celles plus récentes que la dernière connue.

    Renvoie le nombre d'activités créées ou mises à jour.
    """
    params = {"per_page": PER_PAGE}
    if not full:
        latest = account.activities.order_by("-start_date").first()
        if latest:
            params["after"] = int(latest.start_date.timestamp())

    count = 0
    for page in range(1, MAX_PAGES + 1):
        batch = strava.api_get(account, "/athlete/activities", {**params, "page": page})
        for data in batch:
            Activity.objects.update_or_create(
                strava_id=data["id"], defaults={"account": account, **_to_fields(data)}
            )
            count += 1
        if len(batch) < PER_PAGE:
            break
    return count
