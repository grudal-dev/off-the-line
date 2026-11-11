"""Les sorties : liste, page d'une sortie, synchronisation avec Strava, retirer, remettre, garder."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .. import checks, journeys, polyline, strava
from ..models import Activity, RemovedActivity, StravaAccount
from ..sync import import_activity, sync_activities
from .common import _account_or_none, account_required, _back, _wants_json, _complete, _sync_photos_with_message


@require_POST
@login_required
def activity_complete(request, strava_id):
    account = _account_or_none(request)
    activity = get_object_or_404(Activity, strava_id=strava_id, account__user=request.user)
    return JsonResponse(_complete(account, [activity]))


@account_required
def activity_list(request, account):
    qs = account.activities.all()
    sport = request.GET.get("sport", "")
    if sport:
        qs = qs.filter(sport_type=sport)
    page_obj = Paginator(qs.prefetch_related("photos"), 30).get_page(request.GET.get("page"))
    sports = account.activities.order_by("sport_type").values_list("sport_type", flat=True).distinct()

    return render(
        request,
        "activities/list.html",
        {"account": account, "page_obj": page_obj, "sports": sports, "sport": sport,
         "total": account.activities.count(), "removed": account.removed_activities.all()},
    )


@require_POST
@account_required
def activity_sync(request, account):
    """« Synchroniser » : aligne le site sur Strava (nouvelles sorties, modifications, suppressions)."""
    try:
        result = sync_activities(account)
    except strava.StravaError as exc:
        messages.error(request, f"Synchronisation interrompue : {exc}")
    else:
        text = result.summary()
        if text:
            messages.success(request, f"Strava synchronisé : {text}.")
        else:
            messages.info(request, "Rien de nouveau sur Strava.")
        _sync_photos_with_message(request, account)
    return _back(request, "activities:map")


@require_POST
@login_required
def activity_sync_auto(request):
    """Appelée par background.js : même synchronisation, sans message ; la page se recharge s'il y a du nouveau."""
    account = _account_or_none(request)
    if account is None or account.is_local:
        raise Http404
    try:
        result = sync_activities(account)
    except strava.RateLimited as exc:
        return JsonResponse({"found": 0, "remaining": 1, "error": str(exc), "retry": True})
    except strava.StravaError as exc:
        return JsonResponse({"found": 0, "remaining": 0, "error": str(exc), "retry": False})
    return JsonResponse({"found": result.changed, "remaining": 0, "error": None, "retry": False})


    # Les photos restantes et les noms de lieux arrivent ensuite à la demande, en arrière-plan


# ---------- Retirer une sortie du site ----------
# Une sortie saisie par erreur (trajet en voiture, doublon, GPS fou) fausse la carte, les voyages,
# les affiches et les livres. L'utilisateur la retire ici ; rien n'est modifié sur Strava.

@require_POST
@account_required
def activity_remove(request, account, strava_id):
    activity = get_object_or_404(Activity, strava_id=strava_id, account=account)
    if not activity.from_file:  # la synchronisation ne doit pas la ramener
        RemovedActivity.objects.update_or_create(
            account=account, strava_id=activity.strava_id,
            defaults={"name": activity.name, "sport_type": activity.sport_type,
                      "start_date": activity.start_date, "distance": activity.distance})
    name = activity.name
    journeys.forget_activities(account, [activity.strava_id])
    if _wants_json(request):  # retirée depuis Explorer : la carte se met à jour sans rechargement
        return JsonResponse({"ok": True})
    if activity.from_file:
        messages.success(request, f"« {name} » supprimée. Pour la retrouver, importe à nouveau son fichier.")
    else:
        messages.success(request, f"« {name} » retirée du site (elle reste sur Strava). "
                                  "Tu peux la remettre depuis « Toutes mes activités ».")
    return _back(request, "activities:map")


@require_POST
@account_required
def activity_restore(request, account, strava_id):
    """Remet une sortie retirée : un appel à Strava, sinon elle reviendra à la prochaine synchronisation."""
    removed = get_object_or_404(RemovedActivity, account=account, strava_id=strava_id)
    removed.delete()
    try:
        activity = import_activity(account, strava_id)
    except strava.StravaError:
        StravaAccount.objects.filter(pk=account.pk).update(synced_at=None)
        messages.info(request, f"« {removed.name} » reviendra à la prochaine synchronisation avec Strava.")
    else:
        messages.success(request, f"« {activity.name} » est de retour.")
    return _back(request, "activities:list")


@require_POST
@account_required
def activity_keep(request, account, strava_id):
    """« Garder » une sortie signalée à vérifier : elle est correcte, on ne la signale plus."""
    get_object_or_404(Activity, strava_id=strava_id, account=account)
    Activity.objects.filter(strava_id=strava_id, account=account).update(confirmed=True)
    return JsonResponse({"ok": True}) if _wants_json(request) else _back(request, "activities:map")


def _nearby(account, activity):
    """Les sorties voisines dans le temps : de quoi repérer un doublon de celle-ci."""
    window = (activity.start_date - checks.SAME_START, activity.start_date + checks.SAME_START)
    return list(account.activities.filter(start_date__range=window))


@account_required
def activity_detail(request, account, strava_id):
    activity = get_object_or_404(Activity, strava_id=strava_id, account=account)
    route = polyline.decode(activity.summary_polyline) if activity.summary_polyline else []
    background = None
    if not activity.photos_synced_at:  # photos récupérées juste après l'affichage
        background = {"url": reverse("activities:detail_complete", args=[strava_id]), "pending": 1, "what": "photos"}
    return render(request, "activities/detail.html",
                  {"activity": activity, "route": route, "photos": activity.photos.all(), "background": background,
                   "suspect": checks.suspects(_nearby(account, activity)).get(activity.strava_id)})
