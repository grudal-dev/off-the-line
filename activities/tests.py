from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Activity, ActivityPhoto, StravaAccount

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

    @mock.patch("activities.strava.requests.get")
    @mock.patch("activities.strava.requests.post")
    def test_callback_creates_user_logs_in_and_imports(self, post, get):
        post.return_value = fake_response(json_data=TOKEN_RESPONSE)
        get.return_value = fake_response(json_data=[{
            "id": 7, "name": "Première sortie", "sport_type": "Run", "distance": 5000,
            "start_date": "2026-10-01T08:00:00Z", "map": {"summary_polyline": ""},
        }])
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
        self.assertTrue(Activity.objects.filter(strava_id=7, account=account).exists())


# "_p~iF~ps|U_ulLnnqC_mqNvxq`@" = exemple officiel Google : 3 points
SAMPLE_POLYLINE = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


def strava_activity(id_, name="Sortie", start="2026-10-28T17:30:00Z", polyline=SAMPLE_POLYLINE, sport="Run"):
    return {
        "id": id_, "name": name, "type": sport, "sport_type": sport,
        "distance": 10234.5, "moving_time": 3000, "elapsed_time": 3100,
        "total_elevation_gain": 87.2, "average_speed": 3.4,
        "start_date": start, "start_date_local": start,
        "start_latlng": [38.5, -120.2], "map": {"summary_polyline": polyline},
    }


class PolylineTests(TestCase):
    def test_decode_google_example(self):
        from .polyline import decode
        self.assertEqual(decode(SAMPLE_POLYLINE), [[38.5, -120.2], [40.7, -120.95], [43.252, -126.453]])

    def test_decode_empty(self):
        from .polyline import decode
        self.assertEqual(decode(""), [])


class LoggedInTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create(username="strava_42")
        self.account = StravaAccount.objects.create(
            user=self.user, athlete_id=42, firstname="Lucas",
            access_token="tok", refresh_token="ref",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.client.force_login(self.user)


class TokenRefreshTests(LoggedInTestCase):
    @mock.patch("activities.strava.requests.get")
    @mock.patch("activities.strava.requests.post")
    def test_expired_token_is_refreshed_before_call(self, post, get):
        from . import strava
        self.account.expires_at = timezone.now() - timedelta(minutes=1)
        self.account.save()
        post.return_value = fake_response(json_data={
            "access_token": "new", "refresh_token": "ref-new",
            "expires_at": int((timezone.now() + timedelta(hours=6)).timestamp()),
        })
        get.return_value = fake_response(json_data=[])
        strava.api_get(self.account, "/athlete/activities")
        self.account.refresh_from_db()
        self.assertEqual(self.account.access_token, "new")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer new")


class SyncTests(LoggedInTestCase):
    @mock.patch("activities.strava.requests.get")
    def test_sync_creates_activities_and_is_idempotent(self, get):
        from .sync import sync_activities
        get.return_value = fake_response(json_data=[strava_activity(1), strava_activity(2, polyline="")])
        self.assertEqual(sync_activities(self.account), 2)
        self.assertEqual(Activity.objects.count(), 2)
        a = Activity.objects.get(strava_id=1)
        self.assertEqual(a.summary_polyline, SAMPLE_POLYLINE)
        self.assertEqual(a.start_lat, 38.5)

        # 2e synchro : on ne demande que les activités plus récentes
        get.return_value = fake_response(json_data=[])
        sync_activities(self.account)
        self.assertIn("after", get.call_args.kwargs["params"])
        self.assertEqual(Activity.objects.count(), 2)

    @mock.patch("activities.strava.requests.get")
    def test_sync_paginates(self, get):
        from .sync import PER_PAGE, sync_activities
        page1 = [strava_activity(i) for i in range(PER_PAGE)]
        page2 = [strava_activity(PER_PAGE + 1)]
        get.side_effect = [fake_response(json_data=page1), fake_response(json_data=page2)]
        self.assertEqual(sync_activities(self.account), PER_PAGE + 1)
        self.assertEqual(get.call_count, 2)

    @mock.patch("activities.strava.requests.get")
    def test_sync_view_shows_rate_limit_error(self, get):
        get.return_value = fake_response(status=429)
        response = self.client.post(reverse("activities:sync"), follow=True)
        self.assertContains(response, "Limite de requêtes Strava")


def strava_photo(uid, caption=""):
    return {"unique_id": uid, "caption": caption, "created_at": "2026-10-28T17:45:00Z",
            "urls": {"600": f"https://img.example.com/{uid}-600.jpg", "1024": f"https://img.example.com/{uid}.jpg"}}


class PhotoSyncTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        from .sync import _to_fields
        for id_, photos in ((1, 2), (2, 0)):
            data = {**strava_activity(id_), "total_photo_count": photos}
            Activity.objects.create(account=self.account, strava_id=id_, **_to_fields(data))

    @mock.patch("activities.strava.requests.get")
    def test_imports_photos_only_for_activities_that_have_some(self, get):
        from .sync import sync_photos
        get.return_value = fake_response(json_data=[strava_photo("a", "Au sommet"), strava_photo("b")])
        self.assertEqual(sync_photos(self.account), (2, 0))
        self.assertEqual(get.call_count, 1)
        self.assertIn("/activities/1/photos", get.call_args.args[0])
        photo = ActivityPhoto.objects.get(unique_id="a")
        self.assertEqual(photo.url, "https://img.example.com/a.jpg")  # plus grande taille
        self.assertEqual(photo.caption, "Au sommet")

        # Déjà traitée : aucun nouvel appel
        self.assertEqual(sync_photos(self.account), (0, 0))
        self.assertEqual(get.call_count, 1)

    @mock.patch("activities.strava.requests.get")
    def test_batch_limit_leaves_remaining(self, get):
        from .sync import _to_fields, sync_photos
        data = {**strava_activity(3), "total_photo_count": 1}
        Activity.objects.create(account=self.account, strava_id=3, **_to_fields(data))
        get.return_value = fake_response(json_data=[])
        self.assertEqual(sync_photos(self.account, limit=1), (0, 1))

    @mock.patch("activities.strava.requests.get")
    def test_photos_shown_in_list_and_detail(self, get):
        from .sync import sync_photos
        get.return_value = fake_response(json_data=[strava_photo("a", "Au sommet")])
        sync_photos(self.account)
        self.assertContains(self.client.get(reverse("activities:list")), "https://img.example.com/a.jpg")
        detail = self.client.get(reverse("activities:detail", args=[1]))
        self.assertContains(detail, 'alt="Au sommet"')
        self.assertContains(detail, 'id="lightbox"')
        self.assertContains(self.client.get(reverse("activities:map")), "https://img.example.com/a.jpg")


class PhotoBackfillTests(LoggedInTestCase):
    def test_old_activities_get_photo_count_from_raw(self):
        import importlib
        from django.apps import apps
        from .sync import _to_fields
        data = {**strava_activity(7), "total_photo_count": 3}
        Activity.objects.create(account=self.account, strava_id=7, **{**_to_fields(data), "photo_count": 0})
        importlib.import_module("activities.migrations.0004_backfill_photo_count").backfill(apps, None)
        self.assertEqual(Activity.objects.get(strava_id=7).photo_count, 3)


class ActivityPagesTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        from .sync import _to_fields
        for data in (strava_activity(1, "Sortie du soir"), strava_activity(2, "Vélo", sport="Ride"),
                     strava_activity(3, "Tapis", polyline="")):
            Activity.objects.create(account=self.account, strava_id=data["id"], **_to_fields(data))

    def test_list_reads_from_db(self):
        response = self.client.get(reverse("activities:list"))
        self.assertContains(response, "Sortie du soir")
        self.assertContains(response, "10,23 km")
        self.assertContains(response, "50 min")

    def test_list_filters_by_sport(self):
        response = self.client.get(reverse("activities:list"), {"sport": "Ride"})
        self.assertContains(response, "Vélo")
        self.assertNotContains(response, "Sortie du soir")

    def test_detail_has_route(self):
        response = self.client.get(reverse("activities:detail", args=[1]))
        self.assertContains(response, 'id="route-data"')
        self.assertContains(response, "leaflet")

    def test_detail_without_route(self):
        response = self.client.get(reverse("activities:detail", args=[3]))
        self.assertContains(response, "Pas de tracé GPS")

    def test_detail_of_other_user_is_404(self):
        other = get_user_model().objects.create(username="strava_99")
        other_acc = StravaAccount.objects.create(
            user=other, athlete_id=99, access_token="x", refresh_token="y", expires_at=timezone.now())
        from .sync import _to_fields
        Activity.objects.create(account=other_acc, strava_id=500, **_to_fields(strava_activity(500)))
        self.assertEqual(self.client.get(reverse("activities:detail", args=[500])).status_code, 404)

    def test_map_contains_only_routes(self):
        response = self.client.get(reverse("activities:map"))
        self.assertEqual(len(response.context["routes"]), 2)
        self.assertContains(response, 'data-route="1"')
        self.assertNotContains(response, 'data-route="3"')

    def test_pages_require_login(self):
        self.client.logout()
        for url in (reverse("activities:list"), reverse("activities:map"), reverse("activities:detail", args=[1])):
            self.assertEqual(self.client.get(url).status_code, 302)

    @mock.patch("activities.strava.requests.post")
    def test_disconnect_deletes_everything(self, post):
        response = self.client.post(reverse("activities:disconnect"))
        self.assertRedirects(response, reverse("activities:home"))
        self.assertFalse(StravaAccount.objects.exists())
        self.assertFalse(Activity.objects.exists())
