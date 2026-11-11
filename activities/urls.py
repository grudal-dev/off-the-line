from django.urls import path, register_converter
from django.views.generic import RedirectView

from . import views

class SignedIntConverter:
    """Entier éventuellement négatif : les activités importées de fichiers ont un identifiant négatif."""

    regex = "-?[0-9]+"

    def to_python(self, value):
        return int(value)

    def to_url(self, value):
        return str(value)


register_converter(SignedIntConverter, "sid")

app_name = "activities"

urlpatterns = [
    path("", views.home, name="home"),
    path("strava/login/", views.strava_login, name="strava_login"),
    path("strava/callback/", views.strava_callback, name="strava_callback"),
    path("strava/logout/", views.strava_logout, name="logout"),
    path("strava/disconnect/", views.strava_disconnect, name="disconnect"),
    path("strava/webhook/", views.strava_webhook, name="strava_webhook"),
    path("importer/", views.import_files, name="import"),
    path("activites/", views.activity_list, name="list"),
    path("activites/synchroniser/", views.activity_sync, name="sync"),
    path("activites/synchroniser/auto/", views.activity_sync_auto, name="sync_auto"),
    path("activites/<sid:strava_id>/", views.activity_detail, name="detail"),
    path("activites/<sid:strava_id>/completer/", views.activity_complete, name="detail_complete"),
    path("activites/<sid:strava_id>/retirer/", views.activity_remove, name="remove"),
    path("activites/<sid:strava_id>/remettre/", views.activity_restore, name="restore"),
    path("activites/<sid:strava_id>/garder/", views.activity_keep, name="keep"),
    path("carte/", views.all_routes_map, name="map"),
    # Anciennes pages (liste des périples, affiche non enregistrée, création de livre) : tout passe par Explorer
    path("periples/", RedirectView.as_view(pattern_name="activities:map"), name="journeys"),
    path("periples/completer/", views.journeys_complete, name="journeys_complete"),
    path("periples/<sid:journey_id>/", views.journey_detail, name="journey"),
    path("periples/<sid:journey_id>/completer/", views.journey_complete, name="journey_complete"),
    path("periples/<sid:journey_id>/prefere/", views.journey_favorite, name="journey_favorite"),
    path("periples/<sid:journey_id>/ignorer/", views.suggestion_dismiss, name="suggestion_dismiss"),
    path("affiche/", RedirectView.as_view(pattern_name="activities:creations"), name="poster"),
    path("affiche/villes/", views.poster_towns, name="poster_towns"),
    path("affiche/<int:poster_id>/", views.poster_edit, name="poster_edit"),
    path("affiche/<int:poster_id>/enregistrer/", views.poster_save, name="poster_save"),
    path("affiche/<int:poster_id>/supprimer/", views.poster_delete, name="poster_delete"),
    path("creer/", views.create, name="create"),
    path("creations/", views.creations, name="creations"),
    path("livre/", RedirectView.as_view(pattern_name="activities:creations"), name="book"),
    path("livre/<int:book_id>/", views.book_detail, name="book_detail"),
    path("livre/<int:book_id>/modifier/", views.book_update, name="book_update"),
    path("livre/<int:book_id>/photos/", views.book_upload, name="book_upload"),
    path("livre/<int:book_id>/photos-strava/", views.book_fetch_photos, name="book_fetch_photos"),
    path("livre/<int:book_id>/supprimer/", views.book_delete, name="book_delete"),
]
