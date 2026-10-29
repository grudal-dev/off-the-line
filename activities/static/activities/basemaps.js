/* Sélecteur de fond de carte commun aux pages avec carte.
   Les fonds sont fournis par le context processor « basemap » (BASEMAP). */
(function () {
  const STORE = "otl-basemap";
  const RELIEF = "otl-relief";

  function read(key, fallback) {
    try { return localStorage.getItem(key) ?? fallback; } catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* stockage indisponible */ }
  }

  /**
   * Ajoute le sélecteur à la carte.
   * onChange(layerDef) est appelé à chaque changement de fond (et une fois au démarrage),
   * pour adapter le style des tracés au fond choisi.
   */
  window.mountBasemaps = function (map, onChange) {
    const defs = BASEMAP.layers;
    let current = read(STORE, BASEMAP.default);
    if (!defs.some(d => d.key === current)) current = BASEMAP.default;
    let relief = read(RELIEF, "0") === "1";
    let tiles = null;

    map.createPane("relief");
    map.getPane("relief").style.zIndex = 250;
    map.getPane("relief").classList.add("relief-pane");
    const hillshade = L.tileLayer(BASEMAP.hillshade.url, { ...BASEMAP.hillshade.options, pane: "relief" });

    const Control = L.Control.extend({
      options: { position: "topright" },
      onAdd() {
        const el = L.DomUtil.create("div", "basemap-picker");
        el.innerHTML =
          `<div class="basemap-choices" role="group" aria-label="Fond de carte">` +
          defs.map(d => `<button type="button" data-base="${d.key}">${d.label}</button>`).join("") +
          `</div>` +
          `<label class="relief-toggle"><input type="checkbox"> Relief</label>`;
        L.DomEvent.disableClickPropagation(el);
        L.DomEvent.disableScrollPropagation(el);
        el.querySelectorAll("[data-base]").forEach(b => b.addEventListener("click", () => setBase(b.dataset.base)));
        const box = el.querySelector("input");
        box.checked = relief;
        box.addEventListener("change", () => setRelief(box.checked));
        this._el = el;
        return el;
      },
    });
    const control = new Control().addTo(map);

    function setBase(key) {
      const def = defs.find(d => d.key === key);
      current = key;
      if (tiles) tiles.remove();
      tiles = L.tileLayer(def.url, def.options).addTo(map);
      map.getContainer().dataset.theme = def.theme;
      control._el.querySelectorAll("[data-base]").forEach(b => b.setAttribute("aria-pressed", b.dataset.base === key));
      write(STORE, key);
      onChange && onChange(def);
    }

    function setRelief(on) {
      relief = on;
      on ? hillshade.addTo(map) : hillshade.remove();
      write(RELIEF, on ? "1" : "0");
    }

    setBase(current);
    setRelief(relief);
  };
})();
