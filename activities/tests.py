from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Activity, ActivityPhoto, Place, StravaAccount

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
        # Pas de géocodage réseau dans les tests de pages (voir PlaceNamingTests)
        for target in ("activities.places._geopf", "activities.places._nominatim"):
            patcher = mock.patch(target, return_value="")
            patcher.start()
            self.addCleanup(patcher.stop)
        # Ni de photos à la demande (testées à part dans test_photos_on_demand)
        self.photos_patcher = mock.patch("activities.views.ensure_photos", return_value=(0, 0, None))
        self.photos_patcher.start()
        self.addCleanup(self.photos_patcher.stop)


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


def leg(id_, day, hour, start, end, hours=4, sport="Ride", **raw):
    """Activité non enregistrée, suffisante pour la détection des périples."""
    from datetime import datetime, timezone as tz
    begin = datetime(2026, 8, day, hour, tzinfo=tz.utc)
    return Activity(
        strava_id=id_, name=f"Étape {id_}", sport_type=sport, start_date=begin, start_date_local=begin,
        distance=80000, moving_time=hours * 3600, elapsed_time=hours * 3600, total_elevation_gain=900,
        start_lat=start[0], start_lng=start[1],
        raw={"start_latlng": list(start), "end_latlng": list(end), **raw},
    )


ANNECY, FAVERGES, ALBERTVILLE, MOUTIERS, BOURG = (45.899, 6.129), (45.748, 6.293), (45.676, 6.392), (45.485, 6.531), (45.618, 6.770)


class JourneyDetectionTests(TestCase):
    def detect(self, activities):
        from .journeys import detect
        return detect(activities)

    def test_successive_point_to_point_stages_form_a_journey(self):
        found = self.detect([
            leg(1, 10, 8, ANNECY, FAVERGES),
            leg(2, 11, 9, FAVERGES, ALBERTVILLE),
            leg(3, 12, 8, ALBERTVILLE, MOUTIERS),
        ])
        self.assertEqual(len(found), 1)
        self.assertEqual([a.strava_id for a in found[0].activities], [1, 2, 3])
        self.assertEqual(found[0].days, 3)
        self.assertEqual(found[0].nights, [0, 1])
        self.assertEqual(found[0].title, "3 jours à vélo")

    def test_daily_loops_from_home_are_not_a_journey(self):
        self.assertEqual(self.detect([leg(i, 10 + i, 8, ANNECY, ANNECY) for i in range(5)]), [])

    def test_loops_around_home_before_and_after_are_trimmed(self):
        found = self.detect([
            leg(1, 9, 8, ANNECY, ANNECY),
            leg(2, 10, 8, ANNECY, FAVERGES),
            leg(3, 11, 8, FAVERGES, ALBERTVILLE),
            leg(4, 12, 8, ALBERTVILLE, ANNECY),
            leg(5, 13, 8, ANNECY, ANNECY),
            leg(6, 14, 8, ANNECY, ANNECY),
        ])
        self.assertEqual([a.strava_id for a in found[0].activities], [2, 3, 4])

    def test_day_loop_from_a_hut_stays_in_the_journey(self):
        found = self.detect([
            leg(1, 10, 8, ANNECY, MOUTIERS),
            leg(2, 11, 8, MOUTIERS, MOUTIERS),
            leg(3, 12, 8, MOUTIERS, BOURG),
        ])
        self.assertEqual([a.strava_id for a in found[0].activities], [1, 2, 3])

    def test_too_long_gap_or_far_restart_breaks_the_chain(self):
        self.assertEqual(self.detect([leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 13, 8, FAVERGES, ALBERTVILLE)]), [])
        self.assertEqual(self.detect([leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, MOUTIERS, BOURG)]), [])

    def test_home_work_commutes_are_ignored(self):
        work = (45.92, 6.25)  # à ~10 km
        commutes = []
        for d in range(5):
            commutes += [leg(2 * d, 10 + d, 7, ANNECY, work, hours=1), leg(2 * d + 1, 10 + d, 17, work, ANNECY, hours=1)]
        self.assertEqual(self.detect(commutes), [])
        flagged = [leg(1, 10, 7, ANNECY, work, commute=True), leg(2, 11, 17, work, ANNECY, commute=True)]
        self.assertEqual(self.detect(flagged), [])

    def test_two_rides_same_day_are_not_a_journey(self):
        self.assertEqual(self.detect([leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 10, 14, FAVERGES, ALBERTVILLE)]), [])

    def test_two_day_trip_is_not_a_journey(self):
        # Strictement plus de 2 jours : un week-end (samedi → dimanche) ne suffit pas
        self.assertEqual(self.detect([leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE)]), [])

    def test_rest_day_is_allowed(self):
        found = self.detect([leg(1, 10, 8, ANNECY, FAVERGES, hours=6), leg(2, 12, 8, FAVERGES, ALBERTVILLE)])
        self.assertEqual(len(found), 1)


