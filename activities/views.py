import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import formats, timezone
from django.views.decorators.http import require_POST

from . import book, geo, journeys, places, polyline, sports, strava
from .models import Activity, Book, BookPhoto, BookStage, JourneyNote, StravaAccount
from .sync import ensure_photos, sync_activities, sync_photos

SESSION_STATE_KEY = "strava_oauth_state"


def home(request):
    if request.user.is_authenticated and hasattr(request.user, "strava"):
        return redirect("activities:list")
    return render(request, "activities/home.html")


def strava_login(request):
    if not settings.STRAVA_CLIENT_ID or not settings.STRAVA_CLIENT_SECRET:
        messages.error(request, "Clés Strava manquantes : remplis STRAVA_CLIENT_ID et STRAVA_CLIENT_SECRET dans .env.")
        return redirect("activities:home")
    state = secrets.token_urlsafe(24)
    request.session[SESSION_STATE_KEY] = state
    return redirect(strava.authorize_url(state))


def strava_callback(request):
    expected_state = request.session.pop(SESSION_STATE_KEY, None)
    if not expected_state or request.GET.get("state") != expected_state:
        messages.error(request, "Réponse Strava invalide, recommence la connexion.")
        return redirect("activities:home")

    if request.GET.get("error"):
        messages.info(request, "Connexion Strava annulée.")
        return redirect("activities:home")

    granted = set(request.GET.get("scope", "").split(","))
    if "activity:read_all" not in granted and "activity:read" not in granted:
        messages.error(request, "Il faut autoriser l'accès à tes activités pour utiliser le site.")
        return redirect("activities:home")

    code = request.GET.get("code")
    if not code:
        messages.error(request, "Code d'autorisation manquant.")
        return redirect("activities:home")

    try:
        data = strava.exchange_code(code)
    except strava.StravaError as exc:
        messages.error(request, f"Impossible de se connecter à Strava. {exc}")
        return redirect("activities:home")

    athlete = data["athlete"]
    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username=f"strava_{athlete['id']}",
        defaults={"first_name": athlete.get("firstname", ""), "last_name": athlete.get("lastname", "")},
    )
    StravaAccount.objects.update_or_create(
        athlete_id=athlete["id"],
        defaults={
            "user": user,
            "firstname": athlete.get("firstname", ""),
            "lastname": athlete.get("lastname", ""),
            "profile_picture": athlete.get("profile", "") or "",
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "expires_at": strava.expires_at_to_datetime(data["expires_at"]),
            "scope": ",".join(sorted(granted)),
        },
    )
    login(request, user)

    account = user.strava
    if not account.activities.exists():
        try:
            count = sync_activities(account)
            messages.success(request, f"Bienvenue ! {count} activité(s) importée(s) depuis Strava.")
            _sync_photos_with_message(request, account)
        except strava.StravaError as exc:
            messages.error(request, f"Connecté, mais l'import des activités a échoué : {exc}")
    return redirect("activities:list")


@require_POST
def strava_logout(request):
    logout(request)
    return redirect("activities:home")


@require_POST
@login_required
def strava_disconnect(request):
    """Révoque l'accès Strava et supprime les jetons stockés."""
    account = getattr(request.user, "strava", None)
    if account:
        strava.deauthorize(account)
        account.delete()
    logout(request)
    messages.info(request, "Ton compte Strava a été déconnecté du site.")
    return redirect("activities:home")


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


@require_POST
@login_required
def activity_complete(request, strava_id):
    from django.http import JsonResponse
    account = _account_or_none(request)
    activity = get_object_or_404(Activity, strava_id=strava_id, account__user=request.user)
    return JsonResponse(_complete(account, [activity]))


@require_POST
@login_required
def journey_complete(request, journey_id):
    from django.http import JsonResponse
    account = _account_or_none(request)
    if account is None:
        raise Http404
    journey = _find_journey(account, journey_id)
    return JsonResponse(_complete(account, journey.activities, [journey]))


