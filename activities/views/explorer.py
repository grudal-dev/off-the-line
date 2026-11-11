"""Explorer (la carte de toutes les sorties), le bouton « Créer » et Mes créations."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats
from django.views.decorators.http import require_POST

from .. import book, checks, journeys, sports
from ..models import Activity, Book, JourneyNote, Poster
from .common import account_required, _wants_json, _signed, _date_range, _stage_colors, _selected_ids, _names_background, _sync_background


@account_required
def all_routes_map(request, account):
    """Carte qui superpose tous les parcours de l'utilisateur."""
    # « account » doit être chargé (sinon une requête par activité) ; les tracés partent encodés
    # (format Strava, 3 à 5 fois plus léger) et sont décodés par le navigateur, miniatures comprises.
    qs = account.activities.exclude(summary_polyline="").only(
        "account", "strava_id", "name", "sport_type", "start_date", "start_date_local", "distance", "summary_polyline",
        "moving_time", "confirmed",
    ).prefetch_related("photos")
    activities = list(qs)
    doubts = checks.suspects(activities)
    routes, counts = [], {}
    for a in activities:
        fam = sports.family(a.sport_type)
        counts[fam] = counts.get(fam, 0) + 1
        routes.append({
            "id": a.strava_id, "name": a.name, "sport": sports.label(a.sport_type),
            "family": fam, "color": sports.FAMILIES[fam][1],
            "date": formats.date_format(a.start_date_local or a.start_date, "j F Y"),
            "day": (a.start_date_local or a.start_date).isoformat(),
            "km": a.distance_km, "poly": a.summary_polyline,
            "photos": [p.url for p in a.photos.all()],
        })
    suspects = [{"id": r["id"], "name": r["name"], "date": r["date"], "why": doubts[r["id"]]}
                for r in routes if r["id"] in doubts]
    families = [
        {"key": key, "label": label, "color": color, "count": counts[key]}
        for key, (label, color) in sports.FAMILIES.items() if key in counts
    ]
    total_km = round(sum(r["km"] for r in routes))
    everything = journeys.for_account(account)
    found = [j for j in everything if not j.hidden]
    hidden_trips = [{"title": j.name, "dates": _date_range(j), "url": reverse("activities:journey", args=[j.id])}
                    for j in everything if j.hidden]
    trips = [
        {"id": j.id, "title": j.name, "url": reverse("activities:journey", args=[j.id]),
         "dates": _date_range(j), "stages": [a.strava_id for a in j.activities],
         "colors": _stage_colors(len(j.legs)), "transfers": j.transfers,
         "days": j.days, "km": round(j.distance_km), "favorite": j.favorite}
        for j in found
    ]
    suggestion = _suggestion(request, account, found)
    routes_data = [{k: r[k] for k in ("id", "name", "sport", "family", "color", "date", "day", "km", "poly")} for r in routes]
    by_year = {}
    for r in routes:
        y = by_year.setdefault(r["day"][:4], {"year": r["day"][:4], "count": 0, "km": 0})
        y["count"] += 1
        y["km"] += r["km"]
    years = sorted(by_year.values(), key=lambda y: y["year"], reverse=True)
    return render(request, "activities/map.html",
                  {"routes": routes, "routes_data": routes_data, "families": families, "total_km": total_km,
                   "journeys": trips, "years": years, "suggestion": suggestion,
                   "hidden_journeys": hidden_trips, "suspects": suspects,
                   # Une tâche à la fois : Strava d'abord, puis les noms de lieux des voyages
                   "background": _sync_background(account) or _names_background(everything)})


DISMISSED_KEY = "dismissed_suggestions"


def _suggestion(request, account, found):
    """Le voyage proposé au-dessus de la carte : le préféré, sinon le plus marquant pas encore créé."""
    used = {p.settings.get("journey") for p in account.posters.only("settings") if p.settings.get("source") == "journey"}
    for b in account.books.only("journey_ids"):
        used.update(b.journey_ids or [])
    dismissed = set(request.session.get(DISMISSED_KEY, []))
    candidates = [j for j in found if j.id not in used and j.id not in dismissed and j.days >= 2]
    if not candidates:
        return None
    j = max(candidates, key=journeys.highlight_score)
    return {"id": j.id, "title": j.name, "dates": _date_range(j), "days": j.days, "km": round(j.distance_km),
            "photos": sum(a.photo_count or 0 for a in j.activities), "favorite": j.favorite,
            "heading": "Ton voyage préféré" if j.favorite else "Ton plus grand voyage",
            "url": reverse("activities:journey", args=[j.id])}


@login_required
@require_POST
def suggestion_dismiss(request, journey_id):
    """✕ sur la suggestion : on n'en reparle plus (pour cette session)."""
    dismissed = request.session.get(DISMISSED_KEY, [])
    if journey_id not in dismissed:
        request.session[DISMISSED_KEY] = (dismissed + [journey_id])[-200:]
    return JsonResponse({"ok": True}) if _wants_json(request) else redirect("activities:map")


