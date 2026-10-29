from django.test import TestCase
from django.urls import reverse


class HomeTests(TestCase):
    def test_home_ok(self):
        response = self.client.get(reverse("activities:home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Off the line")