@require_POST
@login_required
def journeys_complete(request):
    from django.http import JsonResponse
    account = _account_or_none(request)
    if account is None:
        raise Http404
    return JsonResponse(_complete(account, journeys_to_name=journeys.for_account(account)))


def _account_or_none(request):
    return getattr(request.user, "strava", None)


@login_required
def activity_list(request):
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")

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
         "total": account.activities.count()},
    )


@require_POST
@login_required
def activity_sync(request):
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
    full = request.POST.get("full") == "1"
    try:
        count = sync_activities(account, full=full)
    except strava.StravaError as exc:
        messages.error(request, f"Synchronisation interrompue : {exc}")
    else:
        if count:
            messages.success(request, f"{count} activité(s) importée(s) depuis Strava.")
        else:
            messages.info(request, "Rien de nouveau sur Strava.")
        _sync_photos_with_message(request, account)
    return redirect("activities:list")


def _sync_photos_with_message(request, account):
    """Importe un lot de photos et l'indique à l'utilisateur (une erreur ici n'est pas bloquante)."""
    try:
        photos, remaining = sync_photos(account)
    except strava.StravaError as exc:
        messages.error(request, f"Import des photos interrompu : {exc}")
        return
    if photos:
        messages.success(request, f"{photos} photo(s) importée(s).")
    elif not account.activities.filter(photo_count__gt=0).exists():
        messages.info(request, "Strava n'indique aucune photo sur tes activités.")
    if remaining:
        messages.info(request, f"{remaining} activité(s) avec photos restent à importer : "
                               "relance la synchronisation dans quelques minutes.")
    # Les noms de lieux des nouveaux périples arrivent ensuite en arrière-plan (page Périples)


@login_required
def activity_detail(request, strava_id):
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
    activity = get_object_or_404(Activity, strava_id=strava_id, account=account)
    route = polyline.decode(activity.summary_polyline) if activity.summary_polyline else []
    background = None
    if not activity.photos_synced_at:  # photos récupérées juste après l'affichage
        background = {"url": reverse("activities:detail_complete", args=[strava_id]), "pending": 1, "what": "photos"}
    return render(request, "activities/detail.html",
                  {"activity": activity, "route": route, "photos": activity.photos.all(), "background": background})


@login_required
def all_routes_map(request):
    """Carte qui superpose tous les parcours de l'utilisateur."""
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
    # « account » doit être chargé (sinon une requête par activité) ; les tracés partent encodés
    # (format Strava, 3 à 5 fois plus léger) et sont décodés par le navigateur, miniatures comprises.
    qs = account.activities.exclude(summary_polyline="").only(
        "account", "strava_id", "name", "sport_type", "start_date", "start_date_local", "distance", "summary_polyline"
    ).prefetch_related("photos")
    routes, counts = [], {}
    for a in qs:
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
    families = [
        {"key": key, "label": label, "color": color, "count": counts[key]}
        for key, (label, color) in sports.FAMILIES.items() if key in counts
    ]
    total_km = round(sum(r["km"] for r in routes))
    trips = [
        {"id": j.id, "title": j.name, "url": reverse("activities:journey", args=[j.id]),
         "dates": _date_range(j), "stages": [a.strava_id for a in j.activities],
         "colors": _stage_colors(len(j.legs)), "transfers": j.transfers,
         "days": j.days, "km": round(j.distance_km)}
        for j in journeys.for_account(account, include_hidden=False)
    ]
    routes_data = [{k: r[k] for k in ("id", "name", "sport", "family", "color", "date", "day", "km", "poly")} for r in routes]
    return render(request, "activities/map.html",
                  {"routes": routes, "routes_data": routes_data, "families": families, "total_km": total_km,
                   "journeys": trips})