@account_required
@require_POST
def journey_favorite(request, account, journey_id):
    """L'étoile « Mon préféré » : un seul voyage préféré à la fois."""
    if journey_id not in {j.id for j in journeys.for_account(account)}:
        raise Http404
    note, _ = JourneyNote.objects.get_or_create(account=account, journey_id=journey_id)
    on = not note.favorite
    if on:
        account.journey_notes.filter(favorite=True).exclude(pk=note.pk).update(favorite=False)
        request.session[DISMISSED_KEY] = [i for i in request.session.get(DISMISSED_KEY, []) if i != journey_id]
    note.favorite = on
    note.save(update_fields=["favorite", "updated_at"])
    return JsonResponse({"favorite": on}) if _wants_json(request) else redirect("activities:map")


@account_required
def creations(request, account):
    """Mes créations : affiches et livres, les plus récents d'abord."""
    found = {j.id: j for j in journeys.for_account(account)}
    items = [{"kind": "poster", "obj": p, "updated": p.updated_at,
              "subtitle": f"Affiche {p.settings.get('format', 'A3').replace('x', ' × ')}"}
             for p in account.posters.all()]
    for b in account.books.all():
        if b.activity_ids:
            sub = f"Livre · {len(b.activity_ids)} sortie{'s' if len(b.activity_ids) > 1 else ''}"
        else:
            names = [found[i].name for i in b.journey_ids if i in found]
            sub = "Livre · " + (", ".join(names) if names else "voyage introuvable")
        items.append({"kind": "book", "obj": b, "updated": b.updated_at, "subtitle": sub})
    items.sort(key=lambda i: i["updated"], reverse=True)
    return render(request, "activities/creations.html", {"items": items})


@require_POST
@account_required
def create(request, account):
    """Le bouton « Créer » d'Explorer : une sélection (voyage, sortie, année, zone) → affiche ou livre."""
    kind = request.POST.get("kind")
    title = request.POST.get("title", "").strip()[:120]
    journey_id, activity_id = _signed(request.POST.get("journey")), _signed(request.POST.get("activity"))
    year = request.POST.get("year", "")
    ids = _selected_ids(request)
    settings_, name = {}, ""
    if journey_id is not None:
        found = {j.id: j for j in journeys.for_account(account)}
        j = found.get(journey_id)
        if j is None:
            raise Http404
        settings_, name = {"source": "journey", "journey": j.id}, j.name
        ids = [a.strava_id for a in j.activities]
    elif activity_id is not None:
        a = get_object_or_404(Activity, account=account, strava_id=activity_id)
        settings_, name, ids = {"source": "activity", "activity": a.strava_id}, a.name, [a.strava_id]
    elif year.isdigit():
        settings_, name = {"source": "all", "from": f"{year}-01-01", "to": f"{year}-12-31", "hidden": []}, f"Mes sorties {year}"
        ids = list(account.activities.exclude(summary_polyline="").filter(
            start_date__year=int(year)).order_by("start_date").values_list("strava_id", flat=True))
    elif ids:
        settings_, name = {"source": "selection", "selection": ids}, "Ma sélection"
    else:
        hide = [f for f in request.POST.getlist("hide") if f in sports.FAMILIES]
        settings_, name = {"source": "all", "from": "", "to": "", "hidden": hide}, "Toutes mes sorties"

    if kind == "livre":
        if journey_id is not None:
            j = found[journey_id]
            return _open_new_book(request, account, [j], journey_ids=[j.id], title=title or j.name)
        if len(ids) > MAX_BOOK_ACTIVITIES:
            messages.error(request, f"Un livre compte au plus {MAX_BOOK_ACTIVITIES} sorties ({len(ids)} ici) : "
                                    "choisis un voyage, ou trace une zone plus petite.")
            return redirect("activities:map")
        journey = journeys.from_activities(account, ids)
        if journey is None:
            messages.error(request, "Aucune de ces sorties n'a de tracé : impossible de créer le livre.")
            return redirect("activities:map")
        return _open_new_book(
            request, account, [journey], activity_ids=[a.strava_id for a in journey.activities],
            title=title or (name if name != "Ma sélection" else journey.place_name) or "Mon livre")

    if title:
        settings_["title"] = title
    obj = Poster.objects.create(account=account, title=title or name, settings=settings_)
    return redirect("activities:poster_edit", poster_id=obj.pk)


MAX_BOOK_ACTIVITIES = 150  # une page par étape : au-delà, le livre devient ingérable


def _open_new_book(request, account, journey_list, **fields):
    """Crée le livre, prépare ses étapes (descriptions, profils) et l'ouvre."""
    new = Book.objects.create(account=account, **fields)
    _, problem = book.prepare(account, journey_list)
    if problem:
        messages.info(request, problem)
    # Les photos sont récupérées ensuite par la page du livre, en arrière-plan (book_fetch_photos)
    return redirect("activities:book_detail", book_id=new.pk)
