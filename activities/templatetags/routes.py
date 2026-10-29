from django import template

from .. import geo, polyline, sports

register = template.Library()


@register.filter
def route_path(encoded):
    """Chemin SVG miniature à partir d'une polyligne Strava encodée."""
    return geo.svg_path(polyline.decode(encoded)) if encoded else ""


@register.filter
def sport_label(sport_type):
    return sports.label(sport_type)


@register.filter
def sport_color(sport_type):
    return sports.color(sport_type)
