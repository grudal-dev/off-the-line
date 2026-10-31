"""Photos à la demande : importées à l'ouverture d'un périple, d'une activité ou d'un livre."""

from unittest import mock

from django.urls import reverse

from .models import Activity, ActivityPhoto, Book
from .tests import ALBERTVILLE, ANNECY, FAVERGES, MOUTIERS, SAMPLE_POLYLINE, LoggedInTestCase, fake_response, leg


def photos_reply(prefix, n=2):
    return [{"unique_id": f"{prefix}-{i}", "urls": {"1024": f"https://img.example.com/{prefix}-{i}.jpg"},
             "sizes": {"1024": [1024, 768]}} for i in range(n)]


def strava_get(url, params=None, **kwargs):
    """Strava : photos selon l'activité demandée ; rien d'autre n'est appelé dans ces tests."""
    if url.endswith("/photos"):
        activity_id = url.rstrip("/").split("/")[-2]
        return fake_response(json_data=photos_reply(activity_id))
    return fake_response(json_data={})


class PhotosOnDemandTests(LoggedInTestCase):
    """Les pages s'affichent sans attendre Strava ; photos et lieux arrivent en arrière-plan."""

    def setUp(self):
        super().setUp()
        self.photos_patcher.stop()  # ici, on teste le vrai chargement des photos
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE),
                  leg(3, 12, 8, ALBERTVILLE, MOUTIERS)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.photo_count = 2 if a.strava_id != 2 else 0  # Strava annonce 0 photo pour l'étape 2 (souvent faux)
            a.save()
        self.url = reverse("activities:journey", args=[1])
        self.complete = reverse("activities:journey_complete", args=[1])

    @mock.patch("activities.strava.requests.get", side_effect=strava_get)
    def test_journey_page_renders_without_strava_then_completes_in_background(self, get):
        page = self.client.get(self.url)
        self.assertEqual(get.call_count, 0)  # affichage immédiat
        self.assertContains(page, f'data-url="{self.complete}"')
        data = self.client.post(self.complete).json()
        self.assertEqual(get.call_count, 3)  # chaque étape est vérifiée, même annoncée sans photo
        self.assertEqual((data["found"], data["remaining"], data["error"]), (6, 0, None))
        self.assertEqual(Activity.objects.get(strava_id=2).photo_count, 2)
        page = self.client.get(self.url)
        self.assertContains(page, "https://img.example.com/2-0.jpg")
        self.assertNotContains(page, 'id="bg-task"')  # plus rien à récupérer
        self.client.post(self.complete)
        self.assertEqual(get.call_count, 3)  # déjà vérifiées : aucun nouvel appel

    @mock.patch("activities.strava.requests.get", side_effect=strava_get)
    def test_activity_page(self, get):
        url = reverse("activities:detail", args=[3])
        self.assertContains(self.client.get(url), 'id="bg-task"')
        self.assertEqual(get.call_count, 0)
        self.assertEqual(self.client.post(reverse("activities:detail_complete", args=[3])).json()["found"], 2)
        page = self.client.get(url)
        self.assertContains(page, "https://img.example.com/3-0.jpg")
        self.assertNotContains(page, 'id="bg-task"')

    @mock.patch("activities.strava.requests.get")
    def test_rate_limit_asks_to_retry(self, get):
        get.return_value = fake_response(status=429)
        data = self.client.post(self.complete).json()
        self.assertTrue(data["retry"])
        self.assertEqual(data["remaining"], 3 + 4)  # 3 étapes à vérifier + 4 lieux à nommer
        self.assertFalse(Activity.objects.filter(photos_synced_at__isnull=False).exists())

    @mock.patch("activities.views.PHOTO_BATCH_BG", 1)
    @mock.patch("activities.strava.requests.get", side_effect=strava_get)
    def test_small_batches(self, get):
        self.assertEqual(self.client.post(self.complete).json()["remaining"], 2)
        self.assertEqual(self.client.post(self.complete).json()["remaining"], 1)
        self.assertEqual(self.client.post(self.complete).json()["remaining"], 0)
        self.assertEqual(get.call_count, 3)

    def test_other_users_journey_is_404(self):
        from django.contrib.auth import get_user_model
        from .models import StravaAccount
        from django.utils import timezone
        other = get_user_model().objects.create(username="autre")
        StravaAccount.objects.create(user=other, athlete_id=7, access_token="t", refresh_token="r",
                                     expires_at=timezone.now())
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.complete).status_code, 404)
        self.assertEqual(self.client.post(reverse("activities:detail_complete", args=[1])).status_code, 404)


class PlaceNamesInBackgroundTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE),
                  leg(3, 12, 8, ALBERTVILLE, MOUTIERS)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.save()

    def test_list_names_places_in_background(self):
        names = {(45.9, 6.1): "Annecy", (45.7, 6.3): "Faverges", (45.7, 6.4): "Albertville", (45.5, 6.5): "Moûtiers"}
        with mock.patch("activities.places._geopf", side_effect=lambda p: names.get((round(p[0], 1), round(p[1], 1)), "")) as geo:
            page = self.client.get(reverse("activities:journeys"))
            self.assertEqual(geo.call_count, 0)
            self.assertContains(page, 'data-url="/periples/completer/"')
            data = self.client.post(reverse("activities:journeys_complete")).json()
            self.assertEqual((data["found"], data["remaining"]), (4, 0))
        page = self.client.get(reverse("activities:journeys"))
        self.assertContains(page, "Annecy → Moûtiers")
        self.assertNotContains(page, 'id="bg-task"')

    def test_geocoding_down_stops_without_retry(self):
        import requests
        with mock.patch("activities.places._geopf", side_effect=requests.ConnectionError):
            data = self.client.post(reverse("activities:journeys_complete")).json()
        self.assertTrue(data["error"])
        self.assertFalse(data["retry"])
        self.assertEqual(data["remaining"], 4)


