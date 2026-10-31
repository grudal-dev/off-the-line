from django.db import migrations


def recheck(apps, schema_editor):
    """Les vidéos Strava étaient importées comme des photos (leur vignette).

    On ne peut pas les reconnaître après coup : les activités qui ont des photos seront
    vérifiées à nouveau à leur prochain affichage, ce qui retire les vidéos.
    """
    Activity = apps.get_model("activities", "Activity")
    Activity.objects.filter(photos__isnull=False).distinct().update(photos_synced_at=None)


class Migration(migrations.Migration):
    dependencies = [("activities", "0010_journey_corrections")]
    operations = [migrations.RunPython(recheck, migrations.RunPython.noop)]
