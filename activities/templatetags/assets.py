"""{% asset 'activities/site.css' %} : comme {% static %}, avec la date de modification du fichier.

Le navigateur garde les scripts et feuilles de style en cache ; sans ce suffixe, il peut
continuer à utiliser une ancienne version après une mise à jour du site (`git pull`).
"""

import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def asset(path):
    url = static(path)
    path_on_disk = finders.find(path)
    if not path_on_disk:
        return url
    mtime = int(os.path.getmtime(path_on_disk))
    return f"{url}?v={mtime}"
