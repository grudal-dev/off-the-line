"""Gère l'abonnement aux webhooks Strava et reprend les événements en attente.

    python manage.py strava_webhook subscribe [--url https://…/strava/webhook/]
    python manage.py strava_webhook show
    python manage.py strava_webhook unsubscribe <id>
    python manage.py strava_webhook process
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from activities import strava, webhooks
from activities.models import WebhookEvent


class Command(BaseCommand):
    help = "Abonnement aux webhooks Strava (subscribe, show, unsubscribe) et reprise des événements (process)."

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest="action", required=True)
        s = sub.add_parser("subscribe", help="Créer l'abonnement (un seul par application)")
        s.add_argument("--url", default="", help="Adresse publique du site + /strava/webhook/")
        sub.add_parser("show", help="Afficher l'abonnement en cours")
        u = sub.add_parser("unsubscribe", help="Supprimer un abonnement")
        u.add_argument("subscription_id")
        sub.add_parser("process", help="Traiter les événements en attente")

    def handle(self, *args, action, **options):
        try:
            getattr(self, f"do_{action}")(**options)
        except strava.StravaError as exc:
            raise CommandError(str(exc)) from exc

    def do_subscribe(self, url="", **_):
        url = url or settings.STRAVA_WEBHOOK_URL
        if not url:
            raise CommandError("Adresse inconnue : passe --url https://ton-site/strava/webhook/ "
                               "(le site doit être en ligne, Strava l'appelle aussitôt).")
        data = webhooks.subscribe(url)
        self.stdout.write(self.style.SUCCESS(f"Abonnement créé : {data.get('id')}"))
        self.stdout.write(f"Ajoute STRAVA_WEBHOOK_SUBSCRIPTION_ID={data.get('id')} aux variables d'environnement "
                          "pour n'accepter que les événements de cet abonnement.")

    def do_show(self, **_):
        subs = webhooks.subscriptions() or []
        if not subs:
            self.stdout.write("Aucun abonnement.")
        for s in subs:
            self.stdout.write(f"{s.get('id')} → {s.get('callback_url')} (créé le {s.get('created_at')})")
        pending = WebhookEvent.objects.filter(processed_at__isnull=True).count()
        self.stdout.write(f"Événements en attente : {pending}")

    def do_unsubscribe(self, subscription_id, **_):
        webhooks.unsubscribe(subscription_id)
        self.stdout.write(self.style.SUCCESS(f"Abonnement {subscription_id} supprimé."))

    def do_process(self, **_):
        done = webhooks.process_pending()
        left = WebhookEvent.objects.filter(processed_at__isnull=True).count()
        self.stdout.write(f"{done} événement(s) traité(s), {left} en attente.")