@login_required
def poster(request):
    """Affiche : image à imprimer (PNG haute résolution) composée dans le navigateur.

    Le serveur fournit les tracés encodés et les périples ; le choix de la source, le format,
    les couleurs, le cadre et le cadrage sont faits et dessinés par static/activities/poster.js.
    """
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
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
    initial = {}
    if request.method == "POST":
        known = {r["id"] for r in routes}
        chosen = [i for i in _selected_ids(request) if i in known]
        if chosen:
            initial = {"source": "selection", "id": chosen,
                       "title": request.POST.get("title", "").strip()[:120]}
    for param, key, known in (("periple", "journey", {t["id"] for t in trips}),
                              ("activite", "activity", {r["id"] for r in routes})):
        value = request.GET.get(param, "")
        if value.isdigit() and int(value) in known:
            initial = {"source": key, "id": int(value)}
    return render(request, "activities/poster.html", {
        "routes": routes, "families": families, "journeys": trips, "initial": initial,
        "athlete": account.firstname,
    })


POSTER_PROFILE_POINTS = 40


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


@login_required
def journey_list(request):
    """Périples : activités enchaînées sur plusieurs jours, détectées automatiquement."""
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
    # Miniature : chaque étape dans sa couleur, dans le même cadre que le tracé complet
    found = journeys.for_account(account)
    items = [{"journey": j, "dates": _date_range(j), "thumb": _journey_thumb(j)} for j in found if not j.hidden]
    hidden = [{"journey": j, "dates": _date_range(j)} for j in found if j.hidden]
    return render(request, "activities/journeys.html",
                  {"items": items, "hidden": hidden, "background": _names_background(found)})


def _find_journey(account, journey_id, budget=None):
    journey = next((j for j in journeys.for_account(account, budget) if j.id == journey_id), None)
    if journey is None:
        raise Http404("Périple introuvable")
    return journey


@login_required
def journey_detail(request, journey_id):
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
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
            messages.success(request, "Périple masqué : il n'apparaît plus dans la liste, la carte ni les livres."
                             if note.hidden else "Périple rétabli.")
        note.save()
        if action == "hide":
            return redirect("activities:journeys")
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
    return {int(x) for x in request.POST.getlist(name) if x.isdigit()}


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


@login_required
def book_builder(request):
    """Livre : mes livres enregistrés, et création d'un nouveau livre à partir de périples."""
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
    found = journeys.for_account(account)
    if request.method == "POST" and request.POST.getlist("a"):
        return _book_from_selection(request, account)
    if request.method == "POST":
        wanted = {int(x) for x in request.POST.getlist("j") if x.isdigit()}
        chosen = sorted((j for j in found if j.id in wanted), key=lambda j: j.legs[0].start_at)
        if not chosen:
            messages.error(request, "Choisis au moins un périple pour créer le livre.")
            return redirect("activities:book")
        title = request.POST.get("title", "").strip()[:200]
        new = Book.objects.create(account=account, journey_ids=[j.id for j in chosen],
                                  title=title or (chosen[0].name if len(chosen) == 1 else "Mes périples"))
        _, problem = book.prepare(account, chosen)
        if problem:
            messages.info(request, problem)
        # Les photos sont récupérées ensuite par la page du livre, en arrière-plan (book_fetch_photos)
        return redirect("activities:book_detail", book_id=new.pk)
    preselected = {int(x) for x in request.GET.getlist("j") if x.isdigit()}
    items = [{"journey": j, "dates": _date_range(j), "thumb": _journey_thumb(j), "checked": j.id in preselected}
             for j in found if not j.hidden or j.id in preselected]
    names = {j.id: j.name for j in found}
    books = [{"book": b, "names": [f"Sélection sur la carte : {len(b.activity_ids)} activité{'s' if len(b.activity_ids) > 1 else ''}"]
              if b.activity_ids else [names.get(i, "Périple introuvable") for i in b.journey_ids]}
             for b in account.books.all()]
    return render(request, "activities/book_builder.html",
                  {"items": items, "books": books, "background": _names_background(found)})


MAX_BOOK_ACTIVITIES = 150  # une page par étape : au-delà, le livre devient ingérable


