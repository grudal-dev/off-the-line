from django.contrib import admin

from .models import Activity, RemovedActivity, StravaAccount, WebhookEvent


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


@admin.register(RemovedActivity)
class RemovedActivityAdmin(admin.ModelAdmin):
    list_display = ("name", "strava_id", "account", "removed_at")
    search_fields = ("name", "strava_id")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("received_at", "object_type", "aspect_type", "object_id", "owner_id", "processed_at", "attempts", "error")
    list_filter = ("object_type", "aspect_type")
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]
