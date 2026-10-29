from django.conf import settings


def basemap(request):
    """Fond de carte sombre utilisé par Leaflet.

    CARTO exige une clé depuis 2026 (sinon les tuiles affichent
    « API KEY REQUIRED »). Sans clé, on bascule sur le fond Esri
    Dark Gray, utilisable sans clé.
    """
    key = settings.CARTO_API_KEY
    if key:
        return {"basemap": {
            "url": f"https://basemaps.cartocdn.com/rastertiles/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png?key={key}",
            "options": {"maxZoom": 19, "attribution": "&copy; OpenStreetMap &copy; CARTO"},
        }}
    return {"basemap": {
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        "options": {"maxZoom": 16, "attribution": "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ"},
    }}
