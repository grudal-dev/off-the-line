from django.db import migrations


def backfill(apps, schema_editor):
    """Renseigne photo_count pour les activités importées avant l'ajout des photos.

    La synchronisation est incrémentale : sans ce rattrapage, les anciennes
    activités garderaient photo_count = 0 et leurs photos ne seraient jamais importées.
    Le nombre est déjà présent dans la réponse Strava stockée (champ raw).
    """
    Activity = apps.get_model("activities", "Activity")
    for activity in Activity.objects.filter(photo_count=0).only("id", "raw").iterator():
        count = (activity.raw or {}).get("total_photo_count") or 0
        if count:
            Activity.objects.filter(pk=activity.pk).update(photo_count=count)


class Migration(migrations.Migration):
    dependencies = [("activities", "0003_photos")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
