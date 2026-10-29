import secrets

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
from .sync import sync_activities, sync_photos

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
    # Nomme les nouveaux périples pendant qu'on y est (lieux mis en cache)
    journeys.for_account(account, places.Budget(lookups=60))


@login_required
def activity_detail(request, strava_id):
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
    activity = get_object_or_404(Activity, strava_id=strava_id, account=account)
    route = polyline.decode(activity.summary_polyline) if activity.summary_polyline else []
    return render(request, "activities/detail.html",
                  {"activity": activity, "route": route, "photos": activity.photos.all()})


@login_required
def all_routes_map(request):
    """Carte qui superpose tous les parcours de l'utilisateur."""
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
    qs = account.activities.exclude(summary_polyline="").only(
        "strava_id", "name", "sport_type", "start_date", "start_date_local", "distance", "summary_polyline"
    ).prefetch_related("photos")
    routes, counts = [], {}
    for a in qs:
        coords = polyline.decode(a.summary_polyline)
        fam = sports.family(a.sport_type)
        counts[fam] = counts.get(fam, 0) + 1
        routes.append({
            "id": a.strava_id, "name": a.name, "sport": sports.label(a.sport_type),
            "family": fam, "color": sports.FAMILIES[fam][1],
            "date": formats.date_format(a.start_date_local or a.start_date, "j F Y"),
            "km": a.distance_km, "coords": coords, "thumb": geo.svg_path(coords, max_points=60),
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
         "colors": _stage_colors(len(j.legs))}
        for j in journeys.for_account(account)
    ]
    return render(request, "activities/map.html",
                  {"routes": routes, "families": families, "total_km": total_km, "journeys": trips})


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
    items = [{"journey": j, "dates": _date_range(j), "thumb": _journey_thumb(j)}
             for j in journeys.for_account(account, places.Budget(lookups=30))]
    return render(request, "activities/journeys.html", {"items": items})


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
    if request.method == "POST":
        _find_journey(account, journey_id)  # 404 si ce n'est pas un périple de l'utilisateur
        JourneyNote.objects.update_or_create(
            account=account, journey_id=journey_id,
            defaults={"title": request.POST.get("title", "").strip()[:200],
                      "story": request.POST.get("story", "").strip()},
        )
        messages.success(request, "Périple mis à jour.")
        return redirect("activities:journey", journey_id=journey_id)
    journey = _find_journey(account, journey_id, places.Budget(lookups=20))
    colors = _stage_colors(len(journey.legs))
    stages = []
    for i, (leg, color) in enumerate(zip(journey.legs, colors)):
        a = leg.activity
        stages.append({
            "n": i + 1, "id": a.strava_id, "name": a.name, "color": color, "label": leg.label,
            "sport": sports.label(a.sport_type),
            "date": formats.date_format(a.start_date_local or a.start_date, "l j F"),
            "km": a.distance_km, "elevation": round(a.total_elevation_gain),
            "duration": a.duration_display, "night_after": i in journey.nights,
            "coords": polyline.decode(a.summary_polyline),
        })
    nights = sum(1 for s in stages if s["night_after"])
    return render(request, "activities/journey.html", {
        "journey": journey, "dates": _date_range(journey), "stages": stages, "nights": nights,
        "stages_json": [{k: s[k] for k in ("n", "id", "name", "label", "color", "coords", "night_after")}
                        for s in stages],
    })