def _selected_ids(request):
    return list(dict.fromkeys(int(x) for x in request.POST.getlist("a") if x.isdigit()))


def _book_from_selection(request, account):
    """Livre à partir des activités d'une zone de la carte : un seul chapitre, dans l'ordre chronologique."""
    ids = _selected_ids(request)
    if len(ids) > MAX_BOOK_ACTIVITIES:
        messages.error(request, f"Trop d'activités pour un livre ({len(ids)}) : choisis-en au plus "
                                f"{MAX_BOOK_ACTIVITIES} (zone plus petite, ou décoche-en).")
        return redirect("activities:map")
    journey = journeys.from_activities(account, ids)
    if journey is None:
        messages.error(request, "Aucune des activités choisies n'a de tracé : impossible de créer le livre.")
        return redirect("activities:map")
    title = request.POST.get("title", "").strip()[:200]
    new = Book.objects.create(account=account, activity_ids=[a.strava_id for a in journey.activities],
                              title=title or journey.place_name or "Ma sélection")
    _, problem = book.prepare(account, [journey])
    if problem:
        messages.info(request, problem)
    return redirect("activities:book_detail", book_id=new.pk)


def _journey_thumb(j):
    coords = [polyline.decode(a.summary_polyline) for a in j.activities]
    flat = [p for c in coords for p in c]
    return [{"d": geo.svg_path(c, frame=flat), "color": col} for c, col in zip(coords, _stage_colors(len(coords)))]


def _book_or_404(request, book_id):
    account = _account_or_none(request)
    if account is None:
        raise Http404
    return get_object_or_404(Book, pk=book_id, account=account), account


def _book_journeys(account, book_obj):
    if book_obj.activity_ids:
        j = journeys.from_activities(account, book_obj.activity_ids,
                                     book_obj.selection_title, book_obj.selection_story)
        return [j] if j else []
    found = {j.id: j for j in journeys.for_account(account)}
    return [found[i] for i in book_obj.journey_ids if i in found]


@login_required
def book_detail(request, book_id):
    """Le livre : pages A4 à l'écran comme à l'impression, avec un mode édition."""
    book_obj, account = _book_or_404(request, book_id)
    chosen = _book_journeys(account, book_obj)
    if not chosen:
        messages.error(request, "Les périples de ce livre n'existent plus (activités supprimées ?).")
        return redirect("activities:book")
    # Photos Strava et noms de lieux manquants : récupérés ensuite par la page (book_fetch_photos)
    photos_pending = (sum(1 for j in chosen for a in j.activities if not a.photos_synced_at)
                      + len(places.missing_points(chosen)))
    stage_notes = {s.activity_id: s for s in book_obj.stages.all()}
    pool = book.photo_pool(chosen, book_obj.uploads.all())
    chapters = [book.build(j, _stage_colors(len(j.legs)), _date_range(j), book_obj, stage_notes, pool)
                for j in chosen]

    first, last = chosen[0].first_day, chosen[-1].last_day
    period = (formats.date_format(first, "F Y") if (first.year, first.month) == (last.year, last.month)
              else f"{formats.date_format(first, 'F Y')} – {formats.date_format(last, 'F Y')}")
    totals = {
        "km": round(sum(j.distance_km for j in chosen)), "elevation": sum(j.elevation for j in chosen),
        "days": sum(j.days for j in chosen), "stages": sum(len(j.legs) for j in chosen),
    }
    cover_photo = pool.get(book_obj.cover_photo)
    if book_obj.cover == Book.COVER_PHOTO and cover_photo is None and pool:
        cover_photo = next(iter(pool.values()))

    maps = [{"id": f"j{c['journey'].id}", "stages": [{"coords": s["coords"], "color": s["color"]} for s in c["stages"]],
             "transfers": [[t["from"], t["to"]] for t in c["journey"].transfer_list]}
            for c in chapters]
    maps += [{"id": f"s{s['id']}", "stages": [{"coords": s["coords"], "color": s["color"]}]}
             for c in chapters for s in c["stages"]]
    maps.append({"id": "cover", "stages": [st for m in maps[:len(chapters)] for st in m["stages"]]})
    # Pour l'édition : toutes les photos du livre, et la sélection de chaque étape
    stages_info = [{"id": s["id"], "n": s["n"], "label": s["label"], "chapter": ci, "photos": s["photo_keys"]}
                   for ci, c in enumerate(chapters) for s in c["stages"]]
    return render(request, "activities/book.html", {
        "photos_pending": photos_pending,
        "book": book_obj, "title": book_obj.title or "Mes périples", "chapters": chapters, "totals": totals,
        "maps": maps, "period": period, "cover_photo": cover_photo,
        "subtitle": book_obj.subtitle or f"{period[:1].upper()}{period[1:]}"
                    + (f", {account.firstname} {account.lastname}".rstrip() if account.firstname else ""),
        "opt": {k: book_obj.option(k) for k in Book.DEFAULT_OPTIONS},
        "pool": list(pool.values()), "stages_info": stages_info, "has_photos": bool(pool),
        "edit": request.GET.get("edition") == "1",
    })


