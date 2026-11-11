"""Synchronisation des activités Strava vers la base locale."""

from dataclasses import dataclass

from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from . import journeys, strava
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


def import_activity(account, strava_id):
    """Importe une activité précise (un appel Strava) : webhook « activité créée », sortie remise sur le site."""
    data = strava.activity_detail(account, strava_id)
    fields = _to_fields(data)
    fields["description"] = data.get("description") or ""
    activity, _ = Activity.objects.update_or_create(strava_id=strava_id, defaults={"account": account, **fields})
    return activity


# Champs comparés pour savoir si une activité a changé sur Strava (« raw » suit, sans être comparé)
COMPARED = ("name", "sport_type", "start_date", "start_date_local", "distance", "moving_time", "elapsed_time",
            "total_elevation_gain", "average_speed", "summary_polyline", "photo_count", "start_lat", "start_lng")


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    removed: int = 0

    @property
    def changed(self):
        return self.created + self.updated + self.removed

    def summary(self):
        """« 3 nouvelles sorties, 1 mise à jour, 2 supprimées » (vide s'il n'y a rien de nouveau)."""
        parts = []
        if self.created:
            parts.append(f"{self.created} nouvelle{'s' if self.created > 1 else ''} sortie{'s' if self.created > 1 else ''}")
        if self.updated:
            parts.append(f"{self.updated} mise{'s' if self.updated > 1 else ''} à jour")
        if self.removed:
            parts.append(f"{self.removed} supprimée{'s' if self.removed > 1 else ''} sur Strava et retirée{'s' if self.removed > 1 else ''} d'ici")
        return ", ".join(parts)


def sync_activities(account):
    """Aligne la base sur Strava : nouvelles activités, activités modifiées, activités supprimées.

    Toute la liste est relue (un appel pour 200 activités) : c'est le seul moyen de voir ce qui a été
    supprimé ou modifié sur Strava quand le webhook n'a pas prévenu (abonnement absent, site arrêté,
    suppression antérieure à l'abonnement). Les activités inchangées ne sont pas réécrites, les sorties
    retirées du site par l'utilisateur (RemovedActivity) ne sont pas réimportées, et celles lues dans
    des fichiers (identifiant négatif) ne sont jamais touchées.
    """
    known = {a.strava_id: a for a in account.activities.filter(strava_id__gt=0)
             .only("account", "strava_id", "photos_synced_at", *COMPARED)}
    local_photos = dict(ActivityPhoto.objects.filter(activity__account=account)
                        .values_list("activity__strava_id").annotate(n=Count("id")))
    ignored = set(account.removed_activities.values_list("strava_id", flat=True))
    result, seen, complete = SyncResult(), set(), False

    for page in range(1, MAX_PAGES + 1):
        batch = strava.api_get(account, "/athlete/activities", {"per_page": PER_PAGE, "page": page})
        if not batch:  # Strava peut renvoyer des pages incomplètes : seule une page vide marque la fin
            complete = True
            break
        for data in batch:
            seen.add(data["id"])
            if data["id"] in ignored:
                continue
            fields = _to_fields(data)
            activity = known.get(data["id"])
            if activity is None:
                Activity.objects.update_or_create(strava_id=data["id"], defaults={"account": account, **fields})
                result.created += 1
                continue
            reset = []
            if activity.photos_synced_at:
                local = local_photos.get(activity.strava_id, 0)
                if fields["photo_count"] > local:
                    # Strava annonce plus de photos que nous n'en avons : on les récupérera à nouveau
                    activity.photos_synced_at = None
                    reset.append("photos_synced_at")
                else:
                    # Le compteur de la liste Strava (total_photo_count) vaut souvent 0 à tort :
                    # on garde le nombre réellement importé
                    fields["photo_count"] = local
            changed = [k for k in COMPARED if getattr(activity, k) != fields[k]]
            if not changed and not reset:
                continue
            if "summary_polyline" in changed or "distance" in changed:
                # Tracé recoupé ou corrigé sur Strava : le profil d'altitude sera redemandé
                activity.details_fetched_at = None
                reset.append("details_fetched_at")
            for k in changed:
                setattr(activity, k, fields[k])
            activity.raw = data
            activity.save(update_fields=[*changed, *reset, "raw", "synced_at"])
            result.updated += 1

    # Suppressions : seulement si la liste a été lue jusqu'au bout. Une liste vide alors qu'on connaît
    # des activités ressemble plus à une panne qu'à un compte vidé : on ne supprime rien.
    if complete and (seen or not known):
        gone = set(known) - seen
        if gone:
            result.removed = journeys.forget_activities(account, gone)
        stale = ignored - seen  # retirées du site puis supprimées sur Strava : plus rien à ignorer
        if stale:
            account.removed_activities.filter(strava_id__in=stale).delete()
    account.synced_at = timezone.now()
    account.save(update_fields=["synced_at", "updated_at"])
    return result


