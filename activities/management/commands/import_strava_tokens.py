"""Importe des jetons Strava obtenus à la main (réponse JSON de POST /oauth/token).

Utile quand l'autorisation a été faite hors du site (navigateur + curl / PowerShell) :
le compte est créé exactement comme le ferait la connexion Strava du site
(utilisateur « strava_<id> »), donc l'athlète pourra ensuite se connecter
normalement avec « Se connecter avec Strava ».

    python manage.py import_strava_tokens strava_papa.json --sync
"""

import json
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from activities import strava
from activities.models import StravaAccount
from activities.sync import sync_activities, sync_photos

REQUIRED_KEYS = ("access_token", "refresh_token", "expires_at", "athlete")


def read_json(path):
    """Lit le fichier quel que soit son encodage (PowerShell 5 écrit en UTF-16 par défaut)."""
    raw = Path(path).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-8-sig")
    return json.loads(text)


def import_tokens(data, scope=strava.SCOPES):
    """Crée ou met à jour l'utilisateur et son StravaAccount. Renvoie (compte, créé)."""
    missing = [k for k in REQUIRED_KEYS if not data.get(k)]
    if missing:
        raise ValueError(f"Champs manquants dans la réponse Strava : {', '.join(missing)}")
    athlete = data["athlete"]
    if not athlete.get("id"):
        raise ValueError("Identifiant d'athlète manquant (athlete.id).")

    User = get_user_model()
    with transaction.atomic():
        user, _ = User.objects.get_or_create(
            username=f"strava_{athlete['id']}",
            defaults={"first_name": athlete.get("firstname", "") or "",
                      "last_name": athlete.get("lastname", "") or ""},
        )
        account, created = StravaAccount.objects.update_or_create(
            athlete_id=athlete["id"],
            defaults={
                "user": user,
                "firstname": athlete.get("firstname", "") or "",
                "lastname": athlete.get("lastname", "") or "",
                "profile_picture": athlete.get("profile", "") or "",
                "access_token": data["access_token"],
                "refresh_token": data["refresh_token"],
                "expires_at": strava.expires_at_to_datetime(data["expires_at"]),
                "scope": scope,
            },
        )
    return account, created


class Command(BaseCommand):
    help = "Importe la réponse JSON de l'échange de jeton Strava (POST /oauth/token) dans la base."

    def add_arguments(self, parser):
        parser.add_argument("fichier", help="Fichier JSON contenant access_token, refresh_token, expires_at, athlete")
        parser.add_argument("--scope", default=strava.SCOPES,
                            help=f"Autorisations accordées lors de l'autorisation (défaut : {strava.SCOPES})")
        parser.add_argument("--sync", action="store_true",
                            help="Vérifie les jetons et importe les activités (et un lot de photos) tout de suite")

    def handle(self, *args, **options):
        try:
            data = read_json(options["fichier"])
        except FileNotFoundError:
            raise CommandError(f"Fichier introuvable : {options['fichier']}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommandError(f"Fichier JSON illisible : {exc}")

        try:
            account, created = import_tokens(data, scope=options["scope"])
        except ValueError as exc:
            raise CommandError(str(exc))

        verb = "créé" if created else "mis à jour"
        self.stdout.write(self.style.SUCCESS(
            f"Compte {verb} : {account} → utilisateur « {account.user.username} »"
        ))

        if options["sync"]:
            try:
                count = sync_activities(account)
                photos, remaining = sync_photos(account)
            except strava.StravaError as exc:
                raise CommandError(f"Jetons importés, mais l'appel à Strava a échoué : {exc}")
            self.stdout.write(self.style.SUCCESS(f"{count} activité(s) importée(s), {photos} photo(s)."))
            if remaining:
                self.stdout.write(f"{remaining} activité(s) avec photos restent à traiter : "
                                  "relance « Synchroniser » sur le site plus tard (quota Strava).")
