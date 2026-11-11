"""Connexion Strava (OAuth, webhook), déconnexion et import de fichiers GPX, FIT, TCX."""

import json
import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .. import strava, webhooks
from ..models import Activity, StravaAccount
from ..sync import sync_activities
from .common import _account_or_none, _sync_photos_with_message


SESSION_STATE_KEY = "strava_oauth_state"


def home(request):
    if request.user.is_authenticated and hasattr(request.user, "strava"):
        return redirect("activities:map")
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
        # Retour Strava reçu deux fois (rechargement, page restaurée, double redirection) : la
        # première fois a déjà connecté le compte, inutile d'afficher une erreur
        if request.user.is_authenticated and hasattr(request.user, "strava"):
            return redirect("activities:home")
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
            result = sync_activities(account)
            messages.success(request, f"Bienvenue ! {result.created} sortie{'s' if result.created > 1 else ''} "
                                      f"importée{'s' if result.created > 1 else ''} depuis Strava.")
            _sync_photos_with_message(request, account)
        except strava.StravaError as exc:
            messages.error(request, f"Connecté, mais l'import des activités a échoué : {exc}")
    return redirect("activities:map")


@require_POST
def strava_logout(request):
    logout(request)
    return redirect("activities:home")


@require_POST
@login_required
def strava_disconnect(request):
    """Révoque l'accès Strava et supprime les jetons stockés."""
    account = getattr(request.user, "strava", None)
    local = bool(account and account.is_local)
    if account:
        if not local:
            strava.deauthorize(account)
        account.delete()
    user = request.user
    logout(request)
    if local:
        user.delete()  # un compte sans Strava n'a rien d'autre : on efface tout
        messages.info(request, "Tes activités et tes créations ont été supprimées.")
    else:
        messages.info(request, "Ton compte Strava a été déconnecté du site.")
    return redirect("activities:home")


@csrf_exempt
@require_http_methods(["GET", "POST"])
def strava_webhook(request):
    """Webhooks Strava (activities/webhooks.py).

    GET : Strava vérifie l'adresse à la création de l'abonnement (jeton + challenge à renvoyer).
    POST : un événement ; réponse immédiate (Strava attend moins de 2 s), traitement juste après.
    """
    if request.method == "GET":
        if (request.GET.get("hub.mode") != "subscribe"
                or not secrets.compare_digest(request.GET.get("hub.verify_token", ""), settings.STRAVA_WEBHOOK_VERIFY_TOKEN)):
            return JsonResponse({"error": "jeton invalide"}, status=403)
        return JsonResponse({"hub.challenge": request.GET.get("hub.challenge", "")})
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": "JSON invalide"}, status=400)
    event = webhooks.record(payload) if isinstance(payload, dict) else None
    if event is not None:
        webhooks.dispatch(event)
    return JsonResponse({"ok": True})


# ---------- Import de fichiers (GPX, FIT, TCX, ZIP) ----------
# Pour tout le monde, avec ou sans Strava : sans compte, un compte « sans Strava » est créé et lié
# au navigateur (cookie de session d'un an). Les fichiers viennent de l'utilisateur, pas de l'API Strava.

MAX_IMPORT_FILES = 300


MAX_IMPORT_BYTES = 200 * 1024 * 1024


def _local_account(request):
    """Compte de l'utilisateur connecté, ou nouveau compte sans Strava pour un visiteur."""
    account = _account_or_none(request)
    if account is not None:
        return account
    User = get_user_model()
    user = User.objects.create(username=f"local_{secrets.token_hex(8)}")
    user.set_unusable_password()
    user.save(update_fields=["password"])
    account = StravaAccount.objects.create(
        user=user, athlete_id=-user.pk, is_local=True, access_token="", refresh_token="",
        expires_at=timezone.now(),
    )
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return account


def _new_file_id():
    # Négatif (pas de collision avec Strava) et sous 2^53 : le navigateur lit ces identifiants sans arrondi
    return -secrets.randbelow(2 ** 52) - 1


def import_files(request):
    """Dépôt de fichiers d'activité : la page, puis l'import (plusieurs fichiers ou une archive)."""
    from .. import tracks

    if request.method != "POST":
        return render(request, "activities/import.html", {"account": _account_or_none(request)})
    uploads = request.FILES.getlist("files")[:MAX_IMPORT_FILES]
    if not uploads:
        messages.error(request, "Choisis au moins un fichier.")
        return redirect("activities:import")
    if sum(f.size for f in uploads) > MAX_IMPORT_BYTES:
        messages.error(request, "Fichiers trop lourds (200 Mo au plus par envoi) : envoie-les en plusieurs fois.")
        return redirect("activities:import")
    found, skipped = tracks.read_files([(f.name, f.read()) for f in uploads])
    if not found:
        reason = f" ({skipped[0][1]})" if skipped else ""
        messages.error(request, f"Aucune activité avec un tracé GPS dans ces fichiers{reason}.")
        return redirect("activities:import")

    account = _local_account(request)
    # Une même sortie déposée deux fois (ou déjà venue de Strava) n'est pas recréée
    known = {(a.start_date.replace(microsecond=0), round(a.distance / 100)) for a in account.activities.only("start_date", "distance")}
    now, created = timezone.now(), 0
    for fields in found:
        key = (fields["start_date"].replace(microsecond=0), round(fields["distance"] / 100))
        if key in known:
            continue
        known.add(key)
        # Pas de photos ni de détails à demander à Strava : tout vient du fichier
        Activity.objects.create(account=account, strava_id=_new_file_id(), photos_synced_at=now,
                                details_fetched_at=now, **fields)
        created += 1
    doubles = len(found) - created
    text = f"{created} activité{'s' if created > 1 else ''} importée{'s' if created > 1 else ''}"
    if doubles:
        text += f", {doubles} déjà présente{'s' if doubles > 1 else ''}"
    if skipped:
        text += f", {len(skipped)} fichier{'s' if len(skipped) > 1 else ''} ignoré{'s' if len(skipped) > 1 else ''}"
    messages.success(request, text + ".")
    return redirect("activities:map" if created else "activities:list")
