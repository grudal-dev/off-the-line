from django.conf import settings

ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/{name}/MapServer/tile/{{z}}/{{y}}/{{x}}"


def basemap(request):
    """Fonds de carte (sombre et clair) utilisés par Leaflet.

    CARTO exige une clé depuis 2026 (sinon les tuiles affichent
    « API KEY REQUIRED »). Sans clé, on bascule sur les fonds Esri
    Canvas, utilisables sans clé.
    """
    key = settings.CARTO_API_KEY
    if key:
        options = {"maxZoom": 19, "attribution": "&copy; OpenStreetMap &copy; CARTO"}
        url = "https://basemaps.cartocdn.com/rastertiles/{style}/{{z}}/{{x}}/{{y}}{{r}}.png?key=" + key
        return {"basemap": {
            "dark": {"url": url.format(style="dark_all"), "options": options},
            "light": {"url": url.format(style="light_all"), "options": options},
        }}
    options = {"maxZoom": 16, "attribution": "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ"}
    return {"basemap": {
        "dark": {"url": ESRI.format(name="World_Dark_Gray_Base"), "options": options},
        "light": {"url": ESRI.format(name="World_Light_Gray_Base"), "options": options},
    }}
