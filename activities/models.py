from django.conf import settings
from django.db import models
from django.utils import timezone


class StravaAccount(models.Model):
    """Lien entre un utilisateur Django et son compte Strava (jetons OAuth)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="strava"
    )
    athlete_id = models.BigIntegerField(unique=True)
    firstname = models.CharField(max_length=100, blank=True)
    lastname = models.CharField(max_length=100, blank=True)
    profile_picture = models.URLField(max_length=500, blank=True)

    access_token = models.CharField(max_length=255)
    refresh_token = models.CharField(max_length=255)
    expires_at = models.DateTimeField()
    scope = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.firstname} {self.lastname} (Strava {self.athlete_id})".strip()

    @property
    def is_expired(self):
        # Marge de 5 minutes pour éviter qu'un jeton expire en pleine requête
        return self.expires_at <= timezone.now() + timezone.timedelta(minutes=5)