class StravaPhotoCounterTests(LoggedInTestCase):
    """Le compteur total_photo_count de la liste Strava vaut souvent 0 alors qu'il y a des photos."""

    @mock.patch("activities.strava.requests.get")
    def test_resync_does_not_forget_photos_strava_counts_as_zero(self, get):
        from django.utils import timezone
        from .sync import sync_activities
        a = leg(1, 10, 8, ANNECY, FAVERGES)
        a.account, a.photo_count, a.photos_synced_at = self.account, 2, timezone.now()
        a.save()
        for i in range(2):
            ActivityPhoto.objects.create(activity=a, unique_id=f"p{i}", url=f"https://img.example.com/{i}.jpg")
        get.return_value = fake_response(json_data=[{
            "id": 1, "name": "Étape 1", "sport_type": "Ride", "start_date": "2026-08-10T08:00:00Z",
            "total_photo_count": 0, "map": {"summary_polyline": ""},
        }])
        sync_activities(self.account, full=True)
        a.refresh_from_db()
        self.assertIsNotNone(a.photos_synced_at)  # pas de nouvel import inutile
        self.assertEqual(a.photo_count, 2)
        self.assertEqual(a.photos.count(), 2)

    @mock.patch("activities.strava.requests.get", side_effect=strava_get)
    def test_sync_photos_all_checks_activities_announced_without_photo(self, get):
        from .sync import sync_photos
        a = leg(1, 10, 8, ANNECY, FAVERGES)
        a.account = self.account
        a.save()
        self.assertEqual(sync_photos(self.account), (0, 0))  # par défaut : on se fie au compteur
        self.assertEqual(sync_photos(self.account, everything=True), (2, 0))


class BookPhotosTests(LoggedInTestCase):
    """Le livre récupère en arrière-plan les photos de toutes ses étapes, par lots."""

    def setUp(self):
        super().setUp()
        self.photos_patcher.stop()
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE),
                  leg(3, 12, 8, ALBERTVILLE, MOUTIERS)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.save()
        self.book = Book.objects.create(account=self.account, journey_ids=[1])
        self.page = reverse("activities:book_detail", args=[self.book.pk])
        self.fetch = reverse("activities:book_fetch_photos", args=[self.book.pk])

    @mock.patch("activities.strava.requests.get", side_effect=strava_get)
    def test_page_renders_without_calling_strava_then_fetches_in_batches(self, get):
        page = self.client.get(self.page)
        self.assertEqual(get.call_count, 0)  # la page s'affiche tout de suite
        self.assertContains(page, "à récupérer")
        self.assertContains(page, self.fetch)
        with mock.patch("activities.views.BOOK_PHOTO_BATCH", 2):
            first = self.client.post(self.fetch).json()
            self.assertEqual((first["imported"], first["remaining"], first["error"]), (4, 1, None))
            second = self.client.post(self.fetch).json()
            self.assertEqual((second["imported"], second["remaining"]), (2, 0))
        page = self.client.get(self.page)
        self.assertNotContains(page, "à récupérer")
        self.assertContains(page, "https://img.example.com/3-1.jpg")

    @mock.patch("activities.strava.requests.get")
    def test_rate_limit_is_reported_to_the_page(self, get):
        get.return_value = fake_response(status=429)
        data = self.client.post(self.fetch).json()
        self.assertEqual((data["imported"], data["remaining"]), (0, 3 + 4))  # étapes + lieux
        self.assertIn("Limite", data["error"])

    def test_other_users_book_is_404(self):
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create(username="autre")
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.fetch).status_code, 404)


class VideoFilterTests(LoggedInTestCase):
    @mock.patch("activities.strava.requests.get")
    def test_videos_are_not_imported_as_photos(self, get):
        from .sync import import_activity_photos
        a = leg(1, 10, 8, ANNECY, FAVERGES)
        a.account = self.account
        a.save()
        photo = {"unique_id": "p", "type": 1, "urls": {"1024": "https://img.example.com/p.jpg"}}
        videos = [
            {"unique_id": "v1", "type": 2, "urls": {"1024": "https://img.example.com/v1.jpg"}},
            {"unique_id": "v2", "media_type": 2, "urls": {"1024": "https://img.example.com/v2.jpg"}},
            {"unique_id": "v3", "video_url": "https://v.example.com/3.mp4", "urls": {"1024": "https://img.example.com/v3.jpg"}},
            {"unique_id": "v4", "duration": 12.5, "urls": {"1024": "https://img.example.com/v4.jpg"}},
        ]
        ActivityPhoto.objects.create(activity=a, unique_id="v1", url="https://img.example.com/v1.jpg")  # importée avant
        get.return_value = fake_response(json_data=[photo] + videos)
        self.assertEqual(import_activity_photos(self.account, a), 1)
        self.assertEqual(list(a.photos.values_list("unique_id", flat=True)), ["p"])
        a.refresh_from_db()
        self.assertEqual(a.photo_count, 1)
