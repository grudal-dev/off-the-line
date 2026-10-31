/* Grandes villes sur les cartes, façon carnet de voyage : un point discret et le nom.

   Données : cities.json (Natural Earth, domaine public), généré par scripts/build_cities.py.
   Chaque ville a un « zoom minimum » conseillé : on n'affiche que celles adaptées au zoom
   courant, des plus importantes aux moins importantes, et on écarte celles dont l'étiquette
   chevaucherait une étiquette déjà posée. Résultat : quelques repères, jamais de fouillis. */
(function () {
  // cities.json est servi à côté de ce script
  const SRC = document.currentScript ? document.currentScript.src.replace(/cities\.js(\?.*)?$/, "cities.json") : "";
  let data = null;
  function load() {
    if (!data) data = fetch(SRC).then(r => r.json()).catch(() => []);
    return data;
  }

  /**
   * Affiche les villes sur une carte Leaflet.
   * options.max : nombre maximum d'étiquettes (défaut 18)
   * options.extraZoom : affiche des villes un peu plus petites que le conseil Natural Earth (défaut 1)
   * map.cityAvoid : points [lat, lng] à laisser dégagés (pastilles d'étape, arrivée…), lus à chaque rendu.
   * Renvoie { setEnabled(on) }.
   */
  window.mountCities = function (map, options = {}) {
    const max = options.max ?? 18;
    const extra = options.extraZoom ?? 1.3;
    if (!map.getPane("cities")) {
      map.createPane("cities");
      // Sous les tracés (overlayPane = 400) : le parcours reste le sujet de la carte
      map.getPane("cities").style.zIndex = 380;
      map.getPane("cities").style.pointerEvents = "none";
    }
    const layer = L.layerGroup();
    let enabled = options.enabled ?? true;
    let rows = [];

    function render() {
      layer.clearLayers();
      if (!enabled || !rows.length) return;
      const zoom = map.getZoom();
      const bounds = map.getBounds().pad(0.05);
      const size = map.getSize();
      // Les repères du parcours comptent comme des étiquettes déjà posées
      const placed = (map.cityAvoid || []).map(ll => {
        const q = map.latLngToContainerPoint(ll);
        return [q.x - 15, q.y - 15, q.x + 15, q.y + 15];
      });
      const reserved = placed.length;
      for (const [name, lat, lng, minZoom, pop, capital] of rows) {
        if (placed.length - reserved >= max) break;
        if (minZoom > zoom + extra) break;  // triées par zoom minimum : les suivantes aussi
        if (!bounds.contains([lat, lng])) continue;
        const p = map.latLngToContainerPoint([lat, lng]);
        const big = capital || pop >= 1000;
        const w = name.length * (big ? 7.4 : 6.6) + 14, h = big ? 18 : 16;
        // Étiquette à droite du point ; à gauche si elle sortirait de la carte
        const right = p.x + w < size.x - 4;
        const box = right ? [p.x - 5, p.y - h / 2, p.x + w, p.y + h / 2] : [p.x - w, p.y - h / 2, p.x + 5, p.y + h / 2];
        if (placed.some(b => box[0] < b[2] + 6 && box[2] > b[0] - 6 && box[1] < b[3] + 3 && box[3] > b[1] - 3)) continue;
        placed.push(box);
        L.marker([lat, lng], {
          pane: "cities", interactive: false, keyboard: false,
          icon: L.divIcon({
            className: "",
            html: `<span class="city${big ? " big" : ""}${right ? "" : " left"}"><i></i><b>${escapeHtml(name)}</b></span>`,
            iconSize: [0, 0], iconAnchor: [0, 0],
          }),
        }).addTo(layer);
      }
    }

    load().then(d => { rows = d; render(); });
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
