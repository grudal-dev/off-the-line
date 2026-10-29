from django.urls import path

from . import views

app_name = "activities"

urlpatterns = [
    path("", views.home, name="home"),
    path("strava/login/", views.strava_login, name="strava_login"),
    path("strava/callback/", views.strava_callback, name="strava_callback"),
    path("strava/logout/", views.strava_logout, name="logout"),
    path("strava/disconnect/", views.strava_disconnect, name="disconnect"),
    path("activites/", views.activity_list, name="list"),
    path("activites/synchroniser/", views.activity_sync, name="sync"),
    path("activites/<int:strava_id>/", views.activity_detail, name="detail"),
    path("carte/", views.all_routes_map, name="map"),
    path("periples/", views.journey_list, name="journeys"),
    path("periples/<int:journey_id>/", views.journey_detail, name="journey"),
    path("livre/", views.book_builder, name="book"),
    path("livre/<int:book_id>/", views.book_detail, name="book_detail"),
    path("livre/<int:book_id>/modifier/", views.book_update, name="book_update"),
    path("livre/<int:book_id>/photos/", views.book_upload, name="book_upload"),
    path("livre/<int:book_id>/supprimer/", views.book_delete, name="book_delete"),
]
