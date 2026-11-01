/* Pays et grandes villes sur les cartes, façon carnet de voyage.

   Données : cities.json (Natural Earth, domaine public), généré par scripts/build_cities.py.
   - Pays : nom en capitales espacées, sans point, à la position d'étiquette conseillée, seulement
     dans la plage de zoom où Natural Earth l'affiche (on ne voit plus « FRANCE » en pleine rue).
   - Villes : un point et le nom ; seulement celles adaptées au zoom, des plus importantes aux
     moins importantes.
   Une étiquette qui en chevaucherait une autre (ou une pastille du parcours) n'est pas posée :
   quelques repères, jamais de fouillis. */
(function () {
  // L'adresse versionnée est donnée par la page (leaflet_head.html) ; sinon, à côté de ce script
  const SRC = window.CITIES_URL
    || (document.currentScript ? document.currentScript.src.replace(/cities\.js(\?.*)?$/, "cities.json") : "");
  let data = null;
  function load() {
    if (!data) {
      data = fetch(SRC).then(r => r.json())
        // Ancien format (tableau de villes seul), au cas où le navigateur l'aurait gardé en cache
        .then(d => (Array.isArray(d) ? { cities: d, countries: [] } : d))
        .catch(() => ({ cities: [], countries: [] }));
    }
    return data;
  }

  /**
   * Affiche pays et villes sur une carte Leaflet.
   * options.max : nombre maximum de villes (défaut 18)
   * options.extraZoom : affiche des villes un peu plus petites que le conseil Natural Earth (défaut 1.3)
   * options.countries : afficher les pays (défaut true ; 12 au plus)
   * map.cityAvoid : points [lat, lng] à laisser dégagés (pastilles d'étape, arrivée…), lus à chaque rendu.
   * Renvoie { setEnabled(on) }.
   */
  window.mountCities = function (map, options = {}) {
    const max = options.max ?? 18;
    const extra = options.extraZoom ?? 1.3;
    const withCountries = options.countries ?? true;
    if (!map.getPane("cities")) {
      map.createPane("cities");
      // Sous les tracés (overlayPane = 400) : le parcours reste le sujet de la carte
      map.getPane("cities").style.zIndex = 380;
      map.getPane("cities").style.pointerEvents = "none";
    }
    const layer = L.layerGroup();
    let enabled = options.enabled ?? true;
    let places = { cities: [], countries: [] };

    const overlaps = (box, placed, mx = 6, my = 3) =>
      placed.some(b => box[0] < b[2] + mx && box[2] > b[0] - mx && box[1] < b[3] + my && box[3] > b[1] - my);

    function render() {
      layer.clearLayers();
      if (!enabled) return;
      const zoom = map.getZoom();
      const bounds = map.getBounds().pad(0.05);
      const size = map.getSize();
      // Les repères du parcours comptent comme des étiquettes déjà posées
      const placed = (map.cityAvoid || []).map(ll => {
        const q = map.latLngToContainerPoint(ll);
        return [q.x - 15, q.y - 15, q.x + 15, q.y + 15];
      });

      if (withCountries) {
        let shown = 0;
        for (const [name, lat, lng, minZoom, maxZoom] of places.countries) {
          if (shown >= 12) break;
          if (minZoom > zoom - 1) break;  // triés par zoom minimum ; un cran plus sobre que Natural Earth
          if (zoom > maxZoom || !bounds.contains([lat, lng])) continue;
          const p = map.latLngToContainerPoint([lat, lng]);
          const w = name.length * 12.5 + 8, h = 18;
          const box = [p.x - w / 2, p.y - h / 2, p.x + w / 2, p.y + h / 2];
          if (box[0] < 0 || box[2] > size.x || overlaps(box, placed, 10, 6)) continue;
          placed.push(box);
          shown++;
          add(lat, lng, `<span class="country">${escapeHtml(name)}</span>`);
        }
      }

      let count = 0;
      for (const [name, lat, lng, minZoom, pop, capital] of places.cities) {
        if (count >= max) break;
        if (minZoom > zoom + extra) break;  // triées par zoom minimum : les suivantes aussi
        if (!bounds.contains([lat, lng])) continue;
        const p = map.latLngToContainerPoint([lat, lng]);
        const big = capital || pop >= 1000;
        const w = name.length * (big ? 10 : 7.4) + 16, h = big ? 20 : 18;
        // Étiquette à droite du point ; à gauche si elle sortirait de la carte
        const right = p.x + w < size.x - 4;
        const box = right ? [p.x - 6, p.y - h / 2, p.x + w, p.y + h / 2] : [p.x - w, p.y - h / 2, p.x + 6, p.y + h / 2];
        if (overlaps(box, placed)) continue;
        placed.push(box);
        count++;
        add(lat, lng, `<span class="city${big ? " big" : ""}${right ? "" : " left"}"><i></i><b>${escapeHtml(name)}</b></span>`);
      }
    }

    function add(lat, lng, html) {
      L.marker([lat, lng], {
        pane: "cities", interactive: false, keyboard: false,
        icon: L.divIcon({ className: "", html, iconSize: [0, 0], iconAnchor: [0, 0] }),
      }).addTo(layer);
    }

    load().then(d => { places = d; render(); });
    layer.addTo(map);
    map.on("moveend zoomend resize", render);
    return {
      setEnabled(on) { enabled = on; render(); },
    };
  };

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
})();
