/* Livre : enregistrement, cartes des pages, impression. La page définit BOOK (adresses, options) avant ce script. */
const saveStatus = document.getElementById("save-status");
async function saveBook(payload) {
  saveStatus.textContent = "Enregistrement…";
  try {
    const r = await fetch(BOOK.update, {
      method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": BOOK.csrf },
      body: JSON.stringify(payload),
    });
    const data = await r.json();
    if (!data.ok) throw new Error(data.error || r.status);
    saveStatus.textContent = "Enregistré";
    setTimeout(() => { if (saveStatus.textContent === "Enregistré") saveStatus.textContent = ""; }, 2000);
    return true;
  } catch (e) {
    saveStatus.textContent = `Échec de l'enregistrement (${e.message})`;
    return false;
  }
}
function reloadAt(activityId, reopen) {
  try { sessionStorage.setItem("otl-reopen", reopen ? String(activityId) : ""); } catch (e) {}
  if (activityId) location.hash = "etape-" + activityId;
  else try { sessionStorage.setItem("otl-scroll", String(window.scrollY)); } catch (e) {}
  location.reload();
}

(() => {
  const maps = JSON.parse(document.getElementById("maps-data").textContent);
  const status = document.getElementById("load-status");
  // Imagerie satellite Esri (usage personnel : pas de revente sans accord écrit), Sentinel-2 2016
  // d'EOX (CC BY 4.0, vendable), photos IGN (Etalab 2.0, vendable, France) ou l'un de nos fonds vectoriels
  // (vendables), rendu en image fixe haute définition pour l'impression ; l'attribution est sur chaque carte et au dos.
  let SATELLITE = BASEMAP.layers.find(d => d.key === (BOOK.options.imagery || "satellite"))
    || BASEMAP.layers.find(d => d.key === "satellite");
  const vectorOk = SATELLITE.vector && window.OTLRender && window.maplibregl && maplibregl.supported?.() !== false;
  if (SATELLITE.vector && !vectorOk) SATELLITE = BASEMAP.layers.find(d => d.key === "eox");
  const instances = [];
  // Texte des cartes clair sur les photos et fonds sombres, foncé sur les fonds clairs
  const mapTheme = SATELLITE.theme === "light" ? "light" : "dark";

  // ---------- Villes-étapes : le nom de chaque lieu d'étape, comme sur l'affiche ----------
  const esc = s => s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const hit = (b, boxes) => boxes.some(o => b[0] < o[2] + 4 && b[2] > o[0] - 4 && b[1] < o[3] + 2 && b[3] > o[1] - 2);
  // Départ et arrivée d'abord, puis le milieu, puis les quarts… : bien répartis s'il faut en laisser tomber
  function spread(n) {
    const out = n ? [0] : [];
    if (n > 1) out.push(n - 1);
    const queue = [[0, n - 1]];
    while (queue.length) {
      const [a, b] = queue.shift();
      if (b - a < 2) continue;
      const mid = (a + b) >> 1;
      out.push(mid);
      queue.push([a, mid], [mid, b]);
    }
    return out;
  }
  // Largeur réelle du nom (mêmes police, taille et espacement que .stop-name dans book.css)
  const measure = document.createElement("canvas").getContext("2d");
  function textWidth(label, end) {
    const px = end ? 14 : 12;
    measure.font = `700 ${px}px "Barlow Condensed", "Arial Narrow", sans-serif`;
    const t = label.toUpperCase();
    return measure.measureText(t).width + t.length * px * 0.05;
  }
  // Police chargée après le premier placement : on replace les noms avec les vraies largeurs
  document.fonts && document.fonts.ready.then(() => (window.bookStops || []).forEach(f => f()));
  function mountStops(map, m, el) {
    const places = BOOK.options.stops === false ? [] : (m.places || []);
    map.createPane("stops").style.zIndex = 620;  // au-dessus du tracé et des pastilles
    if (!BOOK.edit) map.getPane("stops").style.pointerEvents = "none";
    const layer = L.layerGroup().addTo(map);
    const pins = m.stages.length > 1 && m.id !== "cover";  // pastilles numérotées au départ de chaque étape
    function render() {
      layer.clearLayers();
      const hidden = new Set(BOOK.options.cityHidden || []), names = BOOK.options.cityNames || {};
      const size = map.getSize(), boxes = [], taken = new Set();
      // Pastilles et ronds du tracé : à éviter, sauf celui du lieu qu'on nomme (le nom se pose contre)
      const marks = (map.cityAvoid || []).map(ll => map.latLngToContainerPoint(ll));
      for (const q of marks) boxes.push([q.x - 14, q.y - 14, q.x + 14, q.y + 14]);
      // Couverture : rien sous l'encadré du titre
      const block = el.parentElement.querySelector(".cover-text");
      if (block && m.id === "cover") {
        // Positions à l'écran divisées par l'échelle de la page (réduite sur téléphone)
        const r = block.getBoundingClientRect(), o = el.getBoundingClientRect(), k = o.width / el.offsetWidth || 1;
        boxes.push([(r.left - o.left) / k, (r.top - o.top) / k - 6, (r.right - o.left) / k, (r.bottom - o.top) / k]);
      }
      // Ordre de pose : départ et arrivée, puis les lieux ajoutés à la main, puis les autres étapes
      const order = spread(places.length).map(i => ({ row: places[i], end: i === 0 || i === places.length - 1, i }));
      const view = map.getBounds();
      const extras = (BOOK.options.cityExtra || []).filter(([la, ln]) => view.contains([la, ln]))
        .map(row => ({ row, extra: true }));
      const queue = [...order.filter(e => e.end), ...extras, ...order.filter(e => !e.end)];
      for (const { row: [lat, lng, name], end = false, extra = false, i } of queue) {
        const off = !extra && hidden.has(name);
        if (off && !BOOK.edit) continue;
        if (extra && taken.has(name.toLowerCase())) continue;
        const label = extra ? name : names[name] || name;
        const p = map.latLngToContainerPoint([lat, lng]);
        // À côté de la pastille d'étape (13 px de rayon), des ronds de départ et d'arrivée d'une
        // carte d'étape, ou d'un petit point posé ici (couverture, arrivée du périple, lieu ajouté)
        const single = m.stages.length === 1;
        const dot = extra || (!single && (!pins || i === places.length - 1));
        const gap = pins && !dot ? 17 : single && !extra ? 11 : 8;
        const w = textWidth(label, end) + 6, h = end ? 17 : 15;
        let spot = null;
        for (const [x, y] of [[p.x + gap, p.y - h / 2], [p.x - gap - w, p.y - h / 2], [p.x - w / 2, p.y - gap - h], [p.x - w / 2, p.y + gap]]) {
          const b = [x, y, x + w, y + h];
          if (x < 4 || y < 4 || b[2] > size.x - 4 || b[3] > size.y - 4) continue;
          if (hit(b, boxes.filter((o, k) => k >= marks.length || Math.hypot(marks[k].x - p.x, marks[k].y - p.y) > 20))) continue;
          spot = b;
          break;
        }
        if (!spot) continue;  // pas de place : on n'empile pas les noms
        boxes.push(spot);
        taken.add(name.toLowerCase());
        taken.add(label.toLowerCase());
        if (dot) {
          boxes.push([p.x - 6, p.y - 6, p.x + 6, p.y + 6]);
          L.marker([lat, lng], { pane: "stops", interactive: false, keyboard: false,
            icon: L.divIcon({ className: "", html: `<i class="stop-dot${end ? " end" : ""}${extra ? " extra" : ""}"></i>`, iconSize: [0, 0] }) }).addTo(layer);
        }
        const at = map.containerPointToLatLng([spot[0], spot[1]]);
        const mk = L.marker(at, { pane: "stops", interactive: false, keyboard: false,
          icon: L.divIcon({ className: "", iconSize: [0, 0], iconAnchor: [0, 0],
            html: `<span class="stop-name${end ? " end" : ""}${off ? " off" : ""}${extra ? " extra" : ""}" data-name="${esc(name)}"${extra ? " data-extra" : ""}`
                + `${BOOK.edit ? ` title="${off ? "Clique pour réafficher" : extra ? "Clique pour renommer ; vide pour retirer" : "Clique pour renommer ; vide pour masquer"}"` : ""}>${esc(label)}</span>` }) });
        mk.addTo(layer);
      }
      map.cityAvoidBoxes = boxes;
      map.cityAvoidNames = taken;
    }
    render();
    map.on("moveend zoomend resize", render);
    return render;
  }

  for (const m of maps) {
    const el = document.getElementById("map-" + m.id);
    if (!el) continue;
    el.dataset.theme = mapTheme;
    const map = L.map(el, {
      zoomControl: false, attributionControl: true, dragging: false, scrollWheelZoom: false,
      doubleClickZoom: false, boxZoom: false, keyboard: false, touchZoom: false, zoomSnap: 0.1,
      fadeAnimation: false, zoomAnimation: false,
    });
    map.attributionControl.setPrefix(false);
    const group = L.featureGroup().addTo(map);
    for (const s of m.stages) {
      L.polyline(s.coords, { color: "#fff", weight: 7, opacity: 0.95 }).addTo(group);
    }
    m.stages.forEach((s, i) => {
      L.polyline(s.coords, { color: s.color, weight: 3.5 }).addTo(group);
      if (m.stages.length > 1 && m.id !== "cover") {
        L.marker(s.coords[0], { icon: L.divIcon({ className: "", html: `<span class="pin pin-stage">${i + 1}</span>`, iconSize: [26, 26], iconAnchor: [13, 13] }) }).addTo(group);
      }
    });
    for (const [from, to] of (m.transfers || [])) {
      L.polyline([from, to], { color: "#ffffff", weight: 3, dashArray: "2 8", lineCap: "round" }).addTo(group);
    }
    if (m.stages.length === 1) {
      const c = m.stages[0].coords;
      L.circleMarker(c[0], { radius: 6, color: "#1c2629", weight: 3, fillColor: "#fff", fillOpacity: 1 }).addTo(group);
      L.circleMarker(c[c.length - 1], { radius: 6, color: "#fff", weight: 3, fillColor: "#1c2629", fillOpacity: 1 }).addTo(group);
    }
    map.fitBounds(group.getBounds(), { padding: m.id === "cover" ? [60, 60] : [28, 28] });
    if (m.id === "cover") map.panBy([0, 70], { animate: false });  // laisse la place au titre
    map.cityAvoid = m.stages.length > 1 && m.id !== "cover"
      ? m.stages.map(s => s.coords[0])
      : m.stages.length === 1 ? [m.stages[0].coords[0], m.stages[0].coords[m.stages[0].coords.length - 1]] : [];
    // Les villes-étapes d'abord (le sujet), puis quelques grandes villes pour se situer, autour
    const stops = mountStops(map, m, el);
    const named = BOOK.options.stops !== false && (m.places || []).length;
    let cities = null;
    if (BOOK.options.cities !== false && window.mountCities) {
      const few = m.id.startsWith("s") ? (named ? 3 : 6) : (named ? 6 : 10);
      cities = window.mountCities(map, { max: few, extraZoom: 0.5 });
    }
    (window.bookStops = window.bookStops || []).push(() => { stops && stops(); cities && cities.refresh(); });
    instances.push({ id: m.id, map, tiles: null });
    (window.bookMaps = window.bookMaps || {})[m.id] = { map, bounds: group.getBounds() };
  }

  // Les cartes doivent être chargées avant d'imprimer, sinon le PDF a des trous
  let pending = 0;
  function track(layer, timeout = 15000) {
    pending++;
    update();
    let done = false;
    const finish = () => { if (!done) { done = true; pending--; update(); } };
    layer.once("load", finish);
    setTimeout(finish, timeout);
  }
  function update() {
    status.textContent = pending ? `Chargement des cartes (${pending} restantes)…` : "Cartes prêtes";
  }
  let creditText = SATELLITE.options.attribution;
  if (vectorOk) {
    const style = OTLStyles.build(SATELLITE.vector, { print: true });
    creditText = OTLStyles.attribution(SATELLITE.vector, { print: true });
    // Une carte à la fois (rendu hors écran partagé), la couverture d'abord puis dans l'ordre des pages
    const ordered = instances.filter(i => i.id === "cover").concat(instances.filter(i => i.id !== "cover"));
    for (const inst of ordered) {
      inst.map.attributionControl.addAttribution(creditText);
      let fire = null;
      track({ once: (ev, fn) => { fire = fn; } }, 180000);
      OTLRender.leafletStill(inst.map, style, { pixelRatio: 3 }).catch(console.error).finally(() => fire && fire());
    }
  } else {
    for (const inst of instances) {
      inst.tiles = L.tileLayer(SATELLITE.url, SATELLITE.options).addTo(inst.map);
      if (SATELLITE.filter) inst.tiles.getContainer().style.filter = SATELLITE.filter;
      track(inst.tiles);
    }
  }
  const credit = document.getElementById("map-credit");
  if (credit) credit.innerHTML = creditText;

  document.getElementById("print-btn").addEventListener("click", () => {
    if ((pending || window.photosPending || window.stravaPhotosFetching) && !confirm("Des cartes ou des photos sont encore en cours de chargement. Imprimer quand même ?")) return;
    window.print();
  });
})();