def _json_body(request):
    import json
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


def _book_photo_keys(account, book_obj):
    return set(book.photo_pool(_book_journeys(account, book_obj), book_obj.uploads.all()))


def _book_activity_ids(account, book_obj):
    return {a.strava_id for j in _book_journeys(account, book_obj) for a in j.activities}


@require_POST
@login_required
def book_update(request, book_id):
    """Enregistre une modification faite dans le mode édition du livre (JSON)."""
    from django.http import JsonResponse

    book_obj, account = _book_or_404(request, book_id)
    data = _json_body(request)
    field, value = data.get("field"), data.get("value")
    text = (value or "").strip() if isinstance(value, str) else ""

    if field in ("book.title", "book.subtitle"):
        setattr(book_obj, field.split(".")[1], text[:300])
        book_obj.save()
    elif field in ("journey.title", "journey.story"):
        journey_id = int(data.get("journey", -1))
        if book_obj.activity_ids and journey_id == journeys.SELECTION_KEY:
            if field == "journey.title":
                book_obj.selection_title = text[:200]
            else:
                book_obj.selection_story = text
            book_obj.save()
        elif journey_id not in book_obj.journey_ids:
            return JsonResponse({"ok": False, "error": "Périple inconnu"}, status=400)
        else:
            note, _ = JourneyNote.objects.get_or_create(account=account, journey_id=journey_id)
            setattr(note, field.split(".")[1], text[:200] if field == "journey.title" else text)
            note.save()
    elif field in ("stage.title", "stage.text", "stage.reset_text", "stage.photos"):
        activity_id = int(data.get("activity", 0))
        if activity_id not in _book_activity_ids(account, book_obj):
            return JsonResponse({"ok": False, "error": "Étape inconnue"}, status=400)
        stage, _ = BookStage.objects.get_or_create(book=book_obj, activity_id=activity_id)
        if field == "stage.title":
            stage.title = text[:200]
        elif field == "stage.text":
            stage.text, stage.text_edited = text, True
        elif field == "stage.reset_text":
            stage.text, stage.text_edited = "", False
        else:
            if not isinstance(value, list):
                return JsonResponse({"ok": False, "error": "Liste de photos attendue"}, status=400)
            allowed = _book_photo_keys(account, book_obj)
            stage.photos = [{"key": str(p.get("key")), "caption": str(p.get("caption", ""))[:300]}
                            for p in value if isinstance(p, dict) and p.get("key") in allowed]
        stage.save()
    elif field == "cover":
        if value in (Book.COVER_MAP, Book.COVER_PHOTO):
            book_obj.cover = value
        book_obj.cover_photo = str(data.get("photo", book_obj.cover_photo))[:30]
        book_obj.save()
    elif field == "options":
        if not isinstance(value, dict):
            return JsonResponse({"ok": False, "error": "Options attendues"}, status=400)
        options = dict(book_obj.options)
        for key, default in Book.DEFAULT_OPTIONS.items():
            if key in value and isinstance(value[key], type(default)):
                if key == "imagery" and value[key] not in Book.IMAGERY:
                    continue
                options[key] = value[key]
        book_obj.options = options
        book_obj.save()
    elif field == "upload.delete":
        upload = get_object_or_404(BookPhoto, pk=int(data.get("upload", 0)), book=book_obj)
        for stage in book_obj.stages.exclude(photos=None):
            kept = [p for p in stage.photos if p.get("key") != upload.key]
            if len(kept) != len(stage.photos):
                stage.photos = kept
                stage.save(update_fields=["photos"])
        upload.image.delete(save=False)
        upload.delete()
    else:
        return JsonResponse({"ok": False, "error": "Champ inconnu"}, status=400)
    Book.objects.filter(pk=book_obj.pk).update(updated_at=timezone.now())
    return JsonResponse({"ok": True})


