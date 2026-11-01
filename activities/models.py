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
    # Compte sans Strava : créé au premier dépôt de fichiers (GPX, FIT…), lié au navigateur.
    # Aucun appel à l'API Strava n'est fait pour lui (pas de jetons).
    is_local = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.is_local:
            return f"Compte sans Strava {self.pk}"
        return f"{self.firstname} {self.lastname} (Strava {self.athlete_id})".strip()

    @property
    def is_expired(self):
        # Marge de 5 minutes pour éviter qu'un jeton expire en pleine requête
        return self.expires_at <= timezone.now() + timezone.timedelta(minutes=5)


class Activity(models.Model):
    """Activité enregistrée localement : synchronisée depuis l'API Strava, ou lue dans un fichier
    déposé par l'utilisateur (GPX, FIT, TCX). Ces dernières ont un identifiant négatif."""

    account = models.ForeignKey(StravaAccount, on_delete=models.CASCADE, related_name="activities")
    strava_id = models.BigIntegerField(unique=True)
    name = models.CharField(max_length=255)
    sport_type = models.CharField(max_length=50, blank=True)
    start_date = models.DateTimeField()
    start_date_local = models.DateTimeField(null=True, blank=True)
    distance = models.FloatField(default=0, help_text="mètres")
    moving_time = models.PositiveIntegerField(default=0, help_text="secondes")
    elapsed_time = models.PositiveIntegerField(default=0, help_text="secondes")
    total_elevation_gain = models.FloatField(default=0, help_text="mètres")
    average_speed = models.FloatField(default=0, help_text="m/s")
    summary_polyline = models.TextField(blank=True)
    photo_count = models.PositiveIntegerField(default=0)
    photos_synced_at = models.DateTimeField(null=True, blank=True)
    # Récupérés à la demande (livre) : description saisie sur Strava et profil d'altitude
    description = models.TextField(blank=True)
    profile = models.JSONField(null=True, blank=True, help_text="[[km, altitude], …] sous-échantillonné")
    details_fetched_at = models.DateTimeField(null=True, blank=True)
    start_lat = models.FloatField(null=True, blank=True)
    start_lng = models.FloatField(null=True, blank=True)
    raw = models.JSONField(default=dict, blank=True)

    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]
        indexes = [models.Index(fields=["account", "-start_date"])]

    def __str__(self):
        return f"{self.name} ({self.start_date:%Y-%m-%d})"

    @property
    def from_file(self):
        return self.strava_id < 0

    @property
    def distance_km(self):
        return round(self.distance / 1000, 2)

    @property
    def duration_display(self):
        h, rem = divmod(self.moving_time, 3600)
        m = rem // 60
        return f"{h} h {m:02d}" if h else f"{m} min"

    @property
    def speed_display(self):
        """Allure (min/km) pour la course et la marche, vitesse (km/h) sinon."""
        from . import sports
        if not self.average_speed:
            return None
        if sports.uses_pace(self.sport_type):
            sec = round(1000 / self.average_speed)
            return {"value": f"{sec // 60}:{sec % 60:02d}", "unit": "/km", "label": "Allure moyenne"}
        return {"value": f"{self.average_speed * 3.6:.1f}".replace(".", ","), "unit": "km/h", "label": "Vitesse moyenne"}

    @property
    def has_route(self):
        return bool(self.summary_polyline)


class ActivityPhoto(models.Model):
    """Photo Strava rattachée à une activité."""

    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="photos")
    unique_id = models.CharField(max_length=100, unique=True)
    url = models.URLField(max_length=1000)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    caption = models.TextField(blank=True)
    taken_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["taken_at", "id"]

    def __str__(self):
        return f"Photo {self.unique_id} ({self.activity})"


