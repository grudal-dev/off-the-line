from django.contrib import admin

from .models import StravaAccount


@admin.register(StravaAccount)
class StravaAccountAdmin(admin.ModelAdmin):
    list_display = ("athlete_id", "firstname", "lastname", "user", "expires_at", "updated_at")
    search_fields = ("athlete_id", "firstname", "lastname")
    exclude = ("access_token", "refresh_token")