@mock.patch("activities.journeys.MIN_DAYS", 2)  # fixtures en 2 jours : on teste autre chose que la durée
class JourneyPagesTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.save()

    def test_list_and_detail(self):
        response = self.client.get(reverse("activities:journeys"))
        self.assertContains(response, "2 jours à vélo")
        self.assertContains(response, reverse("activities:journey", args=[1]))
        detail = self.client.get(reverse("activities:journey", args=[1]))
        self.assertContains(detail, 'id="stages-data"')
        self.assertContains(detail, "Nuit 1")
        self.assertEqual(self.client.get(reverse("activities:journey", args=[2])).status_code, 404)

    def test_map_highlights_journeys(self):
        response = self.client.get(reverse("activities:map"))
        self.assertEqual(response.context["journeys"][0]["stages"], [1, 2])
        self.assertContains(response, 'id="journey-toggle"')

    def test_empty_list_explains_rules(self):
        Activity.objects.all().delete()
        self.assertContains(self.client.get(reverse("activities:journeys")), "Aucun périple")


def fake_http(places=None, photos=None):
    """Simule requests.get pour Strava et les géocodeurs, selon l'URL appelée."""
    places = places or {}

    def get(url, params=None, **kwargs):
        if "geopf" in url:
            name = places.get((round(params["lat"], 1), round(params["lon"], 1)), "")
            return fake_response(json_data={"features": [{"properties": {"city": name}}] if name else []})
        if "nominatim" in url:
            return fake_response(json_data={"address": {"village": "Ailleurs"}})
        if url.endswith("/streams"):
            return fake_response(json_data={"distance": {"data": [0, 500, 1000, 1500]},
                                            "altitude": {"data": [400, 450, 520, 480]}})
        if url.endswith("/photos"):
            return fake_response(json_data=photos or [])
        return fake_response(json_data={"description": "Une belle journée."})
    return get


PLACE_NAMES = {(45.9, 6.1): "Annecy", (45.7, 6.3): "Faverges", (45.7, 6.4): "Albertville"}


@mock.patch("activities.journeys.MIN_DAYS", 2)  # fixtures en 2 jours : on teste autre chose que la durée
class PlaceNamingTests(TestCase):
    @mock.patch("activities.places.requests.get", side_effect=fake_http(PLACE_NAMES))
    def test_journey_and_stages_are_named_and_cached(self, get):
        from .journeys import detect
        from .places import Budget, name_journeys
        j = detect([leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE)])[0]
        name_journeys([j], Budget())
        self.assertEqual(j.name, "Annecy → Albertville")
        self.assertEqual(j.legs[0].label, "Annecy → Faverges")
        calls = get.call_count
        name_journeys([j], Budget())  # tout est en cache
        self.assertEqual(get.call_count, calls)

    @mock.patch("activities.places.requests.get", side_effect=fake_http(PLACE_NAMES))
    def test_round_trip_is_a_loop_and_unknown_place_falls_back(self, get):
        from .journeys import detect
        from .places import Budget, name_journeys
        j = detect([leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ANNECY)])[0]
        name_journeys([j], Budget())
        self.assertEqual(j.name, "Boucle depuis Annecy")
        far = detect([leg(3, 10, 8, MOUTIERS, BOURG), leg(4, 11, 8, BOURG, (45.92, 6.87))])[0]
        name_journeys([far], Budget(nominatim=0))
        self.assertEqual(far.name, "2 jours à vélo")

    def test_without_budget_no_network(self):
        from .journeys import detect
        from .places import name_journeys
        with mock.patch("activities.places.requests.get") as get:
            j = detect([leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE)])[0]
            name_journeys([j])
            get.assert_not_called()
            self.assertEqual(j.name, "2 jours à vélo")


