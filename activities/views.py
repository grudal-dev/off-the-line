import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import polyline, strava
from .models import Activity, StravaAccount
from .sync import sync_activities

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
    page_obj = Paginator(qs, 30).get_page(request.GET.get("page"))
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
    return redirect("activities:list")


@login_required
def activity_detail(request, strava_id):
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
    activity = get_object_or_404(Activity, strava_id=strava_id, account=account)
    route = polyline.decode(activity.summary_polyline) if activity.summary_polyline else []
    return render(request, "activities/detail.html", {"activity": activity, "route": route})


@login_required
def all_routes_map(request):
    """Carte qui superpose tous les parcours de l'utilisateur."""
    account = _account_or_none(request)
    if account is None:
        return redirect("activities:home")
    qs = account.activities.exclude(summary_polyline="")
    sport = request.GET.get("sport", "")
    if sport:
        qs = qs.filter(sport_type=sport)
    routes = [
        {"id": a.strava_id, "name": a.name, "sport": a.sport_type,
         "date": a.start_date.strftime("%d/%m/%Y"), "km": a.distance_km,
         "coords": polyline.decode(a.summary_polyline)}
        for a in qs.only("strava_id", "name", "sport_type", "start_date", "distance", "summary_polyline")
    ]
    sports = account.activities.exclude(summary_polyline="").order_by("sport_type").values_list("sport_type", flat=True).distinct()
    return render(request, "activities/map.html", {"routes": routes, "sports": sports, "sport": sport})
