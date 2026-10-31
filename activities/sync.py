"""Synchronisation des activités Strava vers la base locale."""

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import strava
from .models import Activity, ActivityPhoto

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
        "photo_count": data.get("total_photo_count") or 0,
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
            activity, created = Activity.objects.update_or_create(
                strava_id=data["id"], defaults={"account": account, **_to_fields(data)}
            )
            if not created and activity.photos_synced_at and activity.photo_count != activity.photos.count():
                # Des photos ont été ajoutées ou retirées sur Strava : on les récupérera à nouveau
                Activity.objects.filter(pk=activity.pk).update(photos_synced_at=None)
            count += 1
        if len(batch) < PER_PAGE:
            break
    return count


PHOTO_BATCH = 40  # appels API maximum par synchronisation (limite Strava : 100 / 15 min)


def import_activity_photos(account, activity):
    """Importe les photos d'une activité (un appel Strava). Renvoie le nombre de photos."""
    items = strava.activity_photos(account, activity.strava_id)
    keep = []
    for item in items:
        urls = item.get("urls") or {}
        size_key = max(urls, key=int) if urls else None  # la plus grande taille renvoyée
        url = urls.get(size_key)
        dims = (item.get("sizes") or {}).get(size_key) or [None, None]
        if not url or not item.get("unique_id"):
            continue
        taken_at = parse_datetime(item["created_at"]) if item.get("created_at") else None
        ActivityPhoto.objects.update_or_create(
            unique_id=item["unique_id"],
            defaults={
                "activity": activity, "url": url, "caption": (item.get("caption") or "")[:2000],
                "taken_at": taken_at, "width": dims[0], "height": dims[1],
            },
        )
        keep.append(item["unique_id"])
    activity.photos.exclude(unique_id__in=keep).delete()
    activity.photos_synced_at = timezone.now()
    activity.save(update_fields=["photos_synced_at"])
    # Les photos ont pu être préchargées (prefetch_related) avant l'import : on oublie ce cache
    getattr(activity, "_prefetched_objects_cache", {}).pop("photos", None)
    return len(keep)


def sync_photos(account, limit=PHOTO_BATCH):
    """Importe les photos des activités qui en ont et qui n'ont pas encore été traitées.

    Limité à `limit` activités par appel pour ménager le quota d'API ;
    les suivantes seront traitées à la prochaine synchronisation.
    Renvoie (photos importées, activités restant à traiter).
    """
    pending = account.activities.filter(photo_count__gt=0, photos_synced_at__isnull=True)
    imported = 0
    for activity in pending.order_by("-start_date")[:limit]:
        imported += import_activity_photos(account, activity)
    return imported, pending.count()


ON_DEMAND_LIMIT = 20  # appels maximum à l'ouverture d'une page


def ensure_photos(account, activities, limit=None):
    """Importe à la demande les photos des activités affichées qui n'ont pas encore été traitées.

    Appelée à l'ouverture d'un périple, d'une activité ou d'un livre : seules les
    activités réellement consultées consomment du quota Strava. Ne lève jamais :
    renvoie (photos importées, activités encore en attente, message d'erreur ou None).
    """
    limit = limit or ON_DEMAND_LIMIT
    pending = [a for a in activities if a.photo_count and not a.photos_synced_at]
    imported = 0
    for done, activity in enumerate(pending[:limit]):
        try:
            imported += import_activity_photos(account, activity)
        except strava.StravaError as exc:
            return imported, len(pending) - done, str(exc)
    return imported, max(len(pending) - limit, 0), None
