from django.contrib import admin

from .models import Activity, StravaAccount


@admin.register(StravaAccount)
class StravaAccountAdmin(admin.ModelAdmin):
    list_display = ("athlete_id", "firstname", "lastname", "user", "expires_at", "updated_at")
    search_fields = ("athlete_id", "firstname", "lastname")
    exclude = ("access_token", "refresh_token")


@admin.register(Activity)
class ActivityAdmin(admin.ModelAdmin):
    list_display = ("name", "sport_type", "start_date", "distance", "account")
    list_filter = ("sport_type",)
    search_fields = ("name", "strava_id")
    exclude = ("raw",)
