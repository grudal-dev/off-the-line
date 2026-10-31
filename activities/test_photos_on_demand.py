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
    def setUp(self):
        super().setUp()
        self.photos_patcher.stop()  # ici, on teste le vrai chargement à la demande
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE),
                  leg(3, 12, 8, ALBERTVILLE, MOUTIERS)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.photo_count = 2 if a.strava_id != 2 else 0  # Strava annonce 0 photo pour l'étape 2 (souvent faux)
            a.save()
        self.url = reverse("activities:journey", args=[1])

    @mock.patch("activities.strava.requests.get", side_effect=strava_get)
    def test_opening_a_journey_imports_its_photos_once(self, get):
        page = self.client.get(self.url)
        self.assertEqual(get.call_count, 3)  # chaque étape est vérifiée, même annoncée sans photo
        self.assertEqual(ActivityPhoto.objects.count(), 6)
        self.assertContains(page, "https://img.example.com/1-0.jpg")
        self.assertContains(page, "https://img.example.com/2-0.jpg")  # le compteur Strava disait 0
        self.assertEqual(Activity.objects.get(strava_id=2).photo_count, 2)
        self.client.get(self.url)
        self.assertEqual(get.call_count, 3)  # déjà vérifiées : aucun nouvel appel

    @mock.patch("activities.strava.requests.get", side_effect=strava_get)
    def test_opening_an_activity_imports_its_photos(self, get):
        page = self.client.get(reverse("activities:detail", args=[3]))
        self.assertEqual(get.call_count, 1)
        self.assertContains(page, "https://img.example.com/3-0.jpg")

    @mock.patch("activities.strava.requests.get")
    def test_rate_limit_does_not_break_the_page(self, get):
        get.return_value = fake_response(status=429)
        page = self.client.get(self.url, follow=True)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "certaines photos arriveront")
        self.assertFalse(Activity.objects.filter(photos_synced_at__isnull=False).exists())

    @mock.patch("activities.sync.ON_DEMAND_LIMIT", 1)
    @mock.patch("activities.strava.requests.get", side_effect=strava_get)
    def test_limit_per_page_view(self, get):
        page = self.client.get(self.url, follow=True)
        self.assertEqual(get.call_count, 1)
        self.assertContains(page, "recharge la page")
        self.client.get(self.url)
        self.assertEqual(get.call_count, 2)
        self.client.get(self.url)
        self.assertEqual(get.call_count, 3)


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
        self.assertContains(page, "3 étapes à vérifier")
        self.assertContains(page, self.fetch)
        with mock.patch("activities.views.BOOK_PHOTO_BATCH", 2):
            first = self.client.post(self.fetch).json()
            self.assertEqual((first["imported"], first["remaining"], first["error"]), (4, 1, None))
            second = self.client.post(self.fetch).json()
            self.assertEqual((second["imported"], second["remaining"]), (2, 0))
        page = self.client.get(self.page)
        self.assertNotContains(page, "à vérifier")
        self.assertContains(page, "https://img.example.com/3-1.jpg")

    @mock.patch("activities.strava.requests.get")
    def test_rate_limit_is_reported_to_the_page(self, get):
        get.return_value = fake_response(status=429)
        data = self.client.post(self.fetch).json()
        self.assertEqual((data["imported"], data["remaining"]), (0, 3))
        self.assertIn("Limite", data["error"])

    def test_other_users_book_is_404(self):
        from django.contrib.auth import get_user_model
        other = get_user_model().objects.create(username="autre")
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.fetch).status_code, 404)
