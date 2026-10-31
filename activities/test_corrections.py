"""Corrections de périple (retirer, remettre, ajouter, renommer, masquer) et livre depuis une zone."""

from django.urls import reverse

from .models import Book, JourneyNote
from .tests import ALBERTVILLE, ANNECY, BOURG, FAVERGES, MOUTIERS, SAMPLE_POLYLINE, LoggedInTestCase, leg


class JourneyCorrectionTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        # Périple de 4 étapes (10 → 13 août) + une boucle le 14, ailleurs, que la détection écarte
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE),
                  leg(3, 12, 8, ALBERTVILLE, MOUTIERS), leg(4, 13, 8, MOUTIERS, BOURG),
                  leg(9, 14, 8, (45.2, 6.9), (45.2, 6.9))):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.save()
        self.url = reverse("activities:journey", args=[1])

    def journey(self):
        from .journeys import for_account
        return next(j for j in for_account(self.account) if j.id == 1)

    def fix(self, follow=False, **data):
        return self.client.post(self.url, {"action": "stages", **data}, follow=follow)

    def test_remove_first_stage_keeps_the_same_journey_and_url(self):
        self.fix(remove=["1"])
        j = self.journey()
        self.assertEqual([a.strava_id for a in j.activities], [2, 3, 4])
        self.assertEqual(j.id, 1)
        self.assertTrue(j.corrected)
        self.assertEqual([leg.activity.strava_id for leg in j.removed], [1])
        page = self.client.get(self.url)
        self.assertContains(page, "Étapes retirées")
        self.assertContains(page, 'name="restore" value="1"')

    def test_restore_a_removed_stage(self):
        self.fix(remove=["2"])
        self.fix(restore=["2"])
        self.assertEqual([a.strava_id for a in self.journey().activities], [1, 2, 3, 4])

    def test_add_a_nearby_activity_then_remove_it(self):
        self.assertContains(self.client.get(self.url), 'name="add" value="9"')
        self.fix(add=["9"])
        j = self.journey()
        self.assertEqual([a.strava_id for a in j.activities], [1, 2, 3, 4, 9])
        self.assertIn(3, j.transfers)  # repart loin de l'arrivée précédente : transfert
        self.fix(remove=["9"])
        note = JourneyNote.objects.get()
        self.assertEqual((note.added, note.excluded), ([], []))

    def test_cannot_add_an_activity_far_from_the_dates(self):
        far = leg(50, 25, 8, ANNECY, FAVERGES)
        far.account, far.summary_polyline = self.account, SAMPLE_POLYLINE
        far.save()
        self.fix(add=["50"])
        self.assertNotIn(50, [a.strava_id for a in self.journey().activities])

    def test_cannot_remove_every_stage(self):
        response = self.fix(remove=["1", "2", "3", "4"], follow=True)
        self.assertContains(response, "au moins une étape")
        self.assertEqual(len(self.journey().legs), 4)

    def test_rename_a_stage_and_reset(self):
        self.fix(name_2="Le col de Tamié")
        j = self.journey()
        self.assertEqual(j.legs[1].label, "Le col de Tamié")
        self.assertContains(self.client.get(self.url), "Le col de Tamié")
        self.fix(name_2="")
        self.assertEqual(JourneyNote.objects.get().stage_names, {})
        self.fix(remove=["4"], name_1="Départ")
        self.client.post(self.url, {"action": "reset"})
        j = self.journey()
        self.assertEqual(len(j.legs), 4)
        self.assertFalse(j.corrected)

    def test_hide_and_unhide(self):
        response = self.client.post(self.url, {"action": "hide"})
        self.assertRedirects(response, reverse("activities:journeys"))
        listing = self.client.get(reverse("activities:journeys"))
        self.assertContains(listing, "1 périple masqué")
        self.assertEqual(self.client.get(reverse("activities:map")).context["journeys"], [])
        self.assertContains(self.client.get(self.url), "Rétablir ce périple")
        self.client.post(self.url, {"action": "unhide"})
        self.assertEqual(len(self.client.get(reverse("activities:map")).context["journeys"]), 1)

    def test_transfer_mode_follows_its_stage_after_a_removal(self):
        self.fix(add=["9"])
        self.client.post(self.url, {"transfer_index": "3", "mode": "train"})
        self.assertEqual(JourneyNote.objects.get().transfers, {"a4": "train"})
        self.fix(remove=["1"])  # les index se décalent : le mode reste sur la même étape
        j = self.journey()
        self.assertEqual(j.transfer_modes, {2: "train"})

    def test_legacy_index_transfer_modes_still_work(self):
        from .journeys import _saved_modes
        note = JourneyNote(transfers={"1": "ferry", "a3": "bus", "x": "train", "a2": "fusée"})
        legs = self.journey().legs
        self.assertEqual(_saved_modes(note, legs), {2: "ferry", 3: "bus"})

    def test_book_uses_the_corrected_journey(self):
        self.fix(remove=["4"], name_1="Grand départ")
        self.client.post(reverse("activities:book"), {"j": ["1"]})
        page = self.client.get(reverse("activities:book_detail", args=[Book.objects.get().pk]))
        self.assertContains(page, "Grand départ")
        self.assertNotContains(page, 'id="map-s4"')


class MapZoneTests(LoggedInTestCase):
    def setUp(self):
        super().setUp()
        for a in (leg(1, 10, 8, ANNECY, FAVERGES), leg(2, 11, 8, FAVERGES, ALBERTVILLE),
                  leg(3, 12, 8, ALBERTVILLE, MOUTIERS)):
            a.account = self.account
            a.summary_polyline = SAMPLE_POLYLINE
            a.save()

    def test_map_has_zone_tool_and_journey_figures(self):
        response = self.client.get(reverse("activities:map"))
        self.assertContains(response, 'id="zone-btn"')
        self.assertContains(response, f'action="{reverse("activities:book")}"')
        trip = response.context["journeys"][0]
        self.assertEqual((trip["days"], trip["stages"]), (3, [1, 2, 3]))

    def test_zone_form_creates_a_book(self):
        response = self.client.post(reverse("activities:book"), {"j": ["1"], "title": "Autour d'Annecy"})
        book = Book.objects.get()
        self.assertEqual((book.title, book.journey_ids), ("Autour d'Annecy", [1]))
        self.assertRedirects(response, reverse("activities:book_detail", args=[book.pk]), fetch_redirect_response=False)
