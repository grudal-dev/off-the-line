/* Sélecteur de fond de carte commun aux pages avec carte.
   Les fonds sont fournis par le context processor « basemap » (BASEMAP) : nos styles vectoriels
   (dessinés par mapstyles.js, affichés avec MapLibre) puis les fonds en images (IGN, satellite…). */
(function () {
  const STORE = "otl-basemap";
  const RELIEF = "otl-relief";
  const VRELIEF = "otl-vrelief";  // relief des styles vectoriels (activé par défaut)
  const CITIES = "otl-cities";

  function read(key, fallback) {
    try { return localStorage.getItem(key) ?? fallback; } catch (e) { return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* stockage indisponible */ }
  }
  const canVector = () => !!(window.OTLStyles && L.maplibreGL && window.maplibregl && maplibregl.supported?.() !== false);

  /**
   * Attribution repliable : affichée au chargement, repliée derrière un bouton (i) dès qu'on
   * bouge la carte ou au bout de cinq secondes (règles d'attribution d'OpenStreetMap).
   */
  function compactAttribution(map) {
    const ctl = map.attributionControl;
    if (!ctl) return;
    const el = ctl.getContainer();
    el.classList.add("attr-compact");
    const btn = L.DomUtil.create("button", "attr-toggle");
    btn.type = "button"; btn.textContent = "i"; btn.title = "Sources de la carte";
    btn.setAttribute("aria-label", "Sources de la carte");
    btn.addEventListener("click", () => el.classList.toggle("is-collapsed"));
    // Leaflet réécrit tout le contenu à chaque changement de fond : on remet le texte dans un
    // <span> et le bouton à côté
    const update = ctl._update;
    ctl._update = function () {
      update.call(this);
      const span = document.createElement("span");
      span.className = "attr-text";
      span.innerHTML = el.innerHTML;
      el.replaceChildren(span, btn);
    };
    ctl._update();
    L.DomEvent.disableClickPropagation(el);
    const collapse = () => el.classList.add("is-collapsed");
    setTimeout(collapse, 5000);
    setTimeout(() => map.once("movestart zoomstart", collapse), 1500);  // pas le cadrage initial
  }

  /**
   * Ajoute le sélecteur à la carte.
   * onChange(layerDef) est appelé à chaque changement de fond (et une fois au démarrage),
   * pour adapter le style des tracés au fond choisi.
   */
  window.mountBasemaps = function (map, onChange) {
    const vectorOk = canVector();
    const defs = BASEMAP.layers.filter(d => !d.vector || vectorOk);
    const ours = defs.filter(d => d.vector), others = defs.filter(d => !d.vector);
    let current = read(STORE, BASEMAP.default);
    if (!defs.some(d => d.key === current)) current = defs.some(d => d.key === BASEMAP.default) ? BASEMAP.default : "topo";
    let relief = read(RELIEF, "0") === "1";
    let vrelief = read(VRELIEF, "1") === "1";
    let citiesOn = read(CITIES, "1") === "1";
    const cities = window.mountCities ? window.mountCities(map, { enabled: citiesOn }) : null;
    let tiles = null, def = null, vAttr = null;
    // Attribution des styles vectoriels gérée ici : elle change avec le relief
    function setVectorAttribution(text) {
      const ctl = map.attributionControl;
      if (!ctl) return;
      if (vAttr) ctl.removeAttribution(vAttr);
      vAttr = text;
      if (vAttr) ctl.addAttribution(vAttr);
    }

    map.createPane("relief");
    map.getPane("relief").style.zIndex = 250;
    map.getPane("relief").classList.add("relief-pane");
    const hillshade = L.tileLayer(BASEMAP.hillshade.url, { ...BASEMAP.hillshade.options, pane: "relief" });

    const swatch = d => {
      const s = OTLStyles.styles.find(x => x.key === d.vector);
      const c = s ? s.colors : {};
      return `<i class="swatch" style="--a:${c.land};--b:${c.water};--c:${c.road}"></i>`;
    };
    const Control = L.Control.extend({
      options: { position: "topright" },
      onAdd() {
        const el = L.DomUtil.create("div", "basemap-picker");
        const button = d => `<button type="button" data-base="${d.key}">${d.vector ? swatch(d) : ""}${d.label}</button>`;
        el.innerHTML =
          `<details class="basemap-menu"><summary aria-label="Fond de carte"><span data-current></span></summary>` +
          `<div class="basemap-choices" role="group" aria-label="Fond de carte">` +
          (ours.length ? `<p>Cartes Off the line</p><div class="basemap-grid">${ours.map(button).join("")}</div>` : "") +
          (others.length ? `<p>Autres fonds</p><div class="basemap-grid">${others.map(button).join("")}</div>` : "") +
          `</div></details>` +
          `<label class="relief-toggle"><input type="checkbox" data-toggle="relief"> Relief</label>` +
          (window.mountCities ? `<label class="relief-toggle"><input type="checkbox" data-toggle="cities"> Villes</label>` : "");
        L.DomEvent.disableClickPropagation(el);
        L.DomEvent.disableScrollPropagation(el);
        const menu = el.querySelector(".basemap-menu");
        el.querySelectorAll("[data-base]").forEach(b => b.addEventListener("click", () => { setBase(b.dataset.base); menu.open = false; }));
        document.addEventListener("click", e => { if (menu.open && !menu.contains(e.target)) menu.open = false; });
        const box = el.querySelector("[data-toggle=relief]");
        box.addEventListener("change", () => setRelief(box.checked));
        const cityBox = el.querySelector("[data-toggle=cities]");
        if (cityBox) {
          cityBox.checked = citiesOn;
          cityBox.addEventListener("change", () => { citiesOn = cityBox.checked; write(CITIES, citiesOn ? "1" : "0"); refreshLabels(); });
        }
        this._el = el;
        return el;
      },
    });
    const control = new Control().addTo(map);
    compactAttribution(map);

    function vectorOptions() {
      const s = OTLStyles.styles.find(x => x.key === def.vector) || {};
      return { labels: citiesOn, relief: vrelief ? (s.relief || 0.5) : 0, contours: vrelief && !!s.contours };
    }
    function vectorAttribution() {
      return OTLStyles.attribution(def.vector, vectorOptions());
    }

    function setBase(key, remember = true) {
      def = defs.find(d => d.key === key);
      current = key;
      if (tiles) tiles.remove();
      if (def.vector) {
        try {
          tiles = L.maplibreGL({ style: OTLStyles.build(def.vector, vectorOptions()), maxZoom: 22 });
          tiles.addTo(map);
        } catch (err) {
          // WebGL indisponible (carte graphique, navigateur) : on revient au fond en images
          console.error("Fond vectoriel indisponible", err);
          return fallback(key);
        }
        setVectorAttribution(vectorAttribution());
        watchVector(tiles, key);
      } else {
        setVectorAttribution(null);
        tiles = L.tileLayer(def.url, def.options).addTo(map);
        if (def.filter) tiles.getContainer().style.filter = def.filter;  // ex. Sentinel-2 éclairci
      }
      map.getContainer().dataset.theme = def.theme;
      const el = control._el;
      el.querySelectorAll("[data-base]").forEach(b => b.setAttribute("aria-pressed", b.dataset.base === key));
      el.querySelector("[data-current]").innerHTML = (def.vector ? swatch(def) : "") + def.label;
      el.querySelector("[data-toggle=relief]").checked = def.vector ? vrelief : relief;
      if (remember) write(STORE, key);
      applyRelief();
      if (cities) cities.setEnabled(citiesOn && !def.vector);
      onChange && onChange(def);
    }

    // Si le style ne se charge pas (WebGL bloqué, erreur), on bascule sur le fond en images Topo
    function fallback(key) {
      const raster = defs.find(d => !d.vector && d.key === "topo") || defs.find(d => !d.vector);
      if (tiles) { try { tiles.remove(); } catch (e) { /* déjà retiré */ } tiles = null; }
      ours.forEach(d => { const b = control._el.querySelector(`[data-base="${d.key}"]`); if (b) b.disabled = true; });
      if (raster) setBase(raster.key, false);
    }
    function watchVector(layer, key) {
      const check = () => {
        if (tiles !== layer) return;
        if (document.visibilityState !== "visible") return setTimeout(check, 4000);  // onglet caché : on attend
        const m = layer.getMaplibreMap && layer.getMaplibreMap();
        if (!m || !m.isStyleLoaded()) { console.error("Fond vectoriel non chargé :", key); fallback(key); }
      };
      setTimeout(check, 12000);
    }

    // Style vectoriel : relief et noms font partie du style, que l'on redessine
    function restyle() {
      if (!def.vector || !tiles) return;
      const m = tiles.getMaplibreMap && tiles.getMaplibreMap();
      if (m) m.setStyle(OTLStyles.build(def.vector, vectorOptions()), { diff: true });
      setVectorAttribution(vectorAttribution());
    }
    function refreshLabels() {
      // Sur nos styles, les noms de lieux viennent de la carte elle-même
      if (cities) cities.setEnabled(citiesOn && !(def && def.vector));
      restyle();
    }
    function applyRelief() {
      const raster = relief && def && !def.vector;
      raster ? hillshade.addTo(map) : hillshade.remove();
    }
    function setRelief(on) {
      if (def.vector) { vrelief = on; write(VRELIEF, on ? "1" : "0"); restyle(); }
      else { relief = on; write(RELIEF, on ? "1" : "0"); applyRelief(); }
    }

    setBase(current);
  };
})();
