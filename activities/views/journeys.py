"""La page d'un voyage : étapes, récit, transferts, corrections."""

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import formats
from django.views.decorators.http import require_POST

from .. import journeys, polyline, sports
from ..models import JourneyNote
from .common import _account_or_none, account_required, _signed, _date_range, _stage_colors, _journey_background, _complete, _find_journey


@require_POST
@login_required
def journey_complete(request, journey_id):
    account = _account_or_none(request)
    if account is None:
        raise Http404
    journey = _find_journey(account, journey_id)
    return JsonResponse(_complete(account, journey.activities, [journey]))


@require_POST
@login_required
def journeys_complete(request):
    account = _account_or_none(request)
    if account is None:
        raise Http404
    return JsonResponse(_complete(account, journeys_to_name=journeys.for_account(account)))


@account_required
def journey_detail(request, account, journey_id):
    if request.method == "POST" and "transfer_index" in request.POST:
        found = _find_journey(account, journey_id)
        index, mode = request.POST.get("transfer_index", ""), request.POST.get("mode", "")
        if index.isdigit() and int(index) in found.transfers and mode in journeys.TRANSPORT_MODES:
            note, _ = JourneyNote.objects.get_or_create(account=account, journey_id=journey_id)
            # Réécrit au format « a<id> » : les modes suivent l'étape même si on en retire d'autres
            modes = {journeys.transfer_key(found.legs[i]): m for i, m in found.transfer_modes.items()}
            modes[journeys.transfer_key(found.legs[int(index)])] = mode
            note.transfers = modes
            note.save()
        return redirect("activities:journey", journey_id=journey_id)
    action = request.POST.get("action") if request.method == "POST" else None
    if action == "stages":
        _correct_stages(request, account, _find_journey(account, journey_id))
        return redirect("activities:journey", journey_id=journey_id)
    if action in ("hide", "unhide", "reset"):
        _find_journey(account, journey_id)
        note, _ = JourneyNote.objects.get_or_create(account=account, journey_id=journey_id)
        if action == "reset":
            note.excluded, note.added, note.stage_names = [], [], {}
            messages.success(request, "Étapes remises comme détectées automatiquement.")
        else:
            note.hidden = action == "hide"
            messages.success(request, "Voyage masqué : il n'apparaît plus dans Explorer. Tu le retrouves en bas de l'onglet Voyages."
                             if note.hidden else "Périple rétabli.")
        note.save()
        if action == "hide":
            return redirect("activities:map")
        return redirect("activities:journey", journey_id=journey_id)
    if request.method == "POST":
        _find_journey(account, journey_id)  # 404 si ce n'est pas un périple de l'utilisateur
        JourneyNote.objects.update_or_create(
            account=account, journey_id=journey_id,
            defaults={"title": request.POST.get("title", "").strip()[:200],
                      "story": request.POST.get("story", "").strip()},
        )
        messages.success(request, "Périple mis à jour.")
        return redirect("activities:journey", journey_id=journey_id)
    journey = _find_journey(account, journey_id)
    colors = _stage_colors(len(journey.legs))
    stages = []
    for i, (leg, color) in enumerate(zip(journey.legs, colors)):
        a = leg.activity
        stages.append({
            "n": i + 1, "id": a.strava_id, "name": a.name, "color": color, "label": leg.label,
            "custom_label": leg.custom_label, "auto_label": leg.auto_label,
            "sport": sports.label(a.sport_type),
            "date": formats.date_format(a.start_date_local or a.start_date, "l j F"),
            "km": a.distance_km, "elevation": round(a.total_elevation_gain),
            "duration": a.duration_display, "night_after": i in journey.nights,
            "transfer_after": journey.transfer_after(i),
            "coords": polyline.decode(a.summary_polyline),
            "photos": [p.url for p in a.photos.all()],
        })
    nights = sum(1 for s in stages if s["night_after"])
    removed = [{"id": leg.activity.strava_id, "name": leg.activity.name, "label": leg.label,
                "date": formats.date_format(leg.activity.start_date_local or leg.activity.start_date, "l j F"),
                "km": leg.activity.distance_km} for leg in journey.removed]
    candidates = [{"id": a.strava_id, "name": a.name, "km": a.distance_km, "sport": sports.label(a.sport_type),
                   "date": formats.date_format(a.start_date_local or a.start_date, "l j F")}
                  for a in _addable_activities(account, journey)]
    return render(request, "activities/journey.html", {
        "journey": journey, "dates": _date_range(journey), "stages": stages, "nights": nights,
        "removed": removed, "candidates": candidates,
        "background": _journey_background(journey),
        "stages_json": [{k: s[k] for k in ("n", "id", "name", "label", "color", "coords", "night_after",
                                            "transfer_after")}
                        for s in stages],
        "transport_modes": journeys.TRANSPORT_MODES,
    })


ADD_WINDOW = timedelta(days=3)  # on peut ajouter les sorties jusqu'à 3 jours avant/après le périple


def _addable_activities(account, journey):
    """Activités qu'on peut ajouter à un périple : avec tracé, autour de ses dates, pas déjà dedans."""
    present = {leg.activity.strava_id for leg in journey.legs + journey.removed}
    start = journey.legs[0].start_at - ADD_WINDOW
    end = journey.legs[-1].start_at + ADD_WINDOW
    qs = (account.activities.exclude(summary_polyline="").filter(start_date__range=(start, end))
          .exclude(strava_id__in=present).order_by("start_date"))
    return list(qs.only("strava_id", "name", "sport_type", "distance", "start_date", "start_date_local")[:40])


def _ids(request, name):
    return {v for v in map(_signed, request.POST.getlist(name)) if v is not None}


def _correct_stages(request, account, journey):
    """Formulaire « Corriger les étapes » : renommer, retirer, remettre, ajouter."""
    note, _ = JourneyNote.objects.get_or_create(account=account, journey_id=journey.id)
    current = [leg.activity.strava_id for leg in journey.legs]
    excluded = {int(x) for x in note.excluded}
    added = [int(x) for x in note.added]

    remove = _ids(request, "remove") & set(current)
    restore = _ids(request, "restore") & excluded
    add = _ids(request, "add") & {a.strava_id for a in _addable_activities(account, journey)}
    if len(current) - len(remove) + len(restore) + len(add) < 1:
        messages.error(request, "Un périple doit garder au moins une étape. "
                                "Pour l'écarter entièrement, utilise « Ce n'est pas un périple ».")
        return

    names = dict(note.stage_names)
    for aid in current:
        if f"name_{aid}" in request.POST:
            value = request.POST[f"name_{aid}"].strip()[:200]
            if value:
                names[str(aid)] = value
            else:
                names.pop(str(aid), None)

    # Une étape ajoutée à la main qu'on retire est simplement oubliée ; une étape détectée est exclue
    excluded = (excluded - restore) | {aid for aid in remove if aid not in added}
    added = [aid for aid in added if aid not in remove] + sorted(add)
    note.excluded, note.added, note.stage_names = sorted(excluded), added, names
    note.save()
    changes = []
    if remove:
        changes.append(f"{len(remove)} étape(s) retirée(s)")
    if restore or add:
        changes.append(f"{len(restore) + len(add)} étape(s) ajoutée(s)")
    messages.success(request, (", ".join(changes) + ".").capitalize() if changes else "Étapes enregistrées.")
