"""L'éditeur d'affiche : données, villes, enregistrement."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .. import journeys, polyline, sports, towns
from ..models import Poster
from .common import _account_or_none, account_required, _signed, _json_body, _date_range, _stops


def _poster_context(account):
    """Données de l'éditeur d'affiche : tracés encodés, familles de sports, périples."""
    qs = account.activities.exclude(summary_polyline="").only(
        "account", "strava_id", "name", "sport_type", "start_date", "start_date_local", "distance",
        "total_elevation_gain", "summary_polyline", "profile"
    ).order_by("start_date")
    routes, counts = [], {}
    for a in qs:
        fam = sports.family(a.sport_type)
        counts[fam] = counts.get(fam, 0) + 1
        route = {
            "id": a.strava_id, "name": a.name, "family": fam,
            "day": (a.start_date_local or a.start_date).date().isoformat(),
            "km": a.distance_km, "elev": round(a.total_elevation_gain), "poly": a.summary_polyline,
        }
        if a.profile:  # profil d'altitude (récupéré pour les livres) : allégé pour l'affiche
            step = max(1, len(a.profile) // POSTER_PROFILE_POINTS)
            route["prof"] = [round(alt) for _, alt in a.profile[::step]]
        routes.append(route)
    families = [{"key": key, "label": label, "color": color, "count": counts[key]}
                for key, (label, color) in sports.FAMILIES.items() if key in counts]
    trips = [
        {"id": j.id, "title": j.name, "dates": _date_range(j), "days": j.days,
         "stages": [a.strava_id for a in j.activities], "transfers": j.transfers, "stops": _stops(j)}
        for j in sorted(journeys.for_account(account, include_hidden=False),
                        key=lambda j: j.legs[0].start_at, reverse=True)
    ]
    return {"routes": routes, "families": families, "journeys": trips, "athlete": account.firstname}


@login_required
def poster_towns(request):
    """Villes pour l'affiche (GeoNames, activities/towns.py).

    ?ids=1,2,3&km=10 : villes à moins de km des tracés (dans l'ordre donné) et noms du départ et de l'arrivée.
    ?q=chamon&lat=…&lng=… : recherche par nom, les plus proches d'abord.
    """
    account = _account_or_none(request)
    if account is None:
        return JsonResponse({"error": "non connecté"}, status=403)
    if "q" in request.GET:
        try:
            lat, lng = float(request.GET["lat"]), float(request.GET["lng"])
        except (KeyError, ValueError):
            lat = lng = None
        return JsonResponse({"results": towns.search(request.GET["q"][:60], lat, lng)})
    ids = [i for i in (_signed(x) for x in request.GET.get("ids", "").split(",")[:2000]) if i is not None]
    try:
        km = min(30.0, max(1.0, float(request.GET.get("km", 10))))
    except ValueError:
        km = 10.0
    lines = dict(account.activities.filter(strava_id__in=ids).exclude(summary_polyline="")
                 .values_list("strava_id", "summary_polyline"))
    tracks = [polyline.decode(lines[i]) for i in ids if i in lines]
    tracks = [t for t in tracks if t]
    # Un point tous les ~1 km suffit : les villes sont cherchées dans un rayon de plusieurs km
    points = []
    for t in tracks:
        step = max(1, len(t) // 400)
        points += t[::step] + [t[-1]]
    ends = [towns.nearest(*tracks[0][0]), towns.nearest(*tracks[-1][-1])] if tracks else ["", ""]
    return JsonResponse({"near": towns.near_track(points, km), "ends": ends})


@account_required
def poster_edit(request, account, poster_id):
    """Une affiche de « Mes créations » : mêmes outils, réglages enregistrés au fil de l'eau."""
    obj = get_object_or_404(Poster, pk=poster_id, account=account)
    creation = {"id": obj.pk, "title": obj.title, "settings": obj.settings,
                "save": reverse("activities:poster_save", args=[obj.pk])}
    return render(request, "activities/poster.html",
                  {**_poster_context(account), "initial": {}, "creation": creation, "poster_obj": obj})


@require_POST
@login_required
def poster_save(request, poster_id):
    account = _account_or_none(request)
    obj = get_object_or_404(Poster, pk=poster_id, account=account)
    data = _json_body(request)
    if isinstance(data.get("settings"), dict):
        obj.settings = data["settings"]
    if isinstance(data.get("title"), str):
        obj.title = data["title"].strip()[:200]
    thumb = data.get("thumbnail")
    if isinstance(thumb, str) and thumb.startswith("data:image/") and len(thumb) < 400_000:
        obj.thumbnail = thumb
    obj.save()
    return JsonResponse({"ok": True})


@require_POST
@login_required
def poster_delete(request, poster_id):
    account = _account_or_none(request)
    get_object_or_404(Poster, pk=poster_id, account=account).delete()
    messages.info(request, "Affiche supprimée.")
    return redirect("activities:creations")


POSTER_PROFILE_POINTS = 40