MAX_UPLOAD_SIDE = 2400


BOOK_PHOTO_BATCH = 8  # étapes vérifiées par requête du navigateur : la page reste réactive


@require_POST
@login_required
def book_fetch_photos(request, book_id):
    """Récupère sur Strava les photos manquantes des étapes du livre, par petits lots.

    Appelée en boucle par la page du livre tant qu'il reste des étapes à vérifier.
    """
    from django.http import JsonResponse

    book_obj, account = _book_or_404(request, book_id)
    chosen = _book_journeys(account, book_obj)
    result = _complete(account, [a for j in chosen for a in j.activities], chosen, photo_batch=BOOK_PHOTO_BATCH)
    return JsonResponse({"imported": result["found"], "remaining": result["remaining"], "error": result["error"],
                         "rate_limited": result["retry"]})


@require_POST
@login_required
def book_upload(request, book_id):
    """Ajoute des photos personnelles à une étape du livre (redimensionnées, orientation corrigée)."""
    from io import BytesIO

    from django.core.files.base import ContentFile
    from django.http import JsonResponse
    from PIL import Image, ImageOps, UnidentifiedImageError

    book_obj, account = _book_or_404(request, book_id)
    raw = request.POST.get("activity", "")
    activity_id = int(raw) if raw.isdigit() else None  # sans étape : la photo va dans le bac
    if activity_id is not None and activity_id not in _book_activity_ids(account, book_obj):
        return JsonResponse({"ok": False, "error": "Étape inconnue"}, status=400)
    added, rejected = [], []
    for f in request.FILES.getlist("photos")[:30]:
        try:
            img = ImageOps.exif_transpose(Image.open(f))
            img = img.convert("RGB")
            img.thumbnail((MAX_UPLOAD_SIDE, MAX_UPLOAD_SIDE))
            out = BytesIO()
            img.save(out, "JPEG", quality=85, optimize=True)
        except (UnidentifiedImageError, OSError):
            rejected.append(f.name)
            continue
        upload = BookPhoto(book=book_obj, activity_id=activity_id)
        upload.image.save(f"{secrets.token_hex(8)}.jpg", ContentFile(out.getvalue()), save=True)
        added.append(upload.key)
    stage = BookStage.objects.filter(book=book_obj, activity_id=activity_id).first() if activity_id else None
    if stage and stage.photos is not None:  # sélection personnalisée : on y ajoute les nouvelles photos
        stage.photos += [{"key": k, "caption": ""} for k in added]
        stage.save()
    Book.objects.filter(pk=book_obj.pk).update(updated_at=timezone.now())
    return JsonResponse({"ok": True, "added": len(added), "keys": added, "rejected": rejected})


@require_POST
@login_required
def book_delete(request, book_id):
    book_obj, _ = _book_or_404(request, book_id)
    for upload in book_obj.uploads.all():
        upload.image.delete(save=False)
    book_obj.delete()
    messages.info(request, "Livre supprimé.")
    return redirect("activities:book")