PHOTO_BATCH = 40  # appels API maximum par synchronisation (limite Strava : 100 / 15 min)


def is_video(item):
    """Vidéo Strava : renvoyée par le même point d'accès que les photos, avec une vignette.

    Strava ne documente pas ce point d'accès ; on recoupe plusieurs indices connus :
    type / media_type à 2, adresse de lecture vidéo ou durée.
    """
    return (item.get("type") in (2, "2", "video", "Video") or item.get("media_type") in (2, "2", "video")
            or bool(item.get("video_url")) or bool(item.get("duration")))


def import_activity_photos(account, activity):
    """Importe les photos d'une activité (un appel Strava), sans les vidéos. Renvoie le nombre de photos."""
    items = strava.activity_photos(account, activity.strava_id)
    keep = []
    for item in items:
        if is_video(item):
            continue
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
    activity.photo_count = len(keep)  # le compteur de la liste Strava n'est pas fiable
    activity.save(update_fields=["photos_synced_at", "photo_count"])
    # Les photos ont pu être préchargées (prefetch_related) avant l'import : on oublie ce cache
    getattr(activity, "_prefetched_objects_cache", {}).pop("photos", None)
    return len(keep)


def sync_photos(account, limit=PHOTO_BATCH, everything=False):
    """Importe les photos des activités qui en ont et qui n'ont pas encore été traitées.

    Limité à `limit` activités par appel pour ménager le quota d'API ;
    les suivantes seront traitées à la prochaine synchronisation.
    `everything` : vérifie aussi les activités que Strava annonce sans photo (son compteur
    vaut souvent 0 à tort), au prix d'un appel par activité.
    Renvoie (photos importées, activités restant à traiter).
    """
    pending = account.activities.filter(photos_synced_at__isnull=True)
    if not everything:
        pending = pending.filter(photo_count__gt=0)
    imported = 0
    for activity in pending.order_by("-start_date")[:limit]:
        imported += import_activity_photos(account, activity)
    return imported, pending.count()


ON_DEMAND_LIMIT = 20  # appels maximum à l'ouverture d'une page


def ensure_photos(account, activities, limit=None):
    """Importe à la demande les photos des activités affichées qui n'ont pas encore été vérifiées.

    Toutes les activités jamais vérifiées sont interrogées, même celles que Strava annonce
    sans photo : le compteur de la liste (total_photo_count) vaut souvent 0 à tort.
    Appelée à l'ouverture d'un périple, d'une activité ou d'un livre : seules les
    activités réellement consultées consomment du quota Strava. Ne lève jamais :
    renvoie (photos importées, activités encore en attente, message d'erreur ou None).
    """
    limit = limit or ON_DEMAND_LIMIT
    pending = [a for a in activities if not a.photos_synced_at]
    imported = 0
    for done, activity in enumerate(pending[:limit]):
        try:
            imported += import_activity_photos(account, activity)
        except strava.StravaError as exc:
            return imported, len(pending) - done, str(exc)
    return imported, max(len(pending) - limit, 0), None
