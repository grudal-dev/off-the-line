from django.urls import path, register_converter

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
    path("importer/", views.import_files, name="import"),
    path("activites/", views.activity_list, name="list"),
    path("activites/synchroniser/", views.activity_sync, name="sync"),
    path("activites/<sid:strava_id>/", views.activity_detail, name="detail"),
    path("activites/<sid:strava_id>/completer/", views.activity_complete, name="detail_complete"),
    path("carte/", views.all_routes_map, name="map"),
    path("periples/", views.journey_list, name="journeys"),
    path("periples/completer/", views.journeys_complete, name="journeys_complete"),
    path("periples/<sid:journey_id>/", views.journey_detail, name="journey"),
    path("periples/<sid:journey_id>/completer/", views.journey_complete, name="journey_complete"),
    path("affiche/", views.poster, name="poster"),
    path("livre/", views.book_builder, name="book"),
    path("livre/<int:book_id>/", views.book_detail, name="book_detail"),
    path("livre/<int:book_id>/modifier/", views.book_update, name="book_update"),
    path("livre/<int:book_id>/photos/", views.book_upload, name="book_upload"),
    path("livre/<int:book_id>/photos-strava/", views.book_fetch_photos, name="book_fetch_photos"),
    path("livre/<int:book_id>/supprimer/", views.book_delete, name="book_delete"),
]