class Place(models.Model):
    """Cache du géocodage inverse : nom de commune pour un point (arrondi à ~1 km)."""

    lat_key = models.DecimalField(max_digits=6, decimal_places=2)
    lng_key = models.DecimalField(max_digits=6, decimal_places=2)
    name = models.CharField(max_length=200, blank=True)  # vide : rien trouvé
    source = models.CharField(max_length=20, blank=True)
    fetched_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("lat_key", "lng_key")]

    def __str__(self):
        return f"{self.name or '?'} ({self.lat_key}, {self.lng_key})"


class JourneyNote(models.Model):
    """Titre et récit choisis par l'utilisateur pour un périple (identifié par sa 1re activité)."""

    account = models.ForeignKey(StravaAccount, on_delete=models.CASCADE, related_name="journey_notes")
    journey_id = models.BigIntegerField()
    title = models.CharField(max_length=200, blank=True)
    story = models.TextField(blank=True)
    transfers = models.JSONField(default=dict, blank=True,
                                 help_text='{"a<id de l\'étape avant le transfert>": "train"…} (ancien format : index)')
    # Corrections de l'utilisateur, appliquées par-dessus la détection automatique
    excluded = models.JSONField(default=list, blank=True, help_text="activités détectées retirées du périple")
    added = models.JSONField(default=list, blank=True, help_text="activités ajoutées à la main")
    stage_names = models.JSONField(default=dict, blank=True, help_text='{"<id activité>": "nom de l\'étape"}')
    hidden = models.BooleanField(default=False, help_text="« Ce n'est pas un périple »")
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def has_corrections(self):
        return bool(self.excluded or self.added or self.stage_names)

    class Meta:
        unique_together = [("account", "journey_id")]


class Book(models.Model):
    """Livre de voyage enregistré : périples choisis et personnalisation."""

    COVER_MAP, COVER_PHOTO = "map", "photo"

    account = models.ForeignKey(StravaAccount, on_delete=models.CASCADE, related_name="books")
    title = models.CharField(max_length=200, blank=True)
    subtitle = models.CharField(max_length=300, blank=True)
    journey_ids = models.JSONField(default=list)
    # Livre créé depuis une zone de la carte : activités choisies (un seul chapitre « sélection »)
    activity_ids = models.JSONField(default=list, blank=True)
    selection_title = models.CharField(max_length=200, blank=True)
    selection_story = models.TextField(blank=True)
    cover = models.CharField(max_length=10, default=COVER_MAP)
    cover_photo = models.CharField(max_length=30, blank=True, help_text="clé de photo (s:12 ou u:3)")
    options = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    DEFAULT_OPTIONS = {"toc": True, "roadbook": True, "profiles": True, "back": True, "basemap": "topo", "relief": False,
                       "cities": True, "imagery": "satellite"}
    IMAGERY = {"satellite", "eox"}  # Esri (usage personnel) ou Sentinel-2 2016 d'EOX (libre, vendable)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title or f"Livre {self.pk}"

    def option(self, key):
        return self.options.get(key, self.DEFAULT_OPTIONS[key])


class BookStage(models.Model):
    """Personnalisation d'une étape dans un livre (ce qui n'est pas défini garde la valeur Strava)."""

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="stages")
    activity_id = models.BigIntegerField()
    title = models.CharField(max_length=200, blank=True)
    text = models.TextField(blank=True)
    text_edited = models.BooleanField(default=False, help_text="texte saisi (même vide) au lieu de la description Strava")
    photos = models.JSONField(null=True, blank=True,
                              help_text="[{key, caption}] dans l'ordre ; null = toutes les photos Strava")

    class Meta:
        unique_together = [("book", "activity_id")]


def book_photo_path(instance, filename):
    return f"livres/{instance.book_id}/{filename}"


class BookPhoto(models.Model):
    """Photo ajoutée par l'utilisateur à une étape d'un livre."""

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="uploads")
    activity_id = models.BigIntegerField(null=True, blank=True, help_text="étape d'origine, vide si ajoutée au bac")
    image = models.ImageField(upload_to=book_photo_path, width_field="width", height_field="height")
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    caption = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def key(self):
        return f"u:{self.pk}"
