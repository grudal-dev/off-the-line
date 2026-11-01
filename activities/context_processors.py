from django.conf import settings

ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/{path}/MapServer/tile/{{z}}/{{y}}/{{x}}"
IGN = ("https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&STYLE=normal"
       "&TILEMATRIXSET=PM&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}&LAYER={layer}&FORMAT={fmt}")
OSM = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
# Sentinel-2 cloudless 2016 d'EOX : seul millésime sous CC BY 4.0, donc utilisable pour des affiches vendues
# (attribution obligatoire). Les millésimes 2018 et suivants sont réservés à l'usage non commercial.
EOX = "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg"
EOX_CREDIT = ('Sentinel-2 cloudless – <a href="https://s2maps.eu">s2maps.eu</a> by EOX IT Services GmbH '
              '(Contains modified Copernicus Sentinel data 2016)')


def basemap(request):
    """Fonds de carte proposés par le sélecteur Leaflet (voir static/activities/basemaps.js).

    Tous fonctionnent sans clé, sauf CARTO qui n'est utilisé pour les fonds
    « Clair » et « Nuit » que si CARTO_API_KEY est renseignée (sinon Esri Canvas).
    `personal` : fond réservé à un usage personnel (Esri : revente interdite sans accord écrit),
    signalé dans l'affiche et le livre.
    """
    key = settings.CARTO_API_KEY
    if key:
        carto = "https://basemaps.cartocdn.com/rastertiles/{style}/{{z}}/{{x}}/{{y}}{{r}}.png?key=" + key
        carto_opts = {"maxZoom": 19, "attribution": f"{OSM} &copy; CARTO"}
        light = {"url": carto.format(style="voyager"), "options": carto_opts}
        dark = {"url": carto.format(style="dark_all"), "options": carto_opts}
    else:
        esri_opts = {"maxZoom": 16, "attribution": "Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ"}
        light = {"url": ESRI.format(path="Canvas/World_Light_Gray_Base"), "options": esri_opts, "personal": True}
        dark = {"url": ESRI.format(path="Canvas/World_Dark_Gray_Base"), "options": esri_opts, "personal": True}

    layers = [
        {"key": "topo", "label": "Topo", "theme": "color",
         "url": "https://tile.openmaps.fr/opentopomap/{z}/{x}/{y}.png",
         "options": {"maxZoom": 17, "attribution":
                     f'&copy; <a href="https://github.com/sletuffe/OpenTopoMap">OpenTopoMap-R</a> {OSM}'}},
        {"key": "ign", "label": "IGN", "theme": "color",
         "url": IGN.format(layer="GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2", fmt="image/png"),
         "options": {"maxZoom": 19, "attribution": '&copy; <a href="https://www.ign.fr/">IGN</a>'}},
        {"key": "satellite", "label": "Satellite", "theme": "photo", "personal": True,
         "url": ESRI.format(path="World_Imagery"),
         "options": {"maxZoom": 19, "maxNativeZoom": 18, "attribution": "Imagery &copy; Esri, Maxar, Earthstar Geographics"}},
        {"key": "eox", "label": "Satellite libre", "theme": "photo",
         "url": EOX, "options": {"maxZoom": 19, "maxNativeZoom": 14, "attribution": EOX_CREDIT}},
        {"key": "light", "label": "Clair", "theme": "light", **light},
        {"key": "dark", "label": "Nuit", "theme": "dark", **dark},
    ]
    hillshade = {
        "url": ESRI.format(path="Elevation/World_Hillshade"),
        "options": {"maxZoom": 19, "maxNativeZoom": 16, "opacity": 0.45,
                    "attribution": "Relief &copy; Esri"}, "personal": True,
    }
    return {"basemap": {"layers": layers, "hillshade": hillshade, "default": "topo"}}
