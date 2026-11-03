from django.conf import settings

ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/{path}/MapServer/tile/{{z}}/{{y}}/{{x}}"
IGN = ("https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&STYLE=normal"
       "&TILEMATRIXSET=PM&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}&LAYER={layer}&FORMAT={fmt}")
OSM = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
# Sentinel-2 cloudless 2016 d'EOX : seul millésime sous CC BY 4.0, donc utilisable pour des affiches vendues
# (attribution obligatoire). Les millésimes 2018 et suivants sont réservés à l'usage non commercial.
EOX = "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg"
EOX_CREDIT = ('<a href="https://s2maps.eu">Sentinel-2 cloudless</a> by EOX IT Services GmbH '
              '(Contains modified Copernicus Sentinel data 2016)')
# Le millésime 2016 est terne et sombre : on l'éclaircit à l'affichage (carte, affiche, livre).
EOX_FILTER = "brightness(1.3) contrast(1.1) saturate(1.2)"
# Photos aériennes de l'IGN (BD ORTHO®) : Licence Ouverte Etalab 2.0, vendable avec attribution.
# Très nettes et lumineuses, mais seulement en France (métropole et outre-mer).
IGN_ORTHO_CREDIT = '&copy; <a href="https://www.ign.fr/">IGN</a> – BD ORTHO® (Licence Ouverte Etalab 2.0)'

# Fonds vectoriels dessinés par Off the line (style : static/activities/mapstyles.js).
# Sept couleurs « graines » par style ; le reste en est dérivé. Dosages : nature, urban (0–1),
# relief (intensité par défaut de l'ombrage, 0 = sans), contours (courbes de niveau par défaut),
# casing (routes bordées), waterOutline (contour de l'eau à l'encre), upper (villes en capitales),
# roadScale (épaisseur des routes).
VECTOR_STYLES = [
    {"key": "carnet", "label": "Carnet", "relief": 0.35, "nature": 0.5, "urban": 0.55,
     "colors": {"land": "#efe5cf", "water": "#b5cac6", "nature": "#9aab7c", "urban": "#b88d69",
                "road": "#8c674b", "label": "#4a3626", "shadow": "#5e4630"}},
    {"key": "epure", "label": "Épure", "relief": 0, "nature": 0.45, "urban": 0.7, "upper": True,
     "colors": {"land": "#f4f2ed", "water": "#d2dbdf", "nature": "#bccab1", "urban": "#cfc8bc",
                "road": "#bfb8ab", "label": "#57524a", "shadow": "#5f5a52"}},
    {"key": "encre", "label": "Encre", "relief": 0, "nature": 0, "urban": 0.16, "waterOutline": True, "upper": True,
     "roadScale": 0.75,
     "colors": {"land": "#ffffff", "water": "#eeeeec", "nature": "#ffffff", "urban": "#1c1c1c",
                "road": "#1c1c1c", "label": "#1c1c1c", "shadow": "#000000"}},
    {"key": "rando", "label": "Rando", "relief": 0.55, "contours": True, "nature": 0.55, "urban": 0.7, "casing": True,
     "colors": {"land": "#f3f1e7", "water": "#95c4dc", "nature": "#7fb36c", "urban": "#d2c1aa",
                "road": "#e09a4a", "label": "#2e2e2b", "shadow": "#3b3731"}},
    {"key": "alpin", "label": "Alpin", "relief": 0.85, "contours": True, "nature": 0.38, "urban": 0.6, "upper": True,
     "colors": {"land": "#fafaf8", "water": "#a2c1d8", "nature": "#a3bb9f", "urban": "#c6c9cc",
                "road": "#8d989f", "label": "#243442", "shadow": "#22405c"}},
    {"key": "nocturne", "label": "Nocturne", "relief": 0.45, "nature": 0.45, "urban": 0.6, "upper": True,
     "colors": {"land": "#15191c", "water": "#0b0e11", "nature": "#2a3c30", "urban": "#3b454c",
                "road": "#4c5961", "label": "#a7b2b8", "shadow": "#000000"}},
    {"key": "cyanotype", "label": "Cyanotype", "relief": 0.3, "nature": 0.3, "urban": 0.22, "waterOutline": True, "upper": True,
     "roadScale": 0.8,
     "colors": {"land": "#1f4f7b", "water": "#183f63", "nature": "#2f6c99", "urban": "#e7f0f8",
                "road": "#d9e8f4", "label": "#eef5fb", "shadow": "#0a1f35"}},
]
# Crédits courts (exigés : OpenStreetMap, OpenMapTiles ; OpenFreeMap le demande ; sources du relief sur la page liée)
VECTOR_CREDIT = (f'{OSM} · &copy; <a href="https://www.openmaptiles.org/">OpenMapTiles</a> · '
                 '<a href="https://openfreemap.org">OpenFreeMap</a>')
DEM_CREDIT = 'Relief <a href="https://registry.opendata.aws/terrain-tiles/">Terrain Tiles</a>'


def vector_basemap():
    """Configuration des fonds vectoriels transmise au navigateur."""
    tiles = settings.VECTOR_TILES_URL
    credit = VECTOR_CREDIT if "openfreemap" in tiles else f"{OSM} · &copy; <a href=\"https://www.openmaptiles.org/\">OpenMapTiles</a>"
    return {
        "tiles": tiles, "glyphs": settings.VECTOR_GLYPHS_URL, "attribution": credit, "styles": VECTOR_STYLES,
        "dem": {"url": settings.DEM_TILES_URL, "encoding": "terrarium", "maxzoom": 12, "attribution": DEM_CREDIT}
        if settings.DEM_TILES_URL else None,
    }


def vector_layers(credit):
    """Une entrée du sélecteur par style vectoriel (`vector` : clé du style)."""
    return [{"key": s["key"], "label": s["label"], "vector": s["key"], "group": "otl",
             "theme": "dark" if s["key"] in ("nocturne", "cyanotype") else "light",
             "options": {"maxZoom": 19, "attribution": credit}} for s in VECTOR_STYLES]


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
         "url": EOX, "filter": EOX_FILTER,
         "options": {"maxZoom": 19, "maxNativeZoom": 14, "attribution": EOX_CREDIT}},
        {"key": "ign_ortho", "label": "Photo IGN (France)", "theme": "photo",
         "url": IGN.format(layer="ORTHOIMAGERY.ORTHOPHOTOS", fmt="image/jpeg"),
         "options": {"maxZoom": 19, "attribution": IGN_ORTHO_CREDIT}},
        {"key": "light", "label": "Clair", "theme": "light", **light},
        {"key": "dark", "label": "Nuit", "theme": "dark", **dark},
    ]
    hillshade = {
        "url": ESRI.format(path="Elevation/World_Hillshade"),
        "options": {"maxZoom": 19, "maxNativeZoom": 16, "opacity": 0.45,
                    "attribution": "Relief &copy; Esri"}, "personal": True,
    }
    vector = vector_basemap()
    return {"basemap": {"layers": vector_layers(vector["attribution"]) + layers, "hillshade": hillshade,
                        "default": "carnet", "vector": vector}}
