"""Petit client pour l'API Strava : OAuth, renouvellement des jetons, activités.

Doc : https://developers.strava.com/docs/authentication/
"""

from datetime import datetime, timezone as dt_timezone
from urllib.parse import urlencode

import requests
from django.conf import settings

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
DEAUTHORIZE_URL = "https://www.strava.com/oauth/deauthorize"
API_BASE = "https://www.strava.com/api/v3"

# read : profil public ; activity:read_all : toutes les activités, y compris privées
SCOPES = "read,activity:read_all"
TIMEOUT = 10


class StravaError(Exception):
    pass


def authorize_url(state):
    params = {
        "client_id": settings.STRAVA_CLIENT_ID,
        "redirect_uri": settings.STRAVA_REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPES,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _post_token(data):
    payload = {
        "client_id": settings.STRAVA_CLIENT_ID,
        "client_secret": settings.STRAVA_CLIENT_SECRET,
        **data,
    }
    try:
        response = requests.post(TOKEN_URL, data=payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise StravaError(f"Strava injoignable : {exc}") from exc
    if response.status_code != 200:
        raise StravaError(f"Échange de jeton refusé ({response.status_code}) : {response.text}")
    return response.json()


def exchange_code(code):
    """Échange le code reçu au retour de Strava contre des jetons + infos athlète."""
    return _post_token({"code": code, "grant_type": "authorization_code"})


def refresh(refresh_token):
    return _post_token({"refresh_token": refresh_token, "grant_type": "refresh_token"})


def expires_at_to_datetime(timestamp):
    return datetime.fromtimestamp(int(timestamp), tz=dt_timezone.utc)


def get_valid_access_token(account):
    """Renvoie un jeton d'accès valide, en le renouvelant si besoin."""
    if account.is_expired:
        data = refresh(account.refresh_token)
        account.access_token = data["access_token"]
        account.refresh_token = data["refresh_token"]
        account.expires_at = expires_at_to_datetime(data["expires_at"])
        account.save(update_fields=["access_token", "refresh_token", "expires_at", "updated_at"])
    return account.access_token


def api_get(account, path, params=None):
    token = get_valid_access_token(account)
    try:
        response = requests.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StravaError(f"Strava injoignable : {exc}") from exc
    if response.status_code == 429:
        raise StravaError("Limite de requêtes Strava atteinte, réessaie dans quelques minutes.")
    if response.status_code != 200:
        raise StravaError(f"Erreur API Strava ({response.status_code}) : {response.text}")
    return response.json()


def list_activities(account, page=1, per_page=30):
    return api_get(account, "/athlete/activities", {"page": page, "per_page": per_page})


def activity_photos(account, activity_id, size=1024):
    """Photos d'une activité (point d'accès non documenté mais utilisé par les clients Strava)."""
    return api_get(account, f"/activities/{activity_id}/photos", {"size": size, "photo_sources": "true"})


def deauthorize(account):
    """Révoque l'accès côté Strava (best effort)."""
    try:
        requests.post(
            DEAUTHORIZE_URL,
            data={"access_token": get_valid_access_token(account)},
            timeout=TIMEOUT,
        )
    except (requests.RequestException, StravaError):
        pass
