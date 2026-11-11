"""Outils partagés par les vues : compte de l'utilisateur, retours, tâches de fond, mises en forme."""

import json
from datetime import timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import formats, timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .. import journeys, places, strava
from ..sync import ensure_photos, sync_photos


def _account_or_none(request):
    return getattr(request.user, "strava", None)


def account_required(view):
    """Vue réservée à un utilisateur connecté qui a un compte (Strava ou « sans Strava ») : reçoit ce compte."""
    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        account = _account_or_none(request)
        if account is None:
            return redirect("activities:home")
        return view(request, account, *args, **kwargs)
    return wrapper


def _back(request, default):
    """Adresse où revenir après une action (champ « next » du formulaire), si elle est sur ce site."""
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(default)


# ---------- Chargement en arrière-plan ----------
# Les pages s'affichent sans attendre Strava ni le géocodage ; le navigateur appelle
# ensuite une adresse « …/completer/ » en boucle (static/activities/background.js) qui
# traite un petit lot à chaque fois et renvoie {found, remaining, error, retry}.

PHOTO_BATCH_BG = 6   # activités vérifiées sur Strava par appel


NAMES_BATCH_BG = 8   # points géocodés par appel


def _names_background(found):
    missing = places.missing_points(found)
    if not missing:
        return None
    return {"url": reverse("activities:journeys_complete"), "pending": len(missing), "what": "lieux"}


def _journey_background(journey):
    pending = sum(1 for a in journey.activities if not a.photos_synced_at) + len(places.missing_points([journey]))
    if not pending:
        return None
    return {"url": reverse("activities:journey_complete", args=[journey.id]), "pending": pending,
            "what": "photos et lieux"}


def _complete(account, activities=(), journeys_to_name=(), photo_batch=None):
    """Un lot de travail en arrière-plan : photos Strava puis noms de lieux."""
    photo_batch = photo_batch or PHOTO_BATCH_BG
    found, error, retry = 0, None, False
    if activities:
        imported, _, error = ensure_photos(account, activities, limit=photo_batch)
        found += imported
        retry = bool(error)  # quota ou réseau : on réessaiera dans une minute
    remaining = sum(1 for a in activities if not a.photos_synced_at)
    if journeys_to_name and not error:
        names, names_left, failed = places.fill(places.missing_points(journeys_to_name),
                                                places.Budget(lookups=NAMES_BATCH_BG, nominatim=1))
        found += names
        remaining += names_left
        if failed:
            error = "Noms de lieux indisponibles pour l'instant."
    elif journeys_to_name:
        remaining += len(places.missing_points(journeys_to_name))
    return {"found": found, "remaining": remaining, "error": error, "retry": retry}


AUTO_SYNC_EVERY = timedelta(hours=12)


def _sync_background(account):
    """Synchronisation automatique à l'ouverture d'Explorer, si la dernière date de plus de 12 h."""
    if account.is_local or (account.synced_at and account.synced_at > timezone.now() - AUTO_SYNC_EVERY):
        return None
    return {"url": reverse("activities:sync_auto"), "pending": 1, "what": "sorties Strava"}


def _sync_photos_with_message(request, account):
    """Importe un lot de photos et l'indique à l'utilisateur (une erreur ici n'est pas bloquante)."""
    try:
        photos, remaining = sync_photos(account)
    except strava.StravaError as exc:
        messages.error(request, f"Import des photos interrompu : {exc}")
        return
    if photos:
        messages.success(request, f"{photos} photo{'s' if photos > 1 else ''} importée{'s' if photos > 1 else ''}.")


def _wants_json(request):
    return "application/json" in request.headers.get("Accept", "")


def _stops(journey):
    """Lieux d'étape nommés (départ puis chaque arrivée) : [lat, lng, nom], sans doublon de nom."""
    points = [(journey.legs[0].start, journey.legs[0].start_name)] + [(leg.end, leg.end_name) for leg in journey.legs]
    stops, seen = [], set()
    for (lat, lng), name in points:
        if name and name not in seen:
            seen.add(name)
            stops.append([round(lat, 4), round(lng, 4), name])
    # l'arrivée reste en dernier, au point d'arrivée, même si on y était déjà passé
    last = journey.legs[-1]
    if last.end_name:
        stops = [s for s in stops if s[2] != last.end_name] + [[round(last.end[0], 4), round(last.end[1], 4), last.end_name]]
    return stops


def _signed(text):
    """Entier signé lu dans un paramètre (les activités importées de fichiers ont un identifiant négatif)."""
    text = (text or "").strip()
    return int(text) if text.lstrip("-").isdigit() and text.count("-") <= 1 and not text.endswith("-") else None


def _date_range(journey):
    first, last = journey.first_day, journey.last_day
    if first.year != last.year:
        return f"du {formats.date_format(first, 'j F Y')} au {formats.date_format(last, 'j F Y')}"
    if first.month != last.month:
        return f"du {formats.date_format(first, 'j F')} au {formats.date_format(last, 'j F Y')}"
    return f"du {first.day} au {formats.date_format(last, 'j F Y')}"


def _stage_colors(n):
    """Dégradé orange → violet : la couleur raconte la progression du voyage."""
    a, b = (252, 76, 2), (106, 27, 154)
    if n == 1:
        return ["#fc4c02"]
    return ["#%02x%02x%02x" % tuple(round(a[k] + (b[k] - a[k]) * i / (n - 1)) for k in range(3)) for i in range(n)]


def _find_journey(account, journey_id, budget=None):
    journey = next((j for j in journeys.for_account(account, budget) if j.id == journey_id), None)
    if journey is None:
        raise Http404("Périple introuvable")
    return journey


def _selected_ids(request):
    return list(dict.fromkeys(v for v in map(_signed, request.POST.getlist("a")) if v is not None))


def _json_body(request):
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}
