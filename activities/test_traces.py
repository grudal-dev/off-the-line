"""Tracés détaillés pour l'impression des affiches."""

import math
from unittest import mock

from django.test import SimpleTestCase
from django.urls import reverse

from . import polyline, tracks
from .models import Activity
from .tests import ANNECY, FAVERGES, SAMPLE_POLYLINE, LoggedInTestCase, fake_response, leg

DETAILED = "_p~iF~ps|U_ulLnnqC_mqNvxq`@"


class SimplifyTests(SimpleTestCase):
    def test_keeps_bends_and_drops_straight_lines(self):
        # Une ligne droite de 1 000 points, puis un virage en épingle de 30 m de rayon
        straight = [(45.0 + i * 1e-5, 6.0) for i in range(1000)]
        bend = [(45.01 + 0.00027 * math.sin(a / 20 * math.pi), 6.0 + 0.00038 * (1 - math.cos(a / 20 * math.pi)))
                for a in range(21)]
        out = tracks.detailed(straight + bend)
        self.assertLess(len(out), 40)
        self.assertGreater(len([p for p in out if p in bend]), 8)  # le virage reste une courbe
        self.assertEqual((out[0], out[-1]), (straight[0], bend[-1]))


class PosterTracesTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ANNECY)):
            a.account, a.summary_polyline = self.account, SAMPLE_POLYLINE
            a.save()
        Activity.objects.filter(strava_id=2).update(detailed_polyline="cache")
        self.url = reverse("activities:poster_traces")

    @mock.patch("activities.strava.requests.get")
    def test_fetches_missing_once_then_serves_cache(self, get):
        get.return_value = fake_response(json_data={"id": 1, "map": {"polyline": DETAILED}})
        data = self.client.get(self.url, {"ids": "1,2,999"}).json()
        self.assertEqual(data["traces"], {"1": DETAILED, "2": "cache"})
        self.assertEqual((data["more"], data["error"]), (False, None))
        self.assertEqual(get.call_count, 1)
        self.assertEqual(Activity.objects.get(strava_id=1).detailed_polyline, DETAILED)
        self.client.get(self.url, {"ids": "1,2"})
        self.assertEqual(get.call_count, 1)
        self.assertEqual(len(polyline.decode(DETAILED)), 3)

    @mock.patch("activities.strava.requests.get")
    def test_rate_limit_returns_what_is_known(self, get):
        get.return_value = fake_response(status=429)
        data = self.client.get(self.url, {"ids": "1,2"}).json()
        self.assertEqual(data["traces"], {"2": "cache"})
        self.assertTrue(data["error"])
        self.assertFalse(data["more"])
