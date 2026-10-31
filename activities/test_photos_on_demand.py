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
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE),
                  leg(3, 12, 8, ALBERTVILLE, MOUTIERS)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.photo_count = 2 if a.strava_id != 2 else 0  # l'étape 2 n'a pas de photo
            a.save()
        self.url = reverse("activities:journey", args=[1])

    @mock.patch("activities.strava.requests.get", side_effect=strava_get)
    def test_opening_a_journey_imports_its_photos_once(self, get):
        page = self.client.get(self.url)
        self.assertEqual(get.call_count, 2)  # étapes 1 et 3 seulement
        self.assertEqual(ActivityPhoto.objects.count(), 4)
        self.assertContains(page, "https://img.example.com/1-0.jpg")
        self.assertContains(page, "https://img.example.com/3-1.jpg")
        self.client.get(self.url)
        self.assertEqual(get.call_count, 2)  # déjà en base : aucun nouvel appel

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

    @mock.patch("activities.strava.requests.get", side_effect=strava_get)
    def test_book_view_fetches_missing_photos(self, get):
        b = Book.objects.create(account=self.account, journey_ids=[1])
        page = self.client.get(reverse("activities:book_detail", args=[b.pk]))
        self.assertContains(page, "https://img.example.com/1-0.jpg")