@mock.patch("activities.journeys.MIN_DAYS", 2)  # fixtures en 2 jours : on teste autre chose que la durée
class BookTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.save()
        ActivityPhoto.objects.create(activity=Activity.objects.get(strava_id=2), unique_id="x",
                                     url="https://img.example.com/x.jpg", caption="Au col")

    def test_rename_and_story(self):
        self.client.post(reverse("activities:journey", args=[1]), {"title": "Mon tour", "story": "Il faisait beau."})
        response = self.client.get(reverse("activities:journey", args=[1]))
        self.assertContains(response, "Mon tour")
        self.assertContains(response, "Il faisait beau.")

    @mock.patch("activities.strava.requests.get", side_effect=fake_http(PLACE_NAMES))
    def test_builder_fetches_details_then_book_renders(self, get):
        Place.objects.all().delete()
        with mock.patch("activities.places._geopf", side_effect=lambda p: {
                (45.9, 6.1): "Annecy", (45.7, 6.3): "Faverges", (45.7, 6.4): "Albertville"}[(round(p[0], 1), round(p[1], 1))]):
            builder = self.client.get(reverse("activities:book"))
            self.assertContains(builder, 'data-url="/periples/completer/"')  # noms récupérés en arrière-plan
            self.assertEqual(self.client.post(reverse("activities:journeys_complete")).json()["remaining"], 0)
            self.assertContains(self.client.get(reverse("activities:book")), "Annecy → Albertville")
        response = self.client.post(reverse("activities:book"), {"j": ["1"], "title": "Été 2026"})
        from .models import Book
        created = Book.objects.get()
        self.assertRedirects(response, reverse("activities:book_detail", args=[created.pk]), fetch_redirect_response=False)
        self.assertEqual(created.title, "Été 2026")
        a = Activity.objects.get(strava_id=1)
        self.assertEqual(a.description, "Une belle journée.")
        self.assertEqual(a.profile[-1], [1.5, 480])

        book_page = self.client.get(response["Location"])
        self.assertContains(book_page, "<title>Été 2026</title>", html=False)
        self.assertContains(book_page, "Carnet de route")
        self.assertContains(book_page, "Annecy → Faverges")
        self.assertContains(book_page, "https://img.example.com/x.jpg")
        self.assertContains(book_page, 'id="map-s2"')
        self.assertNotContains(book_page, 'id="tray"')
        self.assertContains(self.client.get(response["Location"], {"edition": "1"}), 'id="tray"')

        # Régénérer ne refait pas d'appels Strava
        calls = get.call_count
        self.client.post(reverse("activities:book"), {"j": ["1"]})
        strava_calls = [c for c in get.call_args_list[calls:] if "strava.com" in c.args[0]]
        self.assertEqual(strava_calls, [])

    def test_builder_requires_a_choice(self):
        response = self.client.post(reverse("activities:book"), {}, follow=True)
        self.assertContains(response, "Choisis au moins un périple")

    def test_book_of_unknown_journey_redirects(self):
        from .models import Book
        b = Book.objects.create(account=self.account, journey_ids=[999])
        self.assertRedirects(self.client.get(reverse("activities:book_detail", args=[b.pk])), reverse("activities:book"))


