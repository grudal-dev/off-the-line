/* Rendu hors écran des fonds vectoriels, pour l'affiche et le livre.

   Une seule carte MapLibre invisible sert à toute la page (un navigateur n'accepte qu'une
   quinzaine de contextes WebGL) : on lui demande une zone, elle la dessine morceau par morceau
   et l'on recopie chaque morceau dans un <canvas> 2D ordinaire, que l'affiche ou le livre
   utilisent comme une image.

   Les morceaux s'assemblent sans couture parce que les styles d'impression n'ont pas de texte
   (l'étiquetage dépend du cadre). Les coordonnées « monde » sont en Web Mercator, entre 0 et 1. */
(() => {
  if (!window.maplibregl || !window.OTLStyles) return;

  const MAX_CHUNK = 2048;  // pixels réels par morceau (largeur et hauteur)
  let map = null, box = null, styleKey = null, queue = Promise.resolve();

  const lngOf = wx => wx * 360 - 180;
  const latOf = wy => Math.atan(Math.sinh(Math.PI * (1 - 2 * wy))) * 180 / Math.PI;

  function ensureMap() {
    if (map) return map;
    box = document.createElement("div");
    box.setAttribute("aria-hidden", "true");
    box.style.cssText = "position:fixed;left:-10000px;top:0;width:256px;height:256px;pointer-events:none;visibility:hidden";
    document.body.appendChild(box);
    map = new maplibregl.Map({
      container: box, style: { version: 8, sources: {}, layers: [] }, interactive: false, attributionControl: false,
      preserveDrawingBuffer: true, fadeDuration: 0, minZoom: -2, maxZoom: 22, maxPitch: 0,
      maxCanvasSize: [8192, 8192], pixelRatio: 1, renderWorldCopies: true, trackResize: false,
    });
    return map;
  }

  // Attend que la vue soit entièrement dessinée (« idle »), avec une vérification régulière en
  // secours : sans tuile à charger (hors des données), l'événement peut ne jamais venir.
  function idle(m, timeout = 30000) {
    return new Promise(resolve => {
      let done = false;
      const finish = ok => { if (!done) { done = true; clearTimeout(timer); clearInterval(poll); resolve(ok); } };
      const timer = setTimeout(() => finish(false), timeout);
      const poll = setInterval(() => {
        if (m.loaded() && m.areTilesLoaded() && m.isStyleLoaded()) requestAnimationFrame(() => requestAnimationFrame(() => finish(true)));
      }, 300);
      m.once("idle", () => finish(true));
      m.triggerRepaint();
    });
  }

  async function setStyle(style) {
    const m = ensureMap();
    const key = JSON.stringify(style);
    if (key === styleKey) return;
    styleKey = key;
    m.setStyle(style, { diff: true });
    if (!m.isStyleLoaded()) await new Promise(r => m.once("styledata", r));
  }

  /**
   * Dessine la zone monde [wx0, wy0] – [wx1, wy1] au niveau de zoom `zoom` (taille des objets en
   * pixels CSS) avec `pixelRatio` pixels réels par pixel CSS. Renvoie { canvas, wx0, wy0, wx1, wy1, complete }.
   * Les appels sont mis en file : un seul rendu à la fois.
   */
  function renderWorld(job) {
    const run = queue.then(() => doRender(job));
    queue = run.catch(() => {});
    return run;
  }

  async function doRender({ style, wx0, wy0, wx1, wy1, zoom, pixelRatio, onProgress, isStale }) {
    const m = ensureMap();
    await setStyle(style);
    const worldPx = 512 * 2 ** zoom;  // pixels CSS pour tout le monde
    const cssW = Math.max(1, Math.ceil((wx1 - wx0) * worldPx)), cssH = Math.max(1, Math.ceil((wy1 - wy0) * worldPx));
    const pr = Math.max(0.25, pixelRatio);
    const out = document.createElement("canvas");
    out.width = Math.max(1, Math.round(cssW * pr)); out.height = Math.max(1, Math.round(cssH * pr));
    const ctx = out.getContext("2d");
    const chunk = Math.max(64, Math.floor(MAX_CHUNK / pr));  // côté d'un morceau, en pixels CSS
    const cols = Math.ceil(cssW / chunk), rows = Math.ceil(cssH / chunk);
    if (m.getPixelRatio() !== pr) m.setPixelRatio(pr);
    let complete = true, n = 0;
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        if (isStale && isStale()) return null;
        const x0 = c * chunk, y0 = r * chunk;
        const w = Math.min(chunk, cssW - x0), h = Math.min(chunk, cssH - y0);
        if (box.style.width !== w + "px" || box.style.height !== h + "px") {
          box.style.width = w + "px"; box.style.height = h + "px";
          m.resize();
        }
        const cx = wx0 + (x0 + w / 2) / worldPx, cy = wy0 + (y0 + h / 2) / worldPx;
        m.jumpTo({ center: [lngOf(cx), latOf(Math.min(0.9999, Math.max(0.0001, cy)))], zoom, bearing: 0, pitch: 0 });
        complete = (await idle(m)) && complete;
        const src = m.getCanvas();
        ctx.drawImage(src, 0, 0, src.width, src.height, Math.round(x0 * pr), Math.round(y0 * pr), src.width, src.height);
        onProgress && onProgress(++n, rows * cols);
      }
    }
    return { canvas: out, wx0, wy0, wx1: wx0 + cssW / worldPx, wy1: wy0 + cssH / worldPx, complete };
  }

  const mercX = lng => (lng + 180) / 360;
  const mercY = lat => { const s = Math.sin(lat * Math.PI / 180); return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI); };

  /**
   * Fond vectoriel d'une carte Leaflet fixe (livre) : la vue actuelle est rendue une fois, en haute
   * définition, puis posée comme image. Renvoie une promesse résolue quand l'image est en place.
   */
  async function leafletStill(lmap, style, { pixelRatio = 3 } = {}) {
    const b = lmap.getBounds();
    const size = lmap.getSize();
    const wx0 = mercX(b.getWest()), wx1 = mercX(b.getEast()), wy0 = mercY(b.getNorth()), wy1 = mercY(b.getSouth());
    const zoom = Math.log2(size.x / (wx1 - wx0) / 512);
    const res = await renderWorld({ style, wx0, wy0, wx1, wy1, zoom, pixelRatio });
    const url = await new Promise(r => res.canvas.toBlob(blob => r(URL.createObjectURL(blob)), "image/png"));
    if (!lmap.getPane("vectorbase")) lmap.createPane("vectorbase").style.zIndex = 150;
    return L.imageOverlay(url, b, { interactive: false, pane: "vectorbase", className: "vector-still" }).addTo(lmap);
  }

  window.OTLRender = { renderWorld, leafletStill };
})();