@login_required
def book_builder(request):
    """Livre : mes livres enregistrés, et création d'un nouveau livre à partir de périples."""
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
    found = journeys.for_account(account, places.Budget(lookups=30))
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
        return redirect("activities:book_detail", book_id=new.pk)
    preselected = {int(x) for x in request.GET.getlist("j") if x.isdigit()}
    items = [{"journey": j, "dates": _date_range(j), "thumb": _journey_thumb(j), "checked": j.id in preselected}
             for j in found]
    names = {j.id: j.name for j in found}
    books = [{"book": b, "names": [names.get(i, "Périple introuvable") for i in b.journey_ids]}
             for b in account.books.all()]
    return render(request, "activities/book_builder.html", {"items": items, "books": books})


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
    stage_notes = {s.activity_id: s for s in book_obj.stages.all()}
    uploads = {}
    for u in book_obj.uploads.all():
        uploads.setdefault(u.activity_id, []).append(u)
    chapters = [book.build(j, _stage_colors(len(j.legs)), _date_range(j), book_obj, stage_notes, uploads)
                for j in chosen]

    first, last = chosen[0].first_day, chosen[-1].last_day
    period = (formats.date_format(first, "F Y") if (first.year, first.month) == (last.year, last.month)
              else f"{formats.date_format(first, 'F Y')} – {formats.date_format(last, 'F Y')}")
    totals = {
        "km": round(sum(j.distance_km for j in chosen)), "elevation": sum(j.elevation for j in chosen),
        "days": sum(j.days for j in chosen), "stages": sum(len(j.legs) for j in chosen),
    }
    all_photos = [p for c in chapters for s in c["stages"] for p in s["all_photos"]]
    cover_photo = next((p for p in all_photos if p["key"] == book_obj.cover_photo), None)
    if book_obj.cover == Book.COVER_PHOTO and cover_photo is None and all_photos:
        cover_photo = all_photos[0]

    maps = [{"id": f"j{c['journey'].id}", "stages": [{"coords": s["coords"], "color": s["color"]} for s in c["stages"]]}
            for c in chapters]
    maps += [{"id": f"s{s['id']}", "stages": [{"coords": s["coords"], "color": s["color"]}]}
             for c in chapters for s in c["stages"]]
    maps.append({"id": "cover", "stages": [st for m in maps[:len(chapters)] for st in m["stages"]]})
    editor = {
        s["id"]: {"label": s["label"], "default_label": s["default_label"], "photos": s["all_photos"]}
        for c in chapters for s in c["stages"]
    }
    return render(request, "activities/book.html", {
        "book": book_obj, "title": book_obj.title or "Mes périples", "chapters": chapters, "totals": totals,
        "maps": maps, "period": period, "cover_photo": cover_photo,
        "subtitle": book_obj.subtitle or f"{period[:1].upper()}{period[1:]}"
                    + (f", {account.firstname} {account.lastname}".rstrip() if account.firstname else ""),
        "opt": {k: book_obj.option(k) for k in Book.DEFAULT_OPTIONS},
        "editor": editor, "all_photos": all_photos,
        "edit": request.GET.get("edition") == "1",
    })


def _json_body(request):
    import json
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


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
        journey_id = int(data.get("journey", 0))
        if journey_id not in book_obj.journey_ids:
            return JsonResponse({"ok": False, "error": "Périple inconnu"}, status=400)
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
            stage.photos = [{"key": str(p.get("key", ""))[:30], "caption": str(p.get("caption", ""))[:300]}
                            for p in value if isinstance(p, dict)]
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
                options[key] = value[key]
        book_obj.options = options
        book_obj.save()
    elif field == "upload.delete":
        upload = get_object_or_404(BookPhoto, pk=int(data.get("upload", 0)), book=book_obj)
        upload.image.delete(save=False)
        upload.delete()
    else:
        return JsonResponse({"ok": False, "error": "Champ inconnu"}, status=400)
    Book.objects.filter(pk=book_obj.pk).update(updated_at=timezone.now())
    return JsonResponse({"ok": True})


MAX_UPLOAD_SIDE = 2400


@require_POST
@login_required
def book_upload(request, book_id):
    """Ajoute des photos personnelles à une étape du livre (redimensionnées, orientation corrigée)."""
    from io import BytesIO

    from django.core.files.base import ContentFile
    from django.http import JsonResponse
    from PIL import Image, ImageOps, UnidentifiedImageError

    book_obj, account = _book_or_404(request, book_id)
    try:
        activity_id = int(request.POST.get("activity", 0))
    except ValueError:
        activity_id = 0
    if activity_id not in _book_activity_ids(account, book_obj):
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
    stage = BookStage.objects.filter(book=book_obj, activity_id=activity_id).first()
    if stage and stage.photos is not None:  # sélection personnalisée : on y ajoute les nouvelles photos
        stage.photos += [{"key": k, "caption": ""} for k in added]
        stage.save()
    Book.objects.filter(pk=book_obj.pk).update(updated_at=timezone.now())
    return JsonResponse({"ok": True, "added": len(added), "rejected": rejected})


@require_POST
@login_required
def book_delete(request, book_id):
    book_obj, _ = _book_or_404(request, book_id)
    for upload in book_obj.uploads.all():
        upload.image.delete(save=False)
    book_obj.delete()
    messages.info(request, "Livre supprimé.")
    return redirect("activities:book")