@override_settings(MEDIA_ROOT=__import__("tempfile").mkdtemp())
@mock.patch("activities.journeys.MIN_DAYS", 2)  # fixtures en 2 jours : on teste autre chose que la durée
class BookEditingTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        from .models import Book
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.description = "Texte Strava"
            a.save()
        act = Activity.objects.get(strava_id=1)
        self.p1 = ActivityPhoto.objects.create(activity=act, unique_id="a", url="https://img.example.com/a.jpg")
        self.p2 = ActivityPhoto.objects.create(activity=act, unique_id="b", url="https://img.example.com/b.jpg")
        self.book = Book.objects.create(account=self.account, journey_ids=[1], title="Livre")
        self.url = reverse("activities:book_update", args=[self.book.pk])

    def post(self, **payload):
        import json
        return self.client.post(self.url, json.dumps(payload), content_type="application/json")

    def page(self):
        return self.client.get(reverse("activities:book_detail", args=[self.book.pk])).content.decode()

    def test_edit_texts(self):
        self.assertEqual(self.post(field="book.title", value="Nouvel été").json(), {"ok": True})
        self.post(field="book.subtitle", value="Avec Paul")
        self.post(field="journey.story", journey=1, value="Le grand départ.")
        self.post(field="stage.title", activity=1, value="Le lac")
        self.post(field="stage.text", activity=2, value="")  # retire le texte Strava de l'étape 2
        html = self.page()
        for text in ("Nouvel été", "Avec Paul", "Le grand départ.", "Le lac"):
            self.assertIn(text, html)
        self.assertEqual(html.count("Texte Strava"), 1)

    def test_photo_selection_order_and_captions(self):
        self.post(field="stage.photos", activity=1, value=[
            {"key": f"s:{self.p2.pk}", "caption": "Au sommet"}])
        html = self.page()
        self.assertIn("https://img.example.com/b.jpg", html)
        self.assertIn("Au sommet", html)
        self.assertNotIn('src="https://img.example.com/a.jpg"', html)

    def test_upload_photo_then_use_it_as_cover(self):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        buf = BytesIO()
        Image.new("RGB", (3000, 2000), "green").save(buf, "PNG")
        files = [SimpleUploadedFile("vue.png", buf.getvalue(), "image/png"),
                 SimpleUploadedFile("notes.txt", b"pas une image", "text/plain")]
        r = self.client.post(reverse("activities:book_upload", args=[self.book.pk]), {"activity": "2", "photos": files})
        self.assertEqual(r.json()["added"], 1)
        self.assertEqual(r.json()["rejected"], ["notes.txt"])
        upload = self.book.uploads.get()
        from PIL import Image as PILImage
        self.assertEqual(max(PILImage.open(upload.image.path).size), 2400)  # redimensionnée
        self.assertIn(upload.image.url, self.page())

        self.post(field="cover", value="photo", photo=upload.key)
        self.assertIn('class="cover-photo" src="' + upload.image.url, self.page())

        self.post(field="upload.delete", upload=upload.pk)
        self.assertFalse(self.book.uploads.exists())

    def test_copy_a_photo_from_another_stage_and_reject_unknown_keys(self):
        self.post(field="stage.photos", activity=2, value=[
            {"key": f"s:{self.p1.pk}", "caption": "Copiée"}, {"key": "s:999999", "caption": "?"}])
        from .models import BookStage
        self.assertEqual(BookStage.objects.get(activity_id=2).photos, [{"key": f"s:{self.p1.pk}", "caption": "Copiée"}])
        html = self.page()
        self.assertEqual(html.count('src="https://img.example.com/a.jpg"'), 2)  # étape 1 (défaut) + étape 2
        self.assertIn("Copiée", html)

    def test_remove_all_photos_of_a_stage_keeps_them_in_the_pool(self):
        self.post(field="stage.photos", activity=1, value=[])
        html = self.client.get(reverse("activities:book_detail", args=[self.book.pk]), {"edition": "1"}).content.decode()
        self.assertNotIn('src="https://img.example.com/a.jpg"', html)
        self.assertIn('"https://img.example.com/a.jpg"', html)  # toujours dans le bac (JSON)
        self.assertIn("Glisse des photos ici", html)

    def test_upload_to_tray_then_delete_removes_it_everywhere(self):
        from io import BytesIO
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        buf = BytesIO()
        Image.new("RGB", (800, 600), "blue").save(buf, "JPEG")
        r = self.client.post(reverse("activities:book_upload", args=[self.book.pk]),
                             {"photos": [SimpleUploadedFile("a.jpg", buf.getvalue(), "image/jpeg")]})
        key = r.json()["keys"][0]
        upload = self.book.uploads.get()
        self.assertIsNone(upload.activity_id)
        self.assertNotIn(upload.image.url, self.page())  # dans le bac, pas encore sur une page
        self.post(field="stage.photos", activity=2, value=[{"key": key, "caption": ""}])
        self.assertIn(upload.image.url, self.page())
        self.post(field="upload.delete", upload=upload.pk)
        from .models import BookStage
        self.assertEqual(BookStage.objects.get(activity_id=2).photos, [])

    def test_options_hide_sections(self):
        self.assertIn("Carnet de route", self.page())
        self.post(field="options", value={"roadbook": False, "back": False, "basemap": "ign", "bogus": 1})
        self.book.refresh_from_db()
        self.assertEqual(self.book.options, {"roadbook": False, "back": False, "basemap": "ign"})
        html = self.page()
        self.assertNotIn("Carnet de route", html)
        self.assertNotIn("jours sur la route", html)

    def test_cannot_touch_other_users_book_or_foreign_stage(self):
        self.assertEqual(self.post(field="stage.title", activity=999, value="x").status_code, 400)
        other = get_user_model().objects.create(username="strava_7")
        StravaAccount.objects.create(user=other, athlete_id=7, access_token="x", refresh_token="y",
                                     expires_at=timezone.now())
        self.client.force_login(other)
        self.assertEqual(self.post(field="book.title", value="pirate").status_code, 404)
        self.assertEqual(self.client.get(reverse("activities:book_detail", args=[self.book.pk])).status_code, 404)

    def test_delete_book(self):
        self.client.post(reverse("activities:book_delete", args=[self.book.pk]))
        from .models import Book
        self.assertFalse(Book.objects.exists())


