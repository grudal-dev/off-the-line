import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import strava, webhooks
from .models import Activity, Poster, StravaAccount, WebhookEvent

POLY = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


@override_settings(STRAVA_WEBHOOK_VERIFY_TOKEN="secret-token", STRAVA_WEBHOOK_SUBSCRIPTION_ID="")
class WebhookTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create(username="strava_42")
        self.account = StravaAccount.objects.create(user=user, athlete_id=42, access_token="tok", refresh_token="ref",
                                                    expires_at=timezone.now() + timedelta(hours=1))
        self.activity = Activity.objects.create(account=self.account, strava_id=500, name="Matin", sport_type="Ride",
                                                start_date=timezone.now(), distance=10000, summary_polyline=POLY)
        self.url = reverse("activities:strava_webhook")
        # Traitement en arrière-plan : appel direct dans les tests
        patcher = mock.patch.object(webhooks, "run_later", side_effect=lambda pk: webhooks.process(WebhookEvent.objects.get(pk=pk)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, **event):
        payload = {"object_type": "activity", "aspect_type": "create", "object_id": 500, "owner_id": 42,
                   "subscription_id": 1, "event_time": 1, "updates": {}, **event}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, json.dumps(payload), content_type="application/json")

    def test_subscription_check(self):
        ok = self.client.get(self.url, {"hub.mode": "subscribe", "hub.verify_token": "secret-token", "hub.challenge": "abc"})
        self.assertEqual(ok.json(), {"hub.challenge": "abc"})
        bad = self.client.get(self.url, {"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "abc"})
        self.assertEqual(bad.status_code, 403)

    def test_no_csrf_and_bad_json(self):
        client = self.client_class(enforce_csrf_checks=True)
        r = client.post(self.url, "pas du json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        r = client.post(self.url, json.dumps({"hello": 1}), content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_deauthorization_deletes_everything(self):
        Poster.objects.create(account=self.account, settings={})
        r = self.post(object_type="athlete", aspect_type="update", object_id=42, updates={"authorized": "false"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(StravaAccount.objects.exists())
        self.assertFalse(Activity.objects.exists())
        self.assertFalse(Poster.objects.exists())
        self.assertIsNotNone(WebhookEvent.objects.get().processed_at)

    def test_delete_and_update(self):
        self.post(aspect_type="update", updates={"title": "Col du Galibier", "type": "GravelRide"})
        self.activity.refresh_from_db()
        self.assertEqual((self.activity.name, self.activity.sport_type), ("Col du Galibier", "GravelRide"))
        self.post(aspect_type="delete")
        self.assertFalse(Activity.objects.exists())

    def test_other_athlete_is_ignored(self):
        self.post(aspect_type="delete", owner_id=999)
        self.assertTrue(Activity.objects.exists())
        self.assertIsNotNone(WebhookEvent.objects.get().processed_at)

    @mock.patch("activities.strava.activity_detail")
    def test_create_imports_activity(self, detail):
        detail.return_value = {"id": 777, "name": "Nouvelle sortie", "sport_type": "Run", "start_date": "2026-11-03T08:00:00Z",
                               "start_date_local": "2026-11-03T09:00:00Z", "distance": 8000, "map": {"summary_polyline": POLY},
                               "description": "Belle lumière"}
        self.post(object_id=777)
        created = Activity.objects.get(strava_id=777)
        self.assertEqual((created.name, created.account, created.description), ("Nouvelle sortie", self.account, "Belle lumière"))

    @mock.patch("activities.strava.activity_detail", side_effect=strava.RateLimited("quota"))
    def test_rate_limited_event_is_retried(self, detail):
        self.post(object_id=777)
        event = WebhookEvent.objects.get()
        self.assertIsNone(event.processed_at)
        detail.side_effect = None
        detail.return_value = {"id": 777, "name": "Plus tard", "start_date": "2026-11-03T08:00:00Z", "map": {}}
        call_command("strava_webhook", "process", stdout=mock.MagicMock())
        self.assertTrue(Activity.objects.filter(strava_id=777, name="Plus tard").exists())
        event.refresh_from_db()
        self.assertIsNotNone(event.processed_at)

    @override_settings(STRAVA_WEBHOOK_SUBSCRIPTION_ID="12")
    def test_other_subscription_is_ignored(self):
        self.post(aspect_type="delete", subscription_id=99)
        self.assertTrue(Activity.objects.exists())
        self.assertFalse(WebhookEvent.objects.exists())
