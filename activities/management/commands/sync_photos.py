"""Importe TOUTES les photos Strava en attente, en patientant quand le quota est atteint.

Sur le site, chaque synchronisation ne traite que PHOTO_BATCH activités (quota Strava).
Cette commande enchaîne les lots jusqu'au bout ; quand Strava répond 429, elle attend
la fenêtre de 15 minutes suivante puis reprend là où elle s'était arrêtée.

    python manage.py sync_photos                    # tous les comptes
    python manage.py sync_photos --athlete 22479587 # un seul compte
"""

import time
from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError

from activities import strava
from activities.models import StravaAccount
from activities.sync import PHOTO_BATCH, sync_photos


def seconds_until_next_window(now=None):
    """Strava remet ses compteurs à zéro à :00, :15, :30 et :45 (+ petite marge)."""
    now = now or datetime.now()
    next_quarter = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0) + timedelta(minutes=15)
    return (next_quarter - now).total_seconds() + 15


class Command(BaseCommand):
    help = "Importe toutes les photos Strava en attente, en attendant quand le quota d'API est atteint."

    def add_arguments(self, parser):
        parser.add_argument("--athlete", type=int, help="Identifiant Strava de l'athlète (défaut : tous les comptes)")
        parser.add_argument("--max-waits", type=int, default=20,
                            help="Nombre maximum d'attentes de quota avant d'abandonner (défaut : 20)")

    def handle(self, *args, **options):
        accounts = StravaAccount.objects.all()
        if options["athlete"]:
            accounts = accounts.filter(athlete_id=options["athlete"])
            if not accounts.exists():
                raise CommandError(f"Aucun compte pour l'athlète {options['athlete']}.")

        for account in accounts:
            self.stdout.write(self.style.MIGRATE_HEADING(f"{account}"))
            total, waits = 0, 0
            while True:
                try:
                    imported, remaining = sync_photos(account, limit=PHOTO_BATCH)
                except strava.RateLimited:
                    waits += 1
                    if waits > options["max_waits"]:
                        raise CommandError("Quota Strava atteint trop souvent, relance la commande plus tard "
                                           "(les photos déjà importées sont conservées).")
                    delay = seconds_until_next_window()
                    pending = account.activities.filter(photo_count__gt=0, photos_synced_at__isnull=True).count()
                    self.stdout.write(f"  Quota Strava atteint ({pending} activité(s) restantes), "
                                      f"reprise dans {int(delay // 60)} min {int(delay % 60)} s…")
                    time.sleep(delay)
                    continue
                except strava.StravaError as exc:
                    raise CommandError(f"Erreur Strava : {exc}")

                total += imported
                self.stdout.write(f"  +{imported} photo(s), {remaining} activité(s) restantes")
                if not remaining:
                    break
            self.stdout.write(self.style.SUCCESS(f"  Terminé : {total} photo(s) importée(s)."))
