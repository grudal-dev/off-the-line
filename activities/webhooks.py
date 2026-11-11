"""Webhooks Strava : être prévenu des activités créées, modifiées ou supprimées, et des accès révoqués.

Doc : https://developers.strava.com/docs/webhooks/

- Accès révoqué (athlete / update / authorized = "false") : toutes les données de l'athlète sont
  supprimées, comme pour « Déconnecter Strava » (exigé par le contrat API).
- Activité supprimée : retirée du site.
- Activité modifiée : nouveau titre ou sport repris sans appel à l'API (ils sont dans l'événement).
- Activité créée : importée avec un appel à l'API (en arrière-plan, Strava attend une réponse rapide).
"""

import logging
import threading
from datetime import timedelta

import requests
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from . import journeys, strava
from .models import Activity, StravaAccount, WebhookEvent
from .sync import import_activity

log = logging.getLogger(__name__)

SUBSCRIPTIONS_URL = f"{strava.API_BASE}/push_subscriptions"
MAX_ATTEMPTS = 5
KEEP_DAYS = 30  # événements traités gardés un mois (suivi), puis effacés


def record(payload):
    """Enregistre un événement reçu. Renvoie None s'il est invalide ou d'un autre abonnement."""
    try:
        object_type = str(payload["object_type"])
        aspect_type = str(payload["aspect_type"])
        object_id, owner_id = int(payload["object_id"]), int(payload["owner_id"])
    except (KeyError, TypeError, ValueError):
        return None
    expected = settings.STRAVA_WEBHOOK_SUBSCRIPTION_ID
    if expected and str(payload.get("subscription_id")) != str(expected):
        return None
    return WebhookEvent.objects.create(object_type=object_type, aspect_type=aspect_type, object_id=object_id,
                                       owner_id=owner_id, payload=payload)


def run_later(event_id):
    """Traite l'événement après la réponse, dans un fil séparé (remplacé par un appel direct en test)."""
    def work():
        close_old_connections()
        try:
            if process(WebhookEvent.objects.get(pk=event_id)):
                process_pending(limit=20)  # sans tâche planifiée : on reprend les événements restés en attente
        except WebhookEvent.DoesNotExist:
            pass
        finally:
            close_old_connections()
    threading.Thread(target=work, daemon=True).start()


def dispatch(event):
    """Révocation et suppression tout de suite (base locale seulement) ; le reste en arrière-plan."""
    if needs_api(event):
        transaction.on_commit(lambda: run_later(event.pk))
    else:
        process(event)


def needs_api(event):
    return event.object_type == "activity" and event.aspect_type == "create"


def process(event):
    """Applique un événement. Les erreurs sont notées sur l'événement, qui sera repris plus tard."""
    event.attempts += 1
    try:
        _apply(event)
    except strava.RateLimited as exc:
        event.attempts -= 1  # pas une vraie erreur : on attendra la fenêtre suivante
        event.error = str(exc)
    except Exception as exc:  # noqa: BLE001 — on garde l'événement pour un nouvel essai
        log.exception("Webhook Strava %s", event)
        event.error = f"{type(exc).__name__}: {exc}"[:2000]
    else:
        event.processed_at = timezone.now()
        event.error = ""
    if event.attempts >= MAX_ATTEMPTS and not event.processed_at:
        event.processed_at = timezone.now()  # abandon : la prochaine synchronisation rattrapera
    event.save()
    return event.processed_at is not None and not event.error


def _apply(event):
    account = StravaAccount.objects.filter(athlete_id=event.owner_id, is_local=False).first()
    if account is None:
        return  # athlète inconnu (jamais connecté ou déjà supprimé) : rien à faire
    updates = event.payload.get("updates") or {}

    if event.object_type == "athlete":
        if str(updates.get("authorized", "")).lower() == "false":
            account.delete()  # activités, photos, périples, livres et affiches suivent (cascade)
        return

    if event.object_type != "activity":
        return
    activities = Activity.objects.filter(account=account, strava_id=event.object_id)
    if event.aspect_type == "delete":
        journeys.forget_activities(account, [event.object_id])
        account.removed_activities.filter(strava_id=event.object_id).delete()
    elif event.aspect_type == "update":
        changes = {}
        if "title" in updates:
            changes["name"] = str(updates["title"])[:255]
        if "type" in updates or "sport_type" in updates:
            changes["sport_type"] = str(updates.get("sport_type") or updates["type"])[:50]
        if changes:
            activities.update(**changes)
    elif event.aspect_type == "create":
        if account.removed_activities.filter(strava_id=event.object_id).exists():
            return  # retirée du site par l'utilisateur : on ne la réimporte pas
        import_activity(account, event.object_id)


def process_pending(limit=200):
    """Reprend les événements non traités (quota, erreur), puis efface les vieux événements traités."""
    done = 0
    for event in WebhookEvent.objects.filter(processed_at__isnull=True)[:limit]:
        if process(event):
            done += 1
    WebhookEvent.objects.filter(processed_at__lt=timezone.now() - timedelta(days=KEEP_DAYS)).delete()
    return done


# --- Abonnement (un seul par application Strava) ---------------------------------

def _credentials():
    return {"client_id": settings.STRAVA_CLIENT_ID, "client_secret": settings.STRAVA_CLIENT_SECRET}


def _call(method, url, **kwargs):
    try:
        response = requests.request(method, url, timeout=strava.TIMEOUT, **kwargs)
    except requests.RequestException as exc:
        raise strava.StravaError(f"Strava injoignable : {exc}") from exc
    if response.status_code >= 400:
        raise strava.StravaError(f"Strava a répondu {response.status_code} : {response.text}")
    return response.json() if response.content else None


def subscribe(callback_url):
    """Crée l'abonnement. Strava appelle aussitôt l'adresse (GET) pour vérifier le jeton."""
    return _call("POST", SUBSCRIPTIONS_URL, data={
        **_credentials(), "callback_url": callback_url, "verify_token": settings.STRAVA_WEBHOOK_VERIFY_TOKEN,
    })


def subscriptions():
    return _call("GET", SUBSCRIPTIONS_URL, params=_credentials())


def unsubscribe(subscription_id):
    return _call("DELETE", f"{SUBSCRIPTIONS_URL}/{subscription_id}", params=_credentials())