class PhotoLayoutTests(TestCase):
    def photos(self, *ratios):
        return [{"ratio": r, "caption": ""} for r in ratios]

    def test_layout_never_exceeds_the_box_and_keeps_ratios(self):
        from .book import CONTENT_W, GAP, best_layout
        for ratios in [(1.5,), (0.75,), (3.0, 0.75), (1.5, 1.5, 0.66), (1, 1, 1, 1, 1, 1)]:
            layout = best_layout(self.photos(*ratios), CONTENT_W, 120)
            # hauteur occupée = somme des rangées réduites + espaces : jamais plus que la zone
            self.assertLessEqual(layout["scale"], 1)
            self.assertEqual(sum(layout["rows"]), len(ratios))

    def test_two_landscapes_share_a_row_portraits_get_more_room(self):
        from .book import paginate_photos
        lead, pages, box = paginate_photos(self.photos(1.5, 1.5, 1.5))
        self.assertEqual(len(lead), 2)  # une 3e photo paysage serait trop petite
        lead, pages, tall = paginate_photos(self.photos(0.75, 0.75, 0.75))
        self.assertEqual(len(lead), 3)  # trois portraits tiennent côte à côte
        self.assertGreater(tall, box)   # et la zone photo est plus haute

    def test_photo_pages_hold_more_small_photos_than_panoramas(self):
        from .book import paginate_photos
        _, pages, _ = paginate_photos(self.photos(*[1.5] * 3 + [3.0] * 6))
        self.assertTrue(all(len(p) <= 6 for p in pages))
        self.assertEqual(sum(len(p) for p in pages) + 2, 9)

    @mock.patch("activities.strava.requests.get")
    def test_sync_stores_photo_dimensions(self, get):
        from .sync import _to_fields, sync_photos
        user = get_user_model().objects.create(username="strava_5")
        acc = StravaAccount.objects.create(user=user, athlete_id=5, access_token="t", refresh_token="r",
                                           expires_at=timezone.now() + timedelta(hours=1))
        Activity.objects.create(account=acc, strava_id=50, **{**_to_fields(strava_activity(50)), "photo_count": 1})
        get.return_value = fake_response(json_data=[{**strava_photo("p"), "sizes": {"1024": [768, 1024]}}])
        sync_photos(acc)
        photo = ActivityPhoto.objects.get()
        self.assertEqual((photo.width, photo.height), (768, 1024))


NICE, MENTON, SANREMO = (43.70, 7.27), (43.775, 7.50), (43.815, 7.775)


@mock.patch("activities.journeys.MIN_DAYS", 2)  # fixtures en 2 jours : on teste autre chose que la durée
class TransferDetectionTests(TestCase):
    def detect(self, activities):
        from .journeys import detect
        return detect(activities)

    def test_train_in_the_middle_of_a_trip(self):
        found = self.detect([
            leg(1, 10, 8, ANNECY, FAVERGES),
            leg(2, 11, 8, FAVERGES, ALBERTVILLE),
            leg(3, 12, 14, NICE, MENTON),        # train Albertville → Nice dans la nuit
            leg(4, 13, 8, MENTON, SANREMO),
        ])
        self.assertEqual(len(found), 1)
        j = found[0]
        self.assertEqual([a.strava_id for a in j.activities], [1, 2, 3, 4])
        self.assertEqual(j.transfers, [1])
        self.assertGreater(j.transfer_after(1)["km"], 150)
        self.assertIsNone(j.transfer_after(0))

    def test_continuous_link_is_preferred_to_a_transfer(self):
        found = self.detect([
            leg(1, 10, 8, ANNECY, FAVERGES),
            leg(2, 11, 7, NICE, MENTON),          # plus tôt, mais loin
            leg(3, 11, 9, FAVERGES, ALBERTVILLE),  # repart de l'arrivée : c'est la suite
        ])
        self.assertEqual([a.strava_id for a in found[0].activities], [1, 3])
        self.assertEqual(found[0].transfers, [])

    def test_rides_from_two_places_are_not_a_trip(self):
        # Une sortie un jour, une autre ailleurs le lendemain : 1 transfert, 0 enchaînement
        self.assertEqual(self.detect([leg(1, 10, 8, FAVERGES, ALBERTVILLE), leg(2, 11, 8, NICE, MENTON)]), [])

    def test_no_transfer_from_or_to_home(self):
        home_loops = [leg(100 + i, 1 + i, 8, ANNECY, ANNECY) for i in range(6)]  # Annecy = chez soi
        found = self.detect(home_loops + [
            leg(1, 10, 8, NICE, MENTON),
            leg(2, 11, 8, MENTON, SANREMO),
            leg(3, 12, 9, ANNECY, FAVERGES),  # rentré à la maison : la sortie suivante en repart
        ])
        self.assertEqual([a.strava_id for a in found[0].activities], [1, 2])

    def test_impossible_speed_is_not_a_transfer(self):
        far = (40.0, -3.7)  # Madrid, une heure après : impossible
        found = self.detect([
            leg(1, 10, 8, ANNECY, FAVERGES, hours=2), leg(2, 10, 11, far, (40.1, -3.6)),
            leg(3, 11, 8, FAVERGES, ALBERTVILLE),
        ])
        self.assertEqual([a.strava_id for a in found[0].activities], [1, 3])


class TransferPagesTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE),
                  leg(3, 12, 14, NICE, MENTON), leg(4, 13, 8, MENTON, SANREMO)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.save()

    def test_choose_transport_mode(self):
        url = reverse("activities:journey", args=[1])
        self.assertContains(self.client.get(url), 'name="transfer_index" value="1"')
        self.client.post(url, {"transfer_index": "1", "mode": "train"})
        page = self.client.get(url)
        self.assertContains(page, '<option value="train" selected>', html=False)
        self.client.post(url, {"transfer_index": "0", "mode": "train"})  # pas un transfert : ignoré
        from .models import JourneyNote
        self.assertEqual(JourneyNote.objects.get().transfers, {"a2": "train"})

    def test_book_mentions_the_transfer(self):
        from .models import Book, JourneyNote
        JourneyNote.objects.create(account=self.account, journey_id=1, transfers={"1": "ferry"})
        b = Book.objects.create(account=self.account, journey_ids=[1])
        page = self.client.get(reverse("activities:book_detail", args=[b.pk]))
        self.assertContains(page, "Ferry")
        self.assertContains(page, "vol d'oiseau")


class ImportStravaTokensCommandTests(TestCase):
    """Commande import_strava_tokens : jetons obtenus hors du site (curl / PowerShell)."""

    def _write(self, data, encoding="utf-8"):
        import json, tempfile, os
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, path)
        with open(path, "w", encoding=encoding) as f:
            f.write(json.dumps(data))
        return path

    def _call(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("import_strava_tokens", *args, stdout=out)
        return out.getvalue()

    def test_creates_user_and_account_like_the_oauth_callback(self):
        out = self._call(self._write(TOKEN_RESPONSE))
        account = StravaAccount.objects.get(athlete_id=42)
        self.assertEqual(account.user.username, "strava_42")
        self.assertEqual(account.refresh_token, "ref-123")
        self.assertEqual(account.scope, "read,activity:read_all")
        self.assertIn("créé", out)

    def test_reads_powershell_utf16_file(self):
        # Out-File de Windows PowerShell 5 écrit en UTF-16 avec BOM
        self._call(self._write(TOKEN_RESPONSE, encoding="utf-16"))
        self.assertTrue(StravaAccount.objects.filter(athlete_id=42).exists())

    def test_reimport_updates_tokens_without_duplicating(self):
        self._call(self._write(TOKEN_RESPONSE))
        out = self._call(self._write({**TOKEN_RESPONSE, "refresh_token": "ref-new"}))
        self.assertEqual(StravaAccount.objects.count(), 1)
        self.assertEqual(StravaAccount.objects.get().refresh_token, "ref-new")
        self.assertIn("mis à jour", out)

    def test_missing_refresh_token_is_an_error(self):
        from django.core.management.base import CommandError
        data = {k: v for k, v in TOKEN_RESPONSE.items() if k != "refresh_token"}
        with self.assertRaises(CommandError):
            self._call(self._write(data))
        self.assertFalse(StravaAccount.objects.exists())

    @mock.patch("activities.strava.requests.get")
    def test_sync_option_imports_activities(self, get):
        get.return_value = fake_response(json_data=[{
            "id": 7, "name": "Sortie", "sport_type": "Ride", "distance": 20000,
            "start_date": "2026-10-01T08:00:00Z", "map": {"summary_polyline": ""},
        }])
        out = self._call(self._write(TOKEN_RESPONSE), "--sync")
        self.assertEqual(StravaAccount.objects.get().activities.count(), 1)
        self.assertIn("1 activité(s) importée(s)", out)


class SyncAllPhotosCommandTests(LoggedInTestCase):
    """Commande sync_photos : enchaîne les lots et attend quand le quota Strava est atteint."""

    def setUp(self):
        super().setUp()
        for i in range(5):
            Activity.objects.create(account=self.account, strava_id=100 + i, name=f"A{i}",
                                    start_date=timezone.now() - timedelta(days=i), photo_count=1)

    def _photo(self, uid):
        return [{"unique_id": uid, "urls": {"1024": f"https://example.com/{uid}.jpg"},
                 "sizes": {"1024": [1024, 768]}}]

    def _call(self):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("sync_photos", "--athlete", "42", stdout=out)
        return out.getvalue()

    @mock.patch("activities.management.commands.sync_photos.PHOTO_BATCH", 2)
    @mock.patch("activities.strava.requests.get")
    def test_processes_every_batch(self, get):
        get.side_effect = [fake_response(json_data=self._photo(f"p{i}")) for i in range(5)]
        out = self._call()
        self.assertEqual(ActivityPhoto.objects.count(), 5)
        self.assertFalse(Activity.objects.filter(photos_synced_at__isnull=True).exists())
        self.assertIn("Terminé : 5 photo(s)", out)

    @mock.patch("activities.management.commands.sync_photos.time.sleep")
    @mock.patch("activities.strava.requests.get")
    def test_waits_on_rate_limit_then_resumes(self, get, sleep):
        responses = [fake_response(json_data=self._photo("p0")), fake_response(status=429)]
        responses += [fake_response(json_data=self._photo(f"p{i}")) for i in range(1, 5)]
        get.side_effect = responses
        out = self._call()
        sleep.assert_called_once()
        self.assertEqual(ActivityPhoto.objects.count(), 5)
        self.assertIn("Quota Strava atteint", out)

    def test_next_window(self):
        from datetime import datetime
        from .management.commands.sync_photos import seconds_until_next_window
        self.assertEqual(seconds_until_next_window(datetime(2026, 10, 31, 21, 44, 0)), 60 + 15)
        self.assertEqual(seconds_until_next_window(datetime(2026, 10, 31, 21, 45, 0)), 15 * 60 + 15)


class PosterTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        for i, (sport, day) in enumerate((("Ride", 3), ("Run", 4))):
            Activity.objects.create(account=self.account, strava_id=100 + i, name=f"Sortie {i}", sport_type=sport,
                                    start_date=timezone.now() - timedelta(days=day), distance=12000,
                                    total_elevation_gain=150, summary_polyline=SAMPLE_POLYLINE)
        Activity.objects.create(account=self.account, strava_id=200, name="Salle", sport_type="Workout",
                                start_date=timezone.now(), summary_polyline="")

    def test_page_sends_routes_with_a_trace(self):
        response = self.client.get(reverse("activities:poster"))
        self.assertContains(response, 'id="poster-canvas"')
        self.assertContains(response, "poster.js")
        routes = response.context["routes"]
        self.assertEqual([r["id"] for r in routes], [101, 100])  # du plus ancien au plus récent, sans la salle
        self.assertEqual([r["family"] for r in routes], ["run", "ride"])
        self.assertEqual(routes[0]["elev"], 150)
        self.assertEqual(response.context["initial"], {})

    def test_landmark_options_and_data(self):
        from django.contrib.staticfiles import finders
        response = self.client.get(reverse("activities:poster"))
        for name in ("countries", "regions", "coast"):
            self.assertContains(response, f'name="{name}"')
            self.assertContains(response, f"activities/borders-{name}.json")
            self.assertTrue(finders.find(f"activities/borders-{name}.json"))
        self.assertContains(response, 'name="cities"')
        self.assertContains(response, 'name="brand"')
        for value in ("none", "gap", "escape", "signature", "stamp"):
            self.assertContains(response, f'<option value="{value}">')
        self.assertContains(response, 'name="q"')
        self.assertContains(response, 'id="activity-search"')
        self.assertNotContains(response, 'name="hl"')  # aucun périple : pas d'option de mise en évidence

    def test_preselected_activity(self):
        response = self.client.get(reverse("activities:poster"), {"activite": "101"})
        self.assertEqual(response.context["initial"], {"source": "activity", "id": 101})
        unknown = self.client.get(reverse("activities:poster"), {"activite": "999", "periple": "x"})
        self.assertEqual(unknown.context["initial"], {})

    def test_links_from_activity_and_nav(self):
        response = self.client.get(reverse("activities:detail", args=[100]))
        self.assertContains(response, reverse("activities:poster") + "?activite=100")

    def test_empty_state(self):
        Activity.objects.all().delete()
        response = self.client.get(reverse("activities:poster"))
        self.assertContains(response, "Aucun parcours avec tracé GPS")
        self.assertNotContains(response, "poster.js")


class ZoneSelectionTests(LoggedInTestCase):
    """Zone de la carte : affiche ou livre à partir d'activités choisies (pas forcément un périple)."""

    def setUp(self):
        super().setUp()
        # Deux sorties isolées (pas un périple) et une sans tracé
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 20, 8, ANNECY, ALBERTVILLE, commute=True)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.photos_synced_at = a.details_fetched_at = timezone.now()
            a.save()

    def test_map_offers_poster_and_book(self):
        response = self.client.get(reverse("activities:map"))
        self.assertContains(response, 'formaction="/affiche/"')
        self.assertContains(response, 'formaction="/livre/"')
        self.assertEqual({r["day"][:10] for r in response.context["routes_data"]}, {"2026-08-10", "2026-08-20"})

    def test_poster_from_selection(self):
        response = self.client.post(reverse("activities:poster"), {"a": ["2", "999"], "title": "Mes Alpes"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["initial"], {"source": "selection", "id": [2], "title": "Mes Alpes"})
        self.assertContains(response, 'data-source="selection"')

    @mock.patch("activities.strava.requests.get", side_effect=fake_http(PLACE_NAMES))
    def test_book_from_selection(self, get):
        from .models import Book
        response = self.client.post(reverse("activities:book"), {"a": ["2", "1"]})
        book_obj = Book.objects.get()
        self.assertRedirects(response, reverse("activities:book_detail", args=[book_obj.pk]), fetch_redirect_response=False)
        self.assertEqual(book_obj.activity_ids, [1, 2])  # ordre chronologique, trajet domicile-travail compris
        self.assertEqual(book_obj.journey_ids, [])
        page = self.client.get(response["Location"])
        self.assertContains(page, 'id="map-s1"')
        self.assertContains(page, 'id="map-s2"')
        self.assertContains(self.client.get(reverse("activities:book")), "Sélection sur la carte : 2 activités")

        # Titre et récit du chapitre : enregistrés dans le livre
        url = reverse("activities:book_update", args=[book_obj.pk])
        for field, value in (("journey.title", "Deux sorties"), ("journey.story", "Un bel été.")):
            r = self.client.post(url, {"field": field, "value": value, "journey": 0}, content_type="application/json")
            self.assertTrue(r.json()["ok"])
        book_obj.refresh_from_db()
        self.assertEqual((book_obj.selection_title, book_obj.selection_story), ("Deux sorties", "Un bel été."))
        self.assertContains(self.client.get(response["Location"]), "Deux sorties")
        bad = self.client.post(url, {"field": "journey.title", "value": "x", "journey": 5}, content_type="application/json")
        self.assertEqual(bad.status_code, 400)

    def test_book_selection_limits(self):
        from .models import Book
        response = self.client.post(reverse("activities:book"), {"a": ["999"]}, follow=True)
        self.assertContains(response, "Aucune des activités choisies")
        with mock.patch("activities.views.MAX_BOOK_ACTIVITIES", 1):
            response = self.client.post(reverse("activities:book"), {"a": ["1", "2"]}, follow=True)
        self.assertContains(response, "Trop d&#x27;activités")
        self.assertFalse(Book.objects.exists())



@mock.patch("activities.journeys.MIN_DAYS", 2)
class PosterJourneyDataTests(LoggedInTestCase):
    """Données envoyées à l'affiche pour un périple : lieux d'étape, jours, profils allégés."""

    def setUp(self):
        super().setUp()
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE), leg(3, 12, 8, ALBERTVILLE, FAVERGES)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.profile = [[k / 10, 400 + k] for k in range(120)]
            a.save()
        from decimal import Decimal
        from .places import _key
        for point, name in ((ANNECY, "Annecy"), (FAVERGES, "Faverges"), (ALBERTVILLE, "Albertville")):
            lat, lng = _key(point)
            Place.objects.create(lat_key=lat, lng_key=lng, name=name)

    def test_trip_stops_days_and_profiles(self):
        response = self.client.get(reverse("activities:poster"))
        trip = response.context["journeys"][0]
        self.assertEqual(trip["days"], 3)
        # Faverges revient en arrivée : une seule fois, à la fin, au point d'arrivée
        self.assertEqual([s[2] for s in trip["stops"]], ["Annecy", "Albertville", "Faverges"])
        self.assertEqual(trip["stops"][-1][:2], [round(FAVERGES[0], 4), round(FAVERGES[1], 4)])
        route = response.context["routes"][0]
        self.assertLessEqual(len(route["prof"]), 41)
        self.assertEqual(route["prof"][0], 400)
        self.assertContains(response, "countries-shapes.json")
        for name in ("mute", "casing", "stageDots", "cityMode", "profile", "air"):
            self.assertContains(response, f'name="{name}"')

    def test_country_shapes_file(self):
        import json
        from django.contrib.staticfiles import finders
        data = json.loads(open(finders.find("activities/countries-shapes.json"), encoding="utf-8").read())
        names = [row[0] for row in data["countries"]]
        self.assertIn("France", names)
        self.assertEqual(len(names), len(set(names)))
