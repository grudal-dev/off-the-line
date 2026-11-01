/* Sélecteur de fond de carte pour la carte en globe (MapLibre, page Carte).
   Mêmes fonds, mêmes préférences enregistrées que basemaps.js (Leaflet, autres pages). */
(function () {
  const STORE = "otl-basemap";
  const RELIEF = "otl-relief";
  const CITIES = "otl-cities";

  function read(key, fallback) {
    try { return localStorage.getItem(key) ?? fallback; } catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* stockage indisponible */ }
  }

  // Modèle d'adresse Leaflet → source raster MapLibre
  function rasterSource(def) {
    const o = def.options || {};
    const url = def.url.replace("{r}", window.devicePixelRatio > 1 ? "@2x" : "");
    const subs = o.subdomains || "abc";
    const tiles = url.includes("{s}") ? [...subs].map(s => url.replace("{s}", s)) : [url];
    return { type: "raster", tiles, tileSize: 256, maxzoom: o.maxNativeZoom ?? o.maxZoom ?? 19, attribution: o.attribution || "" };
  }

  /**
   * Ajoute le sélecteur à une carte MapLibre (style déjà chargé).
   * onChange(layerDef) est appelé à chaque changement de fond (et une fois au démarrage).
   * Le fond et le relief sont toujours placés sous les autres couches.
   */
  window.mountBasemapsGlobe = function (map, onChange) {
    const defs = BASEMAP.layers;
    let current = read(STORE, BASEMAP.default);
    if (!defs.some(d => d.key === current)) current = BASEMAP.default;
    let relief = read(RELIEF, "0") === "1";
    let citiesOn = read(CITIES, "1") === "1";
    const cities = window.mountCities ? window.mountCities(map, { enabled: citiesOn }) : null;
    let el = null;

    // Ordre : fond uni (« bg », s'il existe), fond de carte, relief, puis le reste
    const before = (...skip) => (map.getStyle().layers.find(l => !skip.includes(l.id)) || {}).id;

    map.addControl({
      onAdd() {
        el = document.createElement("div");
        el.className = "maplibregl-ctrl basemap-picker";
        el.innerHTML =
          `<div class="basemap-choices" role="group" aria-label="Fond de carte">` +
          defs.map(d => `<button type="button" data-base="${d.key}">${d.label}</button>`).join("") +
          `</div>` +
          `<label class="relief-toggle"><input type="checkbox" data-toggle="relief"> Relief</label>` +
          (cities ? `<label class="relief-toggle"><input type="checkbox" data-toggle="cities"> Villes</label>` : "");
        el.querySelectorAll("[data-base]").forEach(b => b.addEventListener("click", () => setBase(b.dataset.base)));
        const box = el.querySelector("[data-toggle=relief]");
        box.checked = relief;
        box.addEventListener("change", () => setRelief(box.checked));
        const cityBox = el.querySelector("[data-toggle=cities]");
        if (cityBox) {
          cityBox.checked = citiesOn;
          cityBox.addEventListener("change", () => { citiesOn = cityBox.checked; write(CITIES, citiesOn ? "1" : "0"); cities.setEnabled(citiesOn); });
        }
        return el;
      },
      onRemove() { el.remove(); },
    }, "top-right");

    function setBase(key) {
      const def = defs.find(d => d.key === key);
      current = key;
      if (map.getLayer("base")) map.removeLayer("base");
      if (map.getSource("base")) map.removeSource("base");
      map.addSource("base", rasterSource(def));
      map.addLayer({ id: "base", type: "raster", source: "base", paint: { "raster-fade-duration": 200 } }, before("bg"));
      map.getContainer().dataset.theme = def.theme;
      if (map.getLayer("relief")) {
        map.setPaintProperty("relief", "raster-opacity", def.theme === "dark" ? 0.3 : BASEMAP.hillshade.options.opacity ?? 0.45);
      }
      el.querySelectorAll("[data-base]").forEach(b => b.setAttribute("aria-pressed", b.dataset.base === key));
      write(STORE, key);
      onChange && onChange(def);
    }

    function setRelief(on) {
      relief = on;
      if (on && !map.getLayer("relief")) {
        if (!map.getSource("relief")) map.addSource("relief", rasterSource(BASEMAP.hillshade));
        const dark = map.getContainer().dataset.theme === "dark";
        map.addLayer({ id: "relief", type: "raster", source: "relief",
                       paint: { "raster-opacity": dark ? 0.3 : BASEMAP.hillshade.options.opacity ?? 0.45 } }, before("bg", "base"));
      } else if (!on && map.getLayer("relief")) {
        map.removeLayer("relief");
      }
      write(RELIEF, on ? "1" : "0");
    }

    setBase(current);
    setRelief(relief);
  };
})();
