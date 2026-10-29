from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import StravaAccount

TOKEN_RESPONSE = {
    "access_token": "acc-123",
    "refresh_token": "ref-123",
    "expires_at": int((timezone.now() + timedelta(hours=6)).timestamp()),
    "athlete": {"id": 42, "firstname": "Lucas", "lastname": "G", "profile": "https://example.com/p.jpg"},
}


def fake_response(status=200, json_data=None):
    r = mock.Mock(status_code=status, text="")
    r.json.return_value = json_data
    return r


@override_settings(STRAVA_CLIENT_ID="1234", STRAVA_CLIENT_SECRET="secret")
class StravaOAuthTests(TestCase):
    def test_home_ok(self):
        response = self.client.get(reverse("activities:home"))
        self.assertContains(response, "Se connecter avec Strava")

    def test_login_redirects_to_strava_with_state(self):
        response = self.client.get(reverse("activities:strava_login"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("strava.com/oauth/authorize", response["Location"])
        self.assertIn("activity%3Aread_all", response["Location"])
        self.assertIn("strava_oauth_state", self.client.session)

    @override_settings(STRAVA_CLIENT_ID="")
    def test_login_without_keys_shows_error(self):
        response = self.client.get(reverse("activities:strava_login"), follow=True)
        self.assertContains(response, "Clés Strava manquantes")

    def _start_login(self):
        self.client.get(reverse("activities:strava_login"))
        return self.client.session["strava_oauth_state"]

    def test_callback_rejects_bad_state(self):
        self._start_login()
        response = self.client.get(
            reverse("activities:strava_callback"), {"state": "wrong", "code": "x", "scope": "read,activity:read_all"}
        )
        self.assertRedirects(response, reverse("activities:home"))
        self.assertFalse(StravaAccount.objects.exists())

    def test_callback_requires_activity_scope(self):
        state = self._start_login()
        response = self.client.get(
            reverse("activities:strava_callback"), {"state": state, "code": "x", "scope": "read"}
        )
        self.assertRedirects(response, reverse("activities:home"))
        self.assertFalse(StravaAccount.objects.exists())

    @mock.patch("activities.strava.requests.post")
    def test_callback_creates_user_and_logs_in(self, post):
        post.return_value = fake_response(json_data=TOKEN_RESPONSE)
        state = self._start_login()
        response = self.client.get(
            reverse("activities:strava_callback"),
            {"state": state, "code": "abc", "scope": "read,activity:read_all"},
        )
        self.assertRedirects(response, reverse("activities:list"), fetch_redirect_response=False)
        account = StravaAccount.objects.get(athlete_id=42)
        self.assertEqual(account.access_token, "acc-123")
        self.assertEqual(account.user.username, "strava_42")
        self.assertEqual(int(self.client.session["_auth_user_id"]), account.user.pk)


class ActivityListTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="strava_42")
        self.account = StravaAccount.objects.create(
            user=self.user, athlete_id=42, firstname="Lucas",
            access_token="old", refresh_token="ref-old",
            expires_at=timezone.now() - timedelta(minutes=1),  # expiré
        )
        self.client.force_login(self.user)

    @mock.patch("activities.strava.requests.get")
    @mock.patch("activities.strava.requests.post")
    def test_list_refreshes_token_then_fetches(self, post, get):
        post.return_value = fake_response(json_data={
            "access_token": "new", "refresh_token": "ref-new",
            "expires_at": int((timezone.now() + timedelta(hours=6)).timestamp()),
        })
        get.return_value = fake_response(json_data=[{
            "name": "Sortie du soir", "type": "Run", "sport_type": "Run",
            "distance": 10234.5, "moving_time": 3000, "total_elevation_gain": 87.2,
            "start_date_local": "2026-10-28T18:30:00Z",
        }])
        response = self.client.get(reverse("activities:list"))
        self.assertContains(response, "Sortie du soir")
        self.assertContains(response, "10,23 km")
        self.account.refresh_from_db()
        self.assertEqual(self.account.access_token, "new")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer new")

    @mock.patch("activities.strava.requests.get")
    def test_list_shows_rate_limit_error(self, get):
        self.account.expires_at = timezone.now() + timedelta(hours=1)
        self.account.save()
        get.return_value = fake_response(status=429)
        response = self.client.get(reverse("activities:list"))
        self.assertContains(response, "Limite de requêtes Strava")

    def test_list_requires_login(self):
        self.client.logout()
        response = self.client.get(reverse("activities:list"))
        self.assertEqual(response.status_code, 302)

    @mock.patch("activities.strava.requests.post")
    def test_disconnect_deletes_tokens(self, post):
        response = self.client.post(reverse("activities:disconnect"))
        self.assertRedirects(response, reverse("activities:home"))
        self.assertFalse(StravaAccount.objects.exists())
