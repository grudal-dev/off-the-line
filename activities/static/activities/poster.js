/* Affiche : composition et export d'une image haute résolution à imprimer.

   Tout est dessiné sur un <canvas> par la même fonction pour l'aperçu et pour l'export :
   les dimensions sont en millimètres et converties en pixels selon la taille du canevas
   (aperçu à l'écran, ou 150 / 200 / 300 ppp pour l'image finale). Ce que l'on voit est
   donc exactement ce qui sera imprimé, en plus fin.

   Les tracés sont projetés en Web Mercator (coordonnées « monde » entre 0 et 1) ; le
   cadrage est un centre et une largeur visible dans ces coordonnées, plus une rotation.
   Les fonds de carte sont des tuiles chargées en CORS pour que l'export reste possible. */
(() => {
  const read = id => JSON.parse(document.getElementById(id).textContent);
  const ROUTES = read("routes-data");
  const TRIPS = read("journeys-data");
  const FAMILIES = read("families-data");
  const INITIAL = read("initial-data");
  const BASEMAP = read("basemap-data");
  const ATHLETE = read("athlete-data");
  const FAMILY = Object.fromEntries(FAMILIES.map(f => [f.key, f]));
  const STORE = "otl-poster";

  const form = document.getElementById("poster-form");
  const canvas = document.getElementById("poster-canvas");
  const stage = document.getElementById("poster-stage");
  const status = document.getElementById("export-status");

  // Formats en millimètres (portrait)
  const FORMATS = {
    A4: [210, 297], A3: [297, 420], A2: [420, 594], A1: [594, 841],
    "30x40": [300, 400], "40x50": [400, 500], "50x70": [500, 700], "30x30": [300, 300],
  };
  const LAYERS = BASEMAP.layers;
  const PRESETS = {
    paper: { bg: "#f4efe4", ink: "#2b2118", color: "#c2410c", color2: "#5b1a0b", base: "", frameColor: "#2b2118", matColor: "#fbf8f1", hlColor: "#0f766e", glow: false },
    night: { bg: "#141b1e", ink: "#f3efe6", color: "#ff8a3d", color2: "#fff4d6", base: "", frameColor: "#f3efe6", matColor: "#0b1012", hlColor: "#7dd3fc", glow: true },
    snow: { bg: "#ffffff", ink: "#1c2629", color: "#1c2629", color2: "#fc4c02", base: "", frameColor: "#1c2629", matColor: "#f2f2ef", hlColor: "#fc4c02", glow: false },
    ocean: { bg: "#0f2a3d", ink: "#e0f2fe", color: "#7dd3fc", color2: "#ffffff", base: "", frameColor: "#e0f2fe", matColor: "#0a1d2b", hlColor: "#fbbf24", glow: true },
    forest: { bg: "#e7ece3", ink: "#1f3326", color: "#2f5d3a", color2: "#c2410c", base: "", frameColor: "#1f3326", matColor: "#f6f8f3", hlColor: "#c2410c", glow: false },
    satellite: { bg: "#1a211c", ink: "#f5f1e6", color: "#ffd166", color2: "#ff5d2e", base: "satellite", frameColor: "#f5f1e6", matColor: "#1a211c", hlColor: "#ffffff", glow: true },
  };
  const DEFAULTS = {
    source: "all", hidden: [], q: "", from: "", to: "", journey: TRIPS[0] ? TRIPS[0].id : null, activity: null, selection: [],
    format: "A3", orientation: "portrait", dpi: "200",
    ...PRESETS.paper, relief: false,
    mode: "single", width: 0.6, opacity: 0.85, endpoints: false,
    frame: "line", margin: 15,
    textPos: "bottom", title: "", subtitle: "", stats: "",
    rotation: 0, view: null, manual: false,
    countries: false, regions: false, coast: false,
    borderColor: PRESETS.paper.ink, borderWidth: 0.3, borderStyle: "dash", regionStyle: "dot", borderOpacity: 0.6,
    cities: false, cityCount: 8, citySize: 4, cityColor: PRESETS.paper.ink, cityUpper: true, cityDots: true,
    labelHalo: true, brand: "escape",
    mute: 35, casing: true, stageDots: true, air: 10, cityMode: "stops", profile: true,
    hl: false, hlWidth: 2.5, hlCasing: true, hlLabels: true, hlOff: [],
  };

  // --- Données ------------------------------------------------------------
  function mercator(lat, lng) {
    const s = Math.sin(lat * Math.PI / 180);
    return [(lng + 180) / 360, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)];
  }
  function decode(str, factor = 1e5) {
    const out = [];
    let i = 0, lat = 0, lng = 0;
    while (i < str.length) {
      for (let axis = 0; axis < 2; axis++) {
        let shift = 0, result = 0, b;
        do { b = str.charCodeAt(i++) - 63; result |= (b & 0x1f) << shift; shift += 5; } while (b >= 0x20);
        const d = result & 1 ? ~(result >> 1) : result >> 1;
        if (axis === 0) lat += d; else lng += d;
      }
      out.push(...mercator(lat / factor, lng / factor));
    }
    return Float64Array.from(out);
  }
  for (const r of ROUTES) { r.pts = decode(r.poly); delete r.poly; }
  const byId = new Map(ROUTES.map(r => [r.id, r]));

  // --- État (mémorisé dans le navigateur) ---------------------------------
  let state = { ...DEFAULTS };
  try { Object.assign(state, JSON.parse(localStorage.getItem(STORE) || "{}")); } catch (e) { /* ignoré */ }
  if (INITIAL.source) {
    state.source = INITIAL.source;
    state[INITIAL.source] = INITIAL.id;
    Object.assign(state, { title: INITIAL.title || "", subtitle: "", stats: "", view: null, manual: false });
  }
  // Sélection faite sur la carte (zone) : ids des activités encore présentes
  state.selection = (Array.isArray(state.selection) ? state.selection : []).filter(id => byId.has(id));
  if (state.source === "selection" && !state.selection.length) state.source = "all";
  if (!LAYERS.some(l => l.key === state.base)) state.base = "";
  if (!byId.has(state.activity)) state.activity = ROUTES.length ? ROUTES[ROUTES.length - 1].id : null;
  if (!TRIPS.some(t => t.id === state.journey)) state.journey = TRIPS[0] ? TRIPS[0].id : null;
  if (state.source === "journey" && state.journey === null) state.source = "all";
  function save() {
    try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) { /* stockage indisponible */ }
  }

  // --- Sélection et textes proposés ---------------------------------------
  // Les polices Barlow n'ont pas l'espace fine insécable (U+202F) du format français : « 3613 » au lieu
  // de « 3 613 ». On la remplace par une espace insécable ordinaire, présente dans la police.
  const frNumber = opts => {
    const f = new Intl.NumberFormat("fr-FR", opts);
    return { format: v => f.format(v).replace(/\u202f/g, "\u00a0") };
  };
  const nf = frNumber({ maximumFractionDigits: 0 });
  const nf1 = frNumber({ maximumFractionDigits: 1 });
  const longDate = d => new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long", year: "numeric" }).format(new Date(d + "T12:00"));
  const shortDate = d => new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "long" }).format(new Date(d + "T12:00"));

  // Recherche dans les noms : sans accents ni majuscules, tous les mots doivent y être
  const fold = str => (str || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const searchWords = q => fold(q).split(/\s+/).filter(Boolean);
  for (const r of ROUTES) r.folded = fold(r.name);
  const matches = (r, words) => words.every(w => r.folded.includes(w));

  function selection() {
    if (state.source === "journey") {
      const trip = TRIPS.find(t => t.id === state.journey);
      if (!trip) return { routes: [], transfers: [] };
      const stages = trip.stages.map(id => byId.get(id));
      const transfers = (trip.transfers || []).filter(i => stages[i] && stages[i + 1]).map(i => [stages[i], stages[i + 1]]);
      return { routes: stages.filter(Boolean), transfers, trip };
    }
    if (state.source === "activity") {
      const r = byId.get(state.activity);
      return { routes: r ? [r] : [], transfers: [] };
    }
    if (state.source === "selection") {
      const wanted = new Set(state.selection);
      return { routes: ROUTES.filter(r => wanted.has(r.id)), transfers: [] };  // ROUTES est trié par date
    }
    const hidden = new Set(state.hidden), words = searchWords(state.q);
    return {
      routes: ROUTES.filter(r => !hidden.has(r.family) && (!state.from || r.day >= state.from) && (!state.to || r.day <= state.to)
                                 && matches(r, words)),
      transfers: [],
    };
  }

  // --- Pays traversés : contours simplifiés des pays (Natural Earth 1:110 M, countries-shapes.json) ---
  // Un pays ne compte qu'au-delà de 15 km parcourus : à cette échelle, les contours sont approximatifs.
  const MIN_COUNTRY_KM = 15;
  let world = null, worldLoading = null;
  function loadWorld() {
    if (!worldLoading) {
      worldLoading = fetch(canvas.dataset.countryShapes).then(r => r.json()).then(data => {
        world = (data.countries || []).map(([name, w, s, e, n, rings]) => {
          const [x0, y1] = mercator(s, w), [x1, y0] = mercator(n, e);
          return { name, box: [x0, y0, x1, y1], rings: rings.split(" ").map(r => decode(r, 1e4)) };
        });
        countryCache.clear();
        redraw();
      }).catch(() => { world = []; });
    }
    return world;
  }
  function inRing(x, y, p) {
    let inside = false;
    for (let i = 0, j = p.length - 2; i < p.length; j = i, i += 2) {
      const xi = p[i], yi = p[i + 1], xj = p[j], yj = p[j + 1];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function countryAt(x, y) {
    for (const c of world) {
      const b = c.box;
      if (x < b[0] || x > b[2] || y < b[1] || y > b[3]) continue;
      let inside = false;
      for (const r of c.rings) if (inRing(x, y, r)) inside = !inside;  // les trous (lacs, enclaves) se compensent
      if (inside) return c.name;
    }
    return null;
  }
  const countryCache = new Map();
  function countriesOf(routes) {
    if (!routes.length || !loadWorld() || !world.length) return null;
    const key = routes.map(r => r.id).join();
    if (countryCache.has(key)) return countryCache.get(key);
    const total = routes.reduce((t, r) => t + r.pts.length / 2, 0);
    const every = Math.max(1, Math.floor(total / 4000));  // assez de points, sans bloquer l'aperçu
    const km = new Map();
    for (const r of routes) {
      const p = r.pts;
      let prev = null;
      for (let i = 0; i < p.length; i += 2 * every) {
        const name = countryAt(p[i], p[i + 1]);
        if (prev && name) {
          // longueur du segment : 40 075 km par unité « monde », réduite selon la latitude
          const lat = inverseMercator(p[i], p[i + 1])[0];
          const d = Math.hypot(p[i] - prev[0], p[i + 1] - prev[1]) * 40075 * Math.cos(lat * Math.PI / 180);
          km.set(name, (km.get(name) || 0) + d);
        }
        prev = [p[i], p[i + 1]];
      }
    }
    const found = [...km].filter(([, d]) => d >= MIN_COUNTRY_KM).map(([name]) => name);
    countryCache.set(key, found);
    return found;
  }
  const inverseMercator = (x, y) => [Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180 / Math.PI, x * 360 - 180];
  const plural = (n, word) => `${nf.format(n)} ${word}${n > 1 ? "s" : ""}`;

  function autoTexts(sel) {
    const rs = sel.routes;
    const km = rs.reduce((a, r) => a + r.km, 0), elev = rs.reduce((a, r) => a + r.elev, 0);
    const dplus = elev ? ` · ${nf.format(elev)} m D+` : "";
    if (state.source === "activity") {
      const r = rs[0];
      if (!r) return { title: "", subtitle: "", stats: "" };
      return { title: r.name, subtitle: longDate(r.day), stats: `${nf1.format(r.km)} km${dplus}` };
    }
    const countries = state.source === "all" ? null : countriesOf(rs);
    const pays = countries && countries.length > 1 ? ` · ${countries.length} pays` : "";
    const days = new Set(rs.map(r => r.day)).size;
    if (state.source === "journey" && sel.trip) {
      return {
        title: sel.trip.title, subtitle: sel.trip.dates.replace(/^du /, "Du "),
        stats: `${plural(sel.trip.days || days, "jour")}${pays} · ${nf.format(km)} km${dplus}`,
      };
    }
    const fams = [...new Set(rs.map(r => r.family))];
    if (state.source === "selection" && rs.length) {
      const first = rs[0].day, last = rs[rs.length - 1].day;
      const subtitle = first === last ? longDate(first)
        : first.slice(0, 4) === last.slice(0, 4) ? `Du ${shortDate(first)} au ${longDate(last)}`
        : `${first.slice(0, 4)} – ${last.slice(0, 4)}`;
      return { title: ATHLETE ? `Les parcours de ${ATHLETE}` : "Mes parcours", subtitle,
               stats: `${plural(rs.length, "sortie")}${pays} · ${nf.format(km)} km${dplus}` };
    }
    let title = fams.length === 1 && FAMILY[fams[0]] ? FAMILY[fams[0]].label : (ATHLETE ? `Les parcours de ${ATHLETE}` : "Mes parcours");
    let subtitle = "";
    if (rs.length) {
      const first = rs[0].day, last = rs[rs.length - 1].day;
      if (state.from || state.to) {
        const sameYear = first.slice(0, 4) === last.slice(0, 4);
        subtitle = first === last ? longDate(first)
          : isWholeYear() ? first.slice(0, 4)
          : `Du ${sameYear ? shortDate(first) : longDate(first)} au ${longDate(last)}`;
      } else {
        subtitle = first.slice(0, 4) === last.slice(0, 4) ? first.slice(0, 4) : `${first.slice(0, 4)} – ${last.slice(0, 4)}`;
      }
    }
    return { title, subtitle, stats: `${nf.format(rs.length)} sortie${rs.length > 1 ? "s" : ""} · ${nf.format(km)} km${dplus}` };
  }
  function isWholeYear() {
    return state.from && state.to && state.from.slice(0, 4) === state.to.slice(0, 4)
      && state.from.endsWith("-01-01") && state.to.endsWith("-12-31");
  }
  // Champ vide : texte proposé ; un espace seul : pas de texte
  const text = (key, auto) => state[key] === "" ? auto[key] : state[key].trim();

  // --- Mise en page (en millimètres) --------------------------------------
  function paper() {
    const [w, h] = FORMATS[state.format] || FORMATS.A3;
    return state.orientation === "landscape" ? [Math.max(w, h), Math.min(w, h)] : [Math.min(w, h), Math.max(w, h)];
  }

  // Profil du bandeau : altitudes enchaînées si toutes les étapes en ont un, sinon le D+ de chaque étape
  function profileData(sel) {
    const rs = sel.routes;
    if (state.source === "all" || !rs.length || rs.length > 400) return null;  // « Mes sorties » : pas de sens
    if (rs.every(r => r.prof && r.prof.length > 1)) {
      const pts = [];
      let x0 = 0;
      for (const r of rs) {
        r.prof.forEach((alt, i) => pts.push([x0 + r.km * i / (r.prof.length - 1), alt]));
        x0 += r.km;
      }
      return { kind: "line", pts, total: x0 };
    }
    if (rs.length < 2 || !rs.some(r => r.elev)) return null;
    return { kind: "bars", bars: rs.map(r => [r.km, r.elev]), total: rs.reduce((t, r) => t + r.km, 0) };
  }

  function layout(texts, sel) {
    const [W, H] = paper();
    const k = Math.min(W, H) / 297;  // les épaisseurs suivent la taille de l'affiche
    const m = Math.min(+state.margin, Math.min(W, H) / 4);
    const outer = { x: m, y: m, w: W - 2 * m, h: H - 2 * m };
    let content = outer;
    if (state.frame === "double") content = inset(outer, 2.2 * k);
    if (state.frame === "bold") content = inset(outer, 7 * k);
    let map = content, band = null;
    const lines = state.textPos === "none" ? [] : [texts.title, texts.subtitle, texts.stats];
    if (lines.some(Boolean)) {
      const title = Math.min(content.w * 0.075, content.h * 0.065);
      const sizes = { title, subtitle: title * 0.36, stats: title * 0.3 };
      const pad = title * 0.55;
      let h = pad * 2;
      if (texts.title) h += sizes.title;
      if (texts.subtitle) h += sizes.subtitle * 1.6;
      if (texts.stats) h += sizes.stats * 2.6;
      const prof = state.profile && sel ? profileData(sel) : null;
      if (prof) { h += title * 0.85; sizes.prof = prof; }
      h = Math.min(h, content.h * 0.35);
      band = { x: content.x, w: content.w, h, sizes, pad, y: state.textPos === "top" ? content.y : content.y + content.h - h };
      map = { x: content.x, w: content.w, h: content.h - h, y: state.textPos === "top" ? content.y + h : content.y };
    }
    return { W, H, k, outer, content, map, band };
  }
  const inset = (r, d) => ({ x: r.x + d, y: r.y + d, w: r.w - 2 * d, h: r.h - 2 * d });

  // --- Cadrage --------------------------------------------------------------
  function fit(sel, lay) {
    const th = state.rotation * Math.PI / 180, cos = Math.cos(th), sin = Math.sin(th);
    let u0 = Infinity, u1 = -Infinity, v0 = Infinity, v1 = -Infinity;
    for (const r of sel.routes) {
      const p = r.pts;
      for (let i = 0; i < p.length; i += 2) {
        const u = p[i] * cos - p[i + 1] * sin, v = p[i] * sin + p[i + 1] * cos;
        if (u < u0) u0 = u; if (u > u1) u1 = u; if (v < v0) v0 = v; if (v > v1) v1 = v;
      }
    }
    if (!isFinite(u0)) return { cx: 0.5, cy: 0.5, span: 1 };
    const keep = 1 - Math.min(50, Math.max(2, +state.air || 10)) / 100;  // air autour des tracés (total des deux côtés)
    const ratio = lay.map.w / lay.map.h;
    const span = Math.max((u1 - u0) / keep, (v1 - v0) * ratio / keep, 2e-5);
    const uc = (u0 + u1) / 2, vc = (v0 + v1) / 2;
    return { cx: uc * cos + vc * sin, cy: -uc * sin + vc * cos, span };
  }

  // --- Tuiles des fonds de carte ------------------------------------------
  const tiles = new Map();
  function tile(url) {
    let t = tiles.get(url);
    if (!t) {
      t = { img: new Image(), ok: false };
      t.promise = new Promise(resolve => {
        t.img.onload = () => { t.ok = true; resolve(t); };
        t.img.onerror = () => { t.failed = true; resolve(t); };
      });
      t.img.crossOrigin = "anonymous";
      t.img.src = url;
      tiles.set(url, t);
    }
    return t;
  }
  function tileUrl(def, z, x, y) {
    return def.url.replace("{z}", z).replace("{x}", x).replace("{y}", y).replace("{s}", "a").replace("{r}", "");
  }
  // Tuiles couvrant la carte (rotation comprise) au niveau de zoom adapté à la résolution
  function tilePlan(def, view, mapPx, maxTiles) {
    const s = mapPx.w / view.span;
    const maxZ = (def.options && (def.options.maxNativeZoom || def.options.maxZoom)) || 18;
    let z = Math.max(0, Math.min(maxZ, Math.round(Math.log2(s / 256))));
    const radius = Math.hypot(mapPx.w, mapPx.h) / 2 / s;
    for (;;) {
      const n = 2 ** z;
      const x0 = Math.floor((view.cx - radius) * n), x1 = Math.floor((view.cx + radius) * n);
      const y0 = Math.max(0, Math.floor((view.cy - radius) * n)), y1 = Math.min(n - 1, Math.floor((view.cy + radius) * n));
      if ((x1 - x0 + 1) * (y1 - y0 + 1) > maxTiles && z > 0) { z--; continue; }
      const list = [];
      for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) {
        list.push({ url: tileUrl(def, z, ((x % n) + n) % n, y), x: (x / n - view.cx) * s, y: (y / n - view.cy) * s, size: s / n });
      }
      return list;
    }
  }
  function layerDefs() {
    const defs = [];
    const base = LAYERS.find(l => l.key === state.base);
    if (base) {
      defs.push({ def: base, alpha: 1 });
      if (state.relief && BASEMAP.hillshade) defs.push({ def: BASEMAP.hillshade, alpha: BASEMAP.hillshade.options.opacity || 0.45 });
    }
    return defs;
  }

  // --- Dessin ---------------------------------------------------------------
  function hexRgb(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function mix(a, b, t) {
    const A = hexRgb(a), B = hexRgb(b);
    return `rgb(${A.map((v, i) => Math.round(v + (B[i] - v) * t)).join(",")})`;
  }
  function luminance(hex) {
    const [r, g, b] = hexRgb(hex);
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  }

  // Trace une activité dans le repère de la carte (origine au centre, sans rotation)
  function tracePath(ctx, pts, view, s) {
    let lx = Infinity, ly = Infinity;
    for (let i = 0; i < pts.length; i += 2) {
      const x = (pts[i] - view.cx) * s, y = (pts[i + 1] - view.cy) * s;
      if (i === 0) ctx.moveTo(x, y);
      else if (Math.abs(x - lx) + Math.abs(y - ly) > 0.6 || i === pts.length - 2) ctx.lineTo(x, y);
      else continue;
      lx = x; ly = y;
    }
  }

  function routeColor(r, i, n) {
    if (state.mode === "sport") return (FAMILY[r.family] || {}).color || state.color;
    if (state.mode === "time") return mix(state.color, state.color2, n > 1 ? i / (n - 1) : 0);
    return state.color;
  }

  function drawTracks(ctx, sel, view, s, ppm) {
    const lw = Math.max(0.5, state.width * ppm);
    const routes = sel.routes, n = routes.length;
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    if (state.casing && state.mode !== "density") {
      // Liseré : un trait plus large de la couleur du fond sous les tracés
      ctx.globalAlpha = 0.9; ctx.strokeStyle = state.bg; ctx.lineWidth = lw + Math.max(1.5, lw * 1.6);
      ctx.beginPath(); for (const r of routes) tracePath(ctx, r.pts, view, s); ctx.stroke();
      ctx.globalAlpha = 1;
    }
    if (state.glow) {
      ctx.globalAlpha = Math.min(0.5, state.opacity * 0.14);
      ctx.lineWidth = lw * 5;
      routes.forEach((r, i) => { ctx.strokeStyle = state.mode === "density" ? state.color : routeColor(r, i, n); ctx.beginPath(); tracePath(ctx, r.pts, view, s); ctx.stroke(); });
    }
    ctx.lineWidth = lw;
    if (state.mode === "density") {
      drawDensity(ctx, routes, view, s, lw);
    } else {
      ctx.globalAlpha = state.opacity;
      routes.forEach((r, i) => { ctx.strokeStyle = routeColor(r, i, n); ctx.beginPath(); tracePath(ctx, r.pts, view, s); ctx.stroke(); });
    }
    ctx.globalAlpha = 1;
    // Transferts d'un périple (train, ferry…) : pointillés
    if (sel.transfers.length) {
      ctx.save();
      ctx.strokeStyle = state.ink; ctx.globalAlpha = 0.75; ctx.lineWidth = Math.max(0.5, lw * 0.6);
      ctx.setLineDash([lw * 0.2, lw * 2.4]);
      for (const [a, b] of sel.transfers) {
        ctx.beginPath();
        ctx.moveTo((a.pts[a.pts.length - 2] - view.cx) * s, (a.pts[a.pts.length - 1] - view.cy) * s);
        ctx.lineTo((b.pts[0] - view.cx) * s, (b.pts[1] - view.cy) * s);
        ctx.stroke();
      }
      ctx.restore();
    }
    if (state.stageDots && n > 1 && state.source !== "all") {
      // Un point à chaque fin d'étape (sauf la dernière : c'est l'arrivée)
      const rad = Math.max(lw * 1.05, 0.55 * ppm);
      routes.slice(0, -1).forEach((r, i) => {
        const p = r.pts, x = (p[p.length - 2] - view.cx) * s, y = (p[p.length - 1] - view.cy) * s;
        ctx.beginPath(); ctx.arc(x, y, rad, 0, 2 * Math.PI);
        ctx.fillStyle = state.bg; ctx.fill();
        ctx.lineWidth = Math.max(0.8, rad * 0.5); ctx.strokeStyle = state.mode === "density" ? state.color : routeColor(r, i, n);
        ctx.stroke();
      });
    }
    if (state.endpoints && routes.length && state.source !== "all") {
      const first = routes[0].pts, last = routes[routes.length - 1].pts;
      const rad = Math.max(lw * 1.8, 1.3 * ppm);
      const dot = (x, y, fill) => {
        ctx.beginPath(); ctx.arc((x - view.cx) * s, (y - view.cy) * s, rad, 0, 2 * Math.PI);
        ctx.fillStyle = fill; ctx.fill();
        ctx.lineWidth = rad * 0.45; ctx.strokeStyle = state.mode === "time" ? state.color : routeColor(routes[0], 0, 1); ctx.stroke();
      };
      dot(first[0], first[1], state.bg);
      dot(last[last.length - 2], last[last.length - 1], state.mode === "time" ? state.color2 : routeColor(routes[routes.length - 1], 0, 1));
    }
  }

  // Densité : les tracés s'additionnent sur un calque, puis l'intensité est recolorée
  // de « couleur » (un passage) à « couleur 2 » (très fréquenté).
  function drawDensity(ctx, routes, view, s, lw) {
    const { width, height } = ctx.canvas;
    const off = document.createElement("canvas");
    off.width = width; off.height = height;
    const o = off.getContext("2d");
    o.setTransform(ctx.getTransform());
    o.globalCompositeOperation = "lighter";
    o.strokeStyle = "#fff"; o.lineWidth = lw; o.lineCap = "round"; o.lineJoin = "round";
    o.globalAlpha = Math.max(0.04, state.opacity * 0.3);
    for (const r of routes) { o.beginPath(); tracePath(o, r.pts, view, s); o.stroke(); }
    const ramp = document.createElement("canvas");
    ramp.width = 256; ramp.height = 1;
    const rc = ramp.getContext("2d");
    const g = rc.createLinearGradient(0, 0, 256, 0);
    const [r1, g1, b1] = hexRgb(state.color);
    g.addColorStop(0, `rgba(${r1},${g1},${b1},0)`);
    g.addColorStop(0.05, `rgba(${r1},${g1},${b1},.75)`);
    g.addColorStop(0.35, state.color);
    g.addColorStop(1, state.color2);
    rc.fillStyle = g; rc.fillRect(0, 0, 256, 1);
    const pal = rc.getImageData(0, 0, 256, 1).data;
    const img = o.getImageData(0, 0, width, height), px = img.data;
    for (let i = 3; i < px.length; i += 4) {
      const a = px[i];
      if (!a) continue;
      const k = a * 4;
      px[i - 3] = pal[k]; px[i - 2] = pal[k + 1]; px[i - 1] = pal[k + 2]; px[i] = pal[k + 3];
    }
    o.putImageData(img, 0, 0);
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(off, 0, 0);
    ctx.restore();
  }

  function fontString(weight, size, family) { return `${weight} ${size}px ${family}`; }
  const COND = '"Barlow Condensed", "Arial Narrow", sans-serif';
  const SANS = '"Barlow", system-ui, sans-serif';

  function fitText(ctx, str, weight, size, family, maxW, spacing) {
    let px = size;
    for (let i = 0; i < 12; i++) {
      ctx.font = fontString(weight, px, family);
      if ("letterSpacing" in ctx) ctx.letterSpacing = `${px * spacing}px`;
      if (ctx.measureText(str).width <= maxW) break;
      px *= 0.92;
    }
    return px;
  }

  // Les polices Barlow n'ont pas de flèche (→) : on la dessine, à l'épaisseur du titre
  function fillTextWithArrows(ctx, txt, cx, y, size) {
    const parts = txt.split(/\s*→\s*/);
    if (parts.length < 2) { ctx.fillText(txt, cx, y); return; }
    const arrow = size * 0.62, gap = size * 0.28;
    const widths = parts.map(t => ctx.measureText(t).width);
    const total = widths.reduce((a, b) => a + b, 0) + (parts.length - 1) * (arrow + 2 * gap);
    let x = cx - total / 2;
    const align = ctx.textAlign;
    ctx.textAlign = "left";
    parts.forEach((t, i) => {
      ctx.fillText(t, x, y);
      x += widths[i];
      if (i === parts.length - 1) return;
      const ay = y - size * 0.33, t2 = size * 0.075;
      ctx.save();
      ctx.strokeStyle = ctx.fillStyle; ctx.lineWidth = t2 * 1.6; ctx.lineCap = "round"; ctx.lineJoin = "round";
      ctx.beginPath();
      ctx.moveTo(x + gap, ay); ctx.lineTo(x + gap + arrow, ay);
      ctx.moveTo(x + gap + arrow - size * 0.2, ay - size * 0.18); ctx.lineTo(x + gap + arrow, ay); ctx.lineTo(x + gap + arrow - size * 0.2, ay + size * 0.18);
      ctx.stroke();
      ctx.restore();
      x += arrow + 2 * gap;
    });
    ctx.textAlign = align;
  }

  function drawText(ctx, lay, texts, ppm) {
    const b = lay.band;
    if (!b) return;
    const X = v => v * ppm;
    const cx = X(b.x + b.w / 2), maxW = X(b.w * 0.9);
    ctx.fillStyle = state.ink;
    ctx.textAlign = "center"; ctx.textBaseline = "alphabetic";
    let y = X(b.y + b.pad);
    if (texts.title) {
      const size = fitText(ctx, texts.title.toUpperCase(), 700, X(b.sizes.title), COND, maxW, 0.06);
      y += size * 0.86;
      fillTextWithArrows(ctx, texts.title.toUpperCase(), cx, y, size);
      y += X(b.sizes.title) * 0.14;
    }
    if (texts.subtitle) {
      const size = fitText(ctx, texts.subtitle, 500, X(b.sizes.subtitle), SANS, maxW, 0.02);
      y += size * 1.35;
      ctx.globalAlpha = 0.85;
      fillTextWithArrows(ctx, texts.subtitle, cx, y, size);
      ctx.globalAlpha = 1;
    }
    const prof = b.sizes.prof;
    if (prof) {
      const ph = X(b.sizes.title * 0.55), pw = X(b.w * 0.62), px = cx - pw / 2;
      y += X(b.sizes.title * 0.3);
      ctx.save();
      ctx.fillStyle = ctx.strokeStyle = state.ink;
      if (prof.kind === "line") {
        const alts = prof.pts.map(p => p[1]), lo = Math.min(...alts), hi = Math.max(...alts, lo + 50);
        const P = ([d, a]) => [px + pw * d / prof.total, y + ph - ph * (a - lo) / (hi - lo)];
        ctx.beginPath(); ctx.moveTo(px, y + ph);
        for (const p of prof.pts) ctx.lineTo(...P(p));
        ctx.lineTo(px + pw, y + ph); ctx.closePath();
        ctx.globalAlpha = 0.16; ctx.fill();
        ctx.beginPath(); prof.pts.forEach((p, i) => ctx[i ? "lineTo" : "moveTo"](...P(p)));
        ctx.globalAlpha = 0.75; ctx.lineWidth = Math.max(1, X(0.35 * lay.k)); ctx.lineJoin = "round"; ctx.stroke();
      } else {
        const hi = Math.max(...prof.bars.map(b => b[1]), 1), gap = Math.min(X(0.6 * lay.k), pw / prof.bars.length * 0.25);
        let x = px;
        ctx.globalAlpha = 0.45;
        for (const [km, elev] of prof.bars) {
          const w = pw * km / prof.total, h = Math.max(X(0.3 * lay.k), ph * elev / hi);
          ctx.fillRect(x + gap / 2, y + ph - h, Math.max(0.5, w - gap), h);
          x += w;
        }
      }
      ctx.restore();
      y += ph;
    }
    if (texts.stats) {
      const size = X(b.sizes.stats);
      y += size * 0.9;
      ctx.fillRect(cx - X(b.w * 0.04), y, X(b.w * 0.08), Math.max(1, size * 0.07));
      const fsize = fitText(ctx, texts.stats.toUpperCase(), 600, size, COND, maxW, 0.12);
      y += fsize * 1.55;
      ctx.globalAlpha = 0.8;
      ctx.fillText(texts.stats.toUpperCase(), cx, y);
      ctx.globalAlpha = 1;
    }
    if ("letterSpacing" in ctx) ctx.letterSpacing = "0px";
  }

  function drawFrame(ctx, lay, ppm) {
    const X = v => v * ppm, k = lay.k;
    const rect = (r, w) => { ctx.lineWidth = Math.max(1, X(w)); ctx.strokeRect(X(r.x), X(r.y), X(r.w), X(r.h)); };
    ctx.strokeStyle = ctx.fillStyle = state.frameColor;
    const o = lay.outer;
    if (state.frame === "line") rect(o, 0.5 * k);
    if (state.frame === "double") { rect(o, 0.7 * k); rect(inset(o, 2.2 * k), 0.3 * k); }
    if (state.frame === "bold") {
      const t = 7 * k;
      ctx.beginPath();
      ctx.rect(X(o.x), X(o.y), X(o.w), X(o.h));
      ctx.rect(X(o.x + t), X(o.y + t), X(o.w - 2 * t), X(o.h - 2 * t));
      ctx.fill("evenodd");  // le rectangle intérieur fait un trou
    }
    if (state.frame === "mat") rect(o, 0.35 * k);
  }


  // --- Marque « Off the line » ----------------------------------------------
  // Le motif du logo : une ligne droite (pointillés) et le parcours réel qui en sort.
  const GLYPH_LINE = [96, 400, 416, 112];
  const GLYPH_PATH = new Path2D("M96 400 C 150 352, 120 300, 190 286 S 250 330, 292 262 S 250 170, 330 166 S 392 150, 416 112");
  function drawGlyph(ctx, x, y, size, color, bg) {
    // x, y : coin haut gauche ; size : côté en pixels
    const k = size / 390;  // le motif occupe 62…452 × 78…434 dans le logo 512 × 512
    ctx.save();
    ctx.translate(x - 62 * k, y - 78 * k);
    ctx.scale(k, k);
    ctx.strokeStyle = color; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.globalAlpha = 0.5; ctx.lineWidth = 18; ctx.setLineDash([2, 38]);
    ctx.beginPath(); ctx.moveTo(GLYPH_LINE[0], GLYPH_LINE[1]); ctx.lineTo(GLYPH_LINE[2], GLYPH_LINE[3]); ctx.stroke();
    ctx.globalAlpha = 1; ctx.setLineDash([]); ctx.lineWidth = 34;
    ctx.stroke(GLYPH_PATH);
    // départ : un anneau ; arrivée : un point plein
    for (const [cx, cy, hole] of [[96, 400, true], [416, 112, false]]) {
      ctx.beginPath(); ctx.arc(cx, cy, 34, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill();
      if (hole) { ctx.beginPath(); ctx.arc(cx, cy, 15, 0, Math.PI * 2); ctx.fillStyle = bg; ctx.fill(); }
    }
    ctx.restore();
  }
  const BRAND = "OFF THE LINE";

  function drawBrand(ctx, lay, ppm) {
    let v = state.brand;
    if (!v || v === "none") return;
    if (state.frame === "none" && (v === "gap" || v === "escape")) v = "signature";  // pas de filet à couper
    const X = mm => mm * ppm, k = lay.k, o = lay.outer;
    const outside = state.frame === "mat" ? state.matColor : state.bg;
    const ink = state.frameColor;
    const bottom = X(o.y + o.h), right = X(o.x + o.w), left = X(o.x);
    const margin = X(o.x);  // marge extérieure (identique sur les 4 côtés)
    ctx.save();
    if ("letterSpacing" in ctx) ctx.letterSpacing = "0px";
    ctx.textBaseline = "middle";

    if (v === "gap") {
      // 1. Coupure : le filet du bas s'interrompt pour laisser passer la marque
      const size = X(2.4 * k);
      ctx.font = fontString(600, size, COND);
      if ("letterSpacing" in ctx) ctx.letterSpacing = `${size * 0.35}px`;
      const tw = ctx.measureText(BRAND).width, gw = size * 1.15, pad = size * 0.9;
      const w = gw + size * 0.5 + tw + pad * 2, cx = X(o.x + o.w / 2);
      if (state.frame === "bold") {
        // dans le cadre épais : texte en réserve
        const t = X(7 * k), yc = bottom - t / 2;
        ctx.fillStyle = state.bg;
        drawGlyph(ctx, cx - w / 2 + pad, yc - gw / 2, gw, state.bg, ink);
        ctx.textAlign = "left"; ctx.fillText(BRAND, cx - w / 2 + pad + gw + size * 0.5, yc);
      } else {
        const extra = state.frame === "double" ? X(2.2 * k) : 0;
        ctx.fillStyle = outside;
        ctx.fillRect(cx - w / 2, bottom - X(1.2 * k) - extra, w, X(2.4 * k) + extra);
        ctx.fillStyle = ink;
        drawGlyph(ctx, cx - w / 2 + pad, bottom - gw / 2 - extra / 2, gw, ink, outside);
        ctx.textAlign = "left"; ctx.fillText(BRAND, cx - w / 2 + pad + gw + size * 0.5, bottom - extra / 2);
      }
    }

    if (v === "escape") {
      // 2. Échappée : le filet du bas quitte sa ligne, comme le parcours du logo
      const lw = Math.max(1, X(0.5 * k));
      const x2 = right - X(14 * k), x1 = x2 - X(26 * k), dip = Math.min(X(6 * k), margin * 0.55);
      ctx.fillStyle = outside;
      ctx.fillRect(x1, bottom - lw * 2, x2 - x1, lw * 4);
      ctx.strokeStyle = ink; ctx.lineWidth = lw; ctx.lineCap = "round";
      // la ligne « attendue », en pointillés
      ctx.globalAlpha = 0.45; ctx.setLineDash([0.001, lw * 3.2]); ctx.lineWidth = lw * 1.2;
      ctx.beginPath(); ctx.moveTo(x1, bottom); ctx.lineTo(x2, bottom); ctx.stroke();
      // le parcours réel
      ctx.globalAlpha = 1; ctx.setLineDash([]); ctx.lineWidth = lw * 1.6;
      const w = x2 - x1;
      ctx.beginPath(); ctx.moveTo(x1, bottom);
      ctx.bezierCurveTo(x1 + w * 0.15, bottom + dip * 0.9, x1 + w * 0.3, bottom + dip * 0.2, x1 + w * 0.45, bottom + dip * 0.55);
      ctx.bezierCurveTo(x1 + w * 0.6, bottom + dip * 0.95, x1 + w * 0.78, bottom + dip * 0.7, x2, bottom);
      ctx.stroke();
      for (const [x, filled] of [[x1, false], [x2, true]]) {
        ctx.beginPath(); ctx.arc(x, bottom, lw * 2.2, 0, Math.PI * 2);
        ctx.fillStyle = ink; ctx.fill();
        if (!filled) { ctx.beginPath(); ctx.arc(x, bottom, lw * 1, 0, Math.PI * 2); ctx.fillStyle = outside; ctx.fill(); }
      }
      const size = X(2.1 * k);
      ctx.font = fontString(600, size, COND);
      if ("letterSpacing" in ctx) ctx.letterSpacing = `${size * 0.3}px`;
      ctx.fillStyle = ink; ctx.textAlign = "right";
      ctx.fillText(BRAND, x1 - size * 1.2, bottom + Math.min(dip * 0.6, margin * 0.4));
    }

    if (v === "signature") {
      // 3. Signature d'atelier, dans la marge du bas
      const size = X(2.1 * k), y = bottom + Math.min(margin / 2, X(6 * k));
      ctx.fillStyle = ink; ctx.globalAlpha = 0.85;
      drawGlyph(ctx, left, y - size * 0.75, size * 1.5, ink, outside);
      ctx.font = fontString(600, size, COND);
      if ("letterSpacing" in ctx) ctx.letterSpacing = `${size * 0.3}px`;
      ctx.textAlign = "left"; ctx.fillText(BRAND, left + size * 2.1, y);
      ctx.font = fontString(500, size * 0.95, SANS);
      if ("letterSpacing" in ctx) ctx.letterSpacing = `${size * 0.1}px`;
      ctx.textAlign = "right";
      ctx.fillText(`${state.format.replace("x", " × ")} · ${new Date().getFullYear()}`, right, y);
    }

    if (v === "stamp") {
      // 4. Tampon rond, posé à cheval sur le coin du cadre
      const r = X(9 * k), cx = right - X(4 * k), cy = bottom - X(4 * k);
      ctx.fillStyle = outside; ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = ink; ctx.lineWidth = Math.max(1, X(0.35 * k));
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.stroke();
      ctx.lineWidth = Math.max(1, X(0.2 * k));
      ctx.beginPath(); ctx.arc(cx, cy, r * 0.62, 0, Math.PI * 2); ctx.stroke();
      const txt = "OFF THE LINE · HORS DES SENTIERS · ", size = r * 0.2;
      ctx.font = fontString(600, size, COND); ctx.fillStyle = ink; ctx.textAlign = "center";
      const chars = [...txt], step = (Math.PI * 2) / chars.length;
      chars.forEach((c, i) => {
        const a = -Math.PI / 2 + i * step;
        ctx.save(); ctx.translate(cx + Math.cos(a) * r * 0.81, cy + Math.sin(a) * r * 0.81); ctx.rotate(a + Math.PI / 2);
        ctx.fillText(c, 0, 0); ctx.restore();
      });
      const g = r * 0.8;
      drawGlyph(ctx, cx - g / 2, cy - g / 2, g, ink, outside);
    }
    ctx.restore();
  }

  function drawAttribution(ctx, lay, ppm, defs) {
    if (!defs.length) return;
    const txt = defs.map(d => d.def.options.attribution || "").join(" · ").replace(/<[^>]+>/g, "").replace(/&copy;/g, "©").replace(/&mdash;/g, "—").replace(/&amp;/g, "&");
    const size = Math.max(6, 1.8 * lay.k * ppm);
    ctx.font = fontString(400, size, SANS);
    ctx.textAlign = "right"; ctx.textBaseline = "bottom";
    const x = (lay.map.x + lay.map.w) * ppm - size * 0.6, y = (lay.map.y + lay.map.h) * ppm - size * 0.4;
    const w = ctx.measureText(txt).width;
    ctx.globalAlpha = 0.6; ctx.fillStyle = "#000";
    ctx.fillRect(x - w - size * 0.4, y - size * 1.3, w + size * 0.8, size * 1.5);
    ctx.globalAlpha = 0.9; ctx.fillStyle = "#fff";
    ctx.fillText(txt, x, y - size * 0.1);
    ctx.globalAlpha = 1;
  }

  // Sélection, textes, mise en page et cadrage (recadré sur les tracés tant qu'on n'y a pas touché)
  function compose() {
    const sel = selection();
    const auto = autoTexts(sel);
    const texts = { title: text("title", auto), subtitle: text("subtitle", auto), stats: text("stats", auto) };
    const lay = layout(texts, sel);
    if (!state.view || !state.manual) state.view = fit(sel, lay);
    return { sel, auto, texts, lay };
  }

  // --- Repères : frontières, côtes, villes, périples en évidence ---------
  // Données Natural Earth (domaine public), chargées seulement quand l'option est cochée
  const GEO_URLS = canvas.dataset;
  const geo = {};
  function geoData(key, onLoad) {
    let g = geo[key];
    if (!g) {
      g = geo[key] = { rows: null };
      // cities.json est un objet { cities, countries } (partagé avec les cartes) ; les frontières, des listes
      g.promise = fetch(GEO_URLS[key]).then(r => r.json()).catch(() => [])
        .then(data => { g.rows = Array.isArray(data) ? data : (data && data[key]) || []; return g; });
    }
    if (!g.rows && onLoad) g.promise.then(onLoad);
    return g.rows;
  }
  // [clé, épaisseur et opacité relatives] : les limites intérieures restent plus discrètes
  const BORDER_LAYERS = [["coast", 1, 1], ["regions", 0.55, 0.7], ["countries", 1.5, 1]];
  const wantedGeo = () => [...BORDER_LAYERS.map(([k]) => k), "cities"].filter(k => state[k]);

  function viewBox(view, mapPx, s) {
    const r = Math.hypot(mapPx.w, mapPx.h) / 2 / s;
    const lng = x => x * 360 - 180;
    const lat = y => Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180 / Math.PI;
    return { w: lng(view.cx - r), e: lng(view.cx + r), n: lat(Math.max(0, view.cy - r)), s: lat(Math.min(1, view.cy + r)) };
  }
  const visible = (box, w, s, e, n) => !(e < box.w || w > box.e || n < box.s || s > box.n);

  function drawBorders(ctx, view, s, ppm, mapPx, onLoad) {
    const box = viewBox(view, mapPx, s);
    const base = Math.max(0.5, state.borderWidth * ppm);
    ctx.save();
    ctx.strokeStyle = state.borderColor; ctx.lineJoin = "round";
    for (const [key, kw, ka] of BORDER_LAYERS) {
      if (!state[key]) continue;
      const rows = geoData(key, onLoad);
      if (!rows) continue;
      const w = Math.max(0.5, base * kw);
      // Une côte reste un trait continu ; les régions ont leur propre trait
      const style = key === "coast" ? "solid" : key === "regions" ? state.regionStyle : state.borderStyle;
      ctx.lineWidth = w; ctx.globalAlpha = state.borderOpacity * ka;
      ctx.lineCap = style === "dot" ? "round" : "butt";
      ctx.setLineDash(style === "solid" ? [] : style === "dot" ? [0.001, w * 2.6] : [w * 5, w * 3]);
      ctx.beginPath();
      for (const row of rows) {
        if (!visible(box, row[0], row[1], row[2], row[3])) continue;
        if (!row.parts) row.parts = row[4].split(" ").map(p => decode(p, 1e4));
        for (const pts of row.parts) tracePath(ctx, pts, view, s);
      }
      ctx.stroke();
    }
    ctx.restore();
  }

  // Périples en évidence (source « Mes sorties ») : ceux qui ont au moins une étape affichée
  function tripsInSelection(sel) {
    const ids = new Set(sel.routes.map(r => r.id));
    return TRIPS.filter(t => t.stages.some(id => ids.has(id)));
  }
  function highlighted(sel) {
    if (state.source !== "all" || !state.hl) return [];
    const off = new Set(state.hlOff);
    return tripsInSelection(sel).filter(t => !off.has(t.id))
      .map(t => ({ trip: t, stages: t.stages.map(id => byId.get(id)).filter(Boolean) }));
  }
  function drawHighlights(ctx, list, view, s, ppm) {
    if (!list.length) return;
    const lw = Math.max(0.8, state.width * state.hlWidth * ppm);
    const all = () => { ctx.beginPath(); for (const h of list) for (const r of h.stages) tracePath(ctx, r.pts, view, s); ctx.stroke(); };
    ctx.save();
    ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.globalAlpha = 1;
    if (state.hlCasing) { ctx.strokeStyle = state.bg; ctx.lineWidth = lw + Math.max(1.5, lw * 0.9); all(); }
    ctx.strokeStyle = state.hlColor; ctx.lineWidth = lw; all();
    // Transferts (train, ferry…) en pointillés
    ctx.lineWidth = Math.max(0.6, lw * 0.5); ctx.setLineDash([0.001, lw * 1.6]);
    ctx.beginPath();
    for (const h of list) for (const i of h.trip.transfers || []) {
      const a = byId.get(h.trip.stages[i]), b = byId.get(h.trip.stages[i + 1]);
      if (!a || !b) continue;
      ctx.moveTo((a.pts[a.pts.length - 2] - view.cx) * s, (a.pts[a.pts.length - 1] - view.cy) * s);
      ctx.lineTo((b.pts[0] - view.cx) * s, (b.pts[1] - view.cy) * s);
    }
    ctx.stroke();
    ctx.restore();
  }

  // Étiquettes (périples puis villes), toujours horizontales même si la carte tourne,
  // sans chevauchement et sans déborder de la carte.
  // Ordre d'affichage des lieux d'étape : départ, arrivée, puis le milieu, les quarts…
  function stopOrder(n) {
    if (n <= 2) return [...Array(n).keys()];
    const order = [0, n - 1], seen = new Set(order);
    let parts = [[0, n - 1]];
    while (parts.length) {
      const next = [];
      for (const [a, b] of parts) {
        const mid = Math.floor((a + b) / 2);
        if (mid > a && mid < b && !seen.has(mid)) { seen.add(mid); order.push(mid); }
        if (mid - a > 1) next.push([a, mid]);
        if (b - mid > 1) next.push([mid, b]);
      }
      parts = next;
    }
    return order;
  }

  function drawLabels(ctx, lay, view, ppm, hl, onLoad, sel) {
    if (!state.cities && !(hl.length && state.hlLabels)) return;
    const m = lay.map, X = v => v * ppm;
    const mapPx = { w: X(m.w), h: X(m.h) }, s = mapPx.w / view.span;
    const th = state.rotation * Math.PI / 180, cos = Math.cos(th), sin = Math.sin(th);
    const ox = X(m.x) + mapPx.w / 2, oy = X(m.y) + mapPx.h / 2;
    const project = (x, y) => {
      const u = (x - view.cx) * s, v = (y - view.cy) * s;
      return [ox + u * cos - v * sin, oy + u * sin + v * cos];
    };
    const size = X(state.citySize);
    const edge = size * 0.5;
    const inside = b => b.x0 >= X(m.x) + edge && b.x1 <= X(m.x + m.w) - edge && b.y0 >= X(m.y) + edge && b.y1 <= X(m.y + m.h) - edge;
    const placed = [];
    const free = b => !placed.some(p => b.x0 < p.x1 && b.x1 > p.x0 && b.y0 < p.y1 && b.y1 > p.y0);

    function label(str, px, py, o) {
      ctx.font = fontString(o.weight, o.size, o.family);
      if ("letterSpacing" in ctx) ctx.letterSpacing = `${o.upper ? o.size * 0.08 : 0}px`;
      const txt = o.upper ? str.toUpperCase() : str;
      const w = ctx.measureText(txt).width;
      const r = o.dot ? o.size * 0.2 : 0;
      const gap = o.dot ? o.size * (o.hideDot ? 0.9 : 0.45) : 0;
      const sides = o.dot ? [1, -1] : [0];
      for (const side of sides) {
        const x0 = side > 0 ? px + gap : side < 0 ? px - gap - w : px - w / 2;
        const b = { x0: Math.min(x0, px - r) - o.size * 0.2, x1: Math.max(x0 + w, px + r) + o.size * 0.2, y0: py - o.size * 0.62, y1: py + o.size * 0.62 };
        if (!inside(b) || !free(b)) continue;
        placed.push(b);
        ctx.textAlign = "left"; ctx.textBaseline = "middle";
        if (state.labelHalo) {
          ctx.lineJoin = "round"; ctx.strokeStyle = state.bg; ctx.lineWidth = o.size * 0.32;
          ctx.strokeText(txt, x0, py + o.size * 0.04);
        }
        ctx.fillStyle = o.color;
        ctx.fillText(txt, x0, py + o.size * 0.04);
        if (o.dot && !o.hideDot) {  // hideDot : le marqueur de départ/arrivée est déjà dessiné
          ctx.beginPath(); ctx.arc(px, py, r, 0, 2 * Math.PI);
          if (state.labelHalo) { ctx.lineWidth = r * 0.9; ctx.stroke(); }
          ctx.fill();
        }
        return true;
      }
      return false;
    }

    ctx.save();
    ctx.beginPath(); ctx.rect(X(m.x), X(m.y), mapPx.w, mapPx.h); ctx.clip();
    if (state.hlLabels) {
      for (const h of hl) {
        const p = h.stages[0].pts, [x, y] = project(p[0], p[1]);
        label(h.trip.title, x, y, { size: size * 1.1, weight: 700, family: COND, color: state.hlColor, dot: true, upper: false });
      }
    }
    const stops = state.cities && state.cityMode === "stops" && sel && sel.trip ? sel.trip.stops || [] : [];
    if (stops.length) {
      // Villes-étapes : départ et arrivée en évidence, puis des étapes réparties sur le parcours
      let count = 0;
      for (const i of stopOrder(stops.length)) {
        const ends = i === 0 || i === stops.length - 1;
        if (!ends && count >= state.cityCount) break;
        const [lat, lng, name] = stops[i];
        const [x, y] = project(...mercator(lat, lng));
        if (label(name, x, y, {
          size: ends ? size * 1.15 : size * 0.92, weight: ends ? 700 : 500, family: state.cityUpper ? COND : SANS,
          color: state.cityColor, dot: state.cityDots || (ends && state.endpoints), hideDot: ends && state.endpoints, upper: state.cityUpper,
        }) && !ends) count++;
      }
    }
    const rows = state.cities && !stops.length ? geoData("cities", onLoad) : null;
    if (rows) {
      const box = viewBox(view, mapPx, s);
      let count = 0;
      for (const c of rows) {
        if (count >= state.cityCount) break;
        const [name, lat, lng, , , capital] = c;
        if (!visible(box, lng, lat, lng, lat)) continue;
        const [x, y] = project(...mercator(lat, lng));
        if (label(name, x, y, {
          size: capital ? size * 1.08 : size, weight: capital ? 600 : 500, family: state.cityUpper ? COND : SANS,
          color: state.cityColor, dot: state.cityDots, upper: state.cityUpper,
        })) count++;
      }
    }
    if ("letterSpacing" in ctx) ctx.letterSpacing = "0px";
    ctx.restore();
  }

  /** Dessine l'affiche entière sur ctx ; l'échelle (pixels par mm) vient de la largeur du canevas. */
  function render(ctx, onTileLoad, maxTiles = 160) {
    const { sel, auto, texts, lay } = compose();
    const ppm = ctx.canvas.width / lay.W;
    const X = v => v * ppm;
    const view = state.view;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1; ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = state.frame === "mat" ? state.matColor : state.bg;
    ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height);
    ctx.fillStyle = state.bg;
    ctx.fillRect(X(lay.content.x), X(lay.content.y), X(lay.content.w), X(lay.content.h));

    // Carte : découpée à son cadre, repère centré et tourné
    const m = lay.map;
    const mapPx = { w: X(m.w), h: X(m.h) };
    const s = mapPx.w / view.span;
    ctx.save();
    ctx.beginPath(); ctx.rect(X(m.x), X(m.y), mapPx.w, mapPx.h); ctx.clip();
    ctx.translate(X(m.x) + mapPx.w / 2, X(m.y) + mapPx.h / 2);
    ctx.rotate(state.rotation * Math.PI / 180);
    const defs = layerDefs();
    // Fond atténué : couleurs adoucies puis voile de la couleur du fond, pour que le tracé ressorte
    const mute = defs.length ? Math.min(90, Math.max(0, +state.mute || 0)) / 100 : 0;
    if (mute && "filter" in ctx) ctx.filter = `saturate(${(1 - mute * 0.8).toFixed(2)})`;
    for (const { def, alpha } of defs) {
      ctx.globalAlpha = alpha;
      for (const t of tilePlan(def, view, mapPx, maxTiles)) {
        const tl = tile(t.url);
        if (tl.ok) ctx.drawImage(tl.img, t.x, t.y, t.size + 0.6, t.size + 0.6);
        else if (!tl.failed && onTileLoad) tl.promise.then(onTileLoad);
      }
    }
    if ("filter" in ctx) ctx.filter = "none";
    if (mute) {
      const big = Math.hypot(mapPx.w, mapPx.h);
      ctx.globalAlpha = mute * 0.75; ctx.fillStyle = state.bg;
      ctx.fillRect(-big, -big, 2 * big, 2 * big);
    }
    ctx.globalAlpha = 1;
    drawBorders(ctx, view, s, ppm, mapPx, onTileLoad);
    drawTracks(ctx, sel, view, s, ppm);
    const hl = highlighted(sel);
    drawHighlights(ctx, hl, view, s, ppm);
    ctx.restore();
    try { drawLabels(ctx, lay, view, ppm, hl, onTileLoad, sel); } catch (err) { console.error(err); ctx.restore(); }
    drawAttribution(ctx, lay, ppm, defs);

    if (!sel.routes.length) {
      ctx.fillStyle = state.ink; ctx.globalAlpha = 0.6; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.font = fontString(500, X(lay.k * 7), SANS);
      ctx.fillText("Aucun tracé pour cette sélection", X(m.x + m.w / 2), X(m.y + m.h / 2));
      ctx.globalAlpha = 1;
    }
    drawText(ctx, lay, texts, ppm);
    drawFrame(ctx, lay, ppm);
    drawBrand(ctx, lay, ppm);
    return { sel, auto, lay, defs };
  }

  // --- Aperçu ---------------------------------------------------------------
  let frame = 0, last = null;
  function redraw() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      const [W, H] = paper();
      const box = stage.getBoundingClientRect();
      const pad = window.innerWidth > 800 ? 64 : 24;
      const scale = Math.min((box.width - pad) / W, (box.height - pad) / H);
      const cssW = Math.max(50, Math.floor(W * scale)), cssH = Math.max(50, Math.floor(H * scale));
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.style.width = cssW + "px"; canvas.style.height = cssH + "px";
      if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
        canvas.width = Math.round(cssW * dpr); canvas.height = Math.round(cssH * dpr);
      }
      last = render(canvas.getContext("2d"), redraw);
      refreshPanel(last);
      save();
    });
  }
  new ResizeObserver(redraw).observe(stage);

  // Glisser pour déplacer, molette ou pincement pour zoomer
  const pointers = new Map();
  let pinch = null;
  function mapScale() {
    // pixels CSS par unité « monde » dans l'aperçu
    const ppmCss = parseFloat(canvas.style.width) / paper()[0];
    return { s: last.lay.map.w * ppmCss / state.view.span, ppmCss };
  }
  function toWorld(dx, dy) {
    const th = -state.rotation * Math.PI / 180, { s } = mapScale();
    return [(dx * Math.cos(th) - dy * Math.sin(th)) / s, (dx * Math.sin(th) + dy * Math.cos(th)) / s];
  }
  function zoomAt(factor, clientX, clientY) {
    if (!last) return;
    const rect = canvas.getBoundingClientRect(), { ppmCss } = mapScale();
    const m = last.lay.map;
    const px = clientX === undefined ? 0 : clientX - rect.left - (m.x + m.w / 2) * ppmCss;
    const py = clientY === undefined ? 0 : clientY - rect.top - (m.y + m.h / 2) * ppmCss;
    const [wx, wy] = toWorld(px, py);
    const v = state.view;
    state.view = { cx: v.cx + wx * (1 - factor), cy: v.cy + wy * (1 - factor), span: Math.min(1.5, Math.max(1e-6, v.span * factor)) };
    state.manual = true;
    redraw();
  }
  canvas.addEventListener("pointerdown", e => {
    canvas.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    canvas.classList.add("is-dragging");
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinch = Math.hypot(a[0] - b[0], a[1] - b[1]);
    }
  });
  canvas.addEventListener("pointermove", e => {
    const prev = pointers.get(e.pointerId);
    if (!prev || !last) return;
    pointers.set(e.pointerId, [e.clientX, e.clientY]);
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (pinch) zoomAt(pinch / d, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2);
      pinch = d;
      return;
    }
    const [wx, wy] = toWorld(e.clientX - prev[0], e.clientY - prev[1]);
    state.view = { ...state.view, cx: state.view.cx - wx, cy: state.view.cy - wy };
    state.manual = true;
    redraw();
  });
  const release = e => {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinch = null;
    if (!pointers.size) canvas.classList.remove("is-dragging");
  };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  canvas.addEventListener("wheel", e => {
    e.preventDefault();
    zoomAt(Math.exp(Math.max(-1, Math.min(1, e.deltaY * (e.deltaMode ? 0.05 : 0.0015)))), e.clientX, e.clientY);
  }, { passive: false });
  document.getElementById("zoom-in").addEventListener("click", () => zoomAt(1 / 1.25));
  document.getElementById("zoom-out").addEventListener("click", () => zoomAt(1.25));
  document.getElementById("fit-btn").addEventListener("click", () => { state.manual = false; redraw(); });

  // --- Panneau --------------------------------------------------------------
  const field = name => form.elements[name];
  const baseSelect = document.getElementById("base-select");
  for (const l of LAYERS) baseSelect.insertAdjacentHTML("beforeend", `<option value="${l.key}">${l.label}</option>`);

  const activitySelect = document.getElementById("activity-select");
  const activitySearch = document.getElementById("activity-search");
  const dateFmt = new Intl.DateTimeFormat("fr-FR", { day: "numeric", month: "short", year: "numeric" });
  const newestFirst = ROUTES.slice().reverse();
  // La liste est reconstruite à chaque frappe (Safari ignore les <option hidden>)
  function fillActivities() {
    const words = searchWords(activitySearch.value);
    const found = newestFirst.filter(r => matches(r, words));
    activitySelect.innerHTML = found.length
      ? found.map(r => `<option value="${r.id}">${dateFmt.format(new Date(r.day + "T12:00"))} · ${escapeHtml(r.name)} (${nf1.format(r.km)} km)</option>`).join("")
      : `<option value="" disabled>Aucune activité ne correspond</option>`;
    if (found.length && !found.some(r => r.id === state.activity)) set({ activity: found[0].id });
    else activitySelect.value = found.length ? state.activity : "";
  }
  activitySearch.addEventListener("input", fillActivities);

  const years = [...new Set(ROUTES.map(r => r.day.slice(0, 4)))].sort().reverse();
  const yearBox = document.getElementById("year-picks");
  yearBox.innerHTML = `<button type="button" data-year="">Tout</button>` + years.map(y => `<button type="button" data-year="${y}">${y}</button>`).join("");
  yearBox.addEventListener("click", e => {
    const b = e.target.closest("[data-year]");
    if (!b) return;
    const y = b.dataset.year;
    set({ from: y ? `${y}-01-01` : "", to: y ? `${y}-12-31` : "" }, true);
  });

  const NUMERIC = new Set(["mute", "air", "width", "opacity", "margin", "rotation", "borderWidth", "borderOpacity", "cityCount", "citySize", "hlWidth"]);
  const CHECKS = new Set(["casing", "stageDots", "profile", "relief", "glow", "endpoints", "countries", "regions", "coast", "cities", "cityUpper", "cityDots",
                          "labelHalo", "hl", "hlCasing", "hlLabels"]);
  const RESHAPE = new Set(["air", "profile", "q", "from", "to", "journey", "activity", "format", "orientation", "frame", "margin", "textPos", "rotation"]);
  function syncFields() {
    for (const el of form.elements) {
      if (!el.name || !(el.name in state)) continue;
      if (CHECKS.has(el.name)) el.checked = !!state[el.name];
      else el.value = state[el.name] ?? "";
    }
  }
  function set(changes, reshape) {
    const sourceChange = ["source", "journey", "activity"].some(k => k in changes && changes[k] !== state[k]);
    Object.assign(state, changes);
    if (sourceChange) Object.assign(state, { title: "", subtitle: "", stats: "" });
    if (reshape || sourceChange) state.manual = false;
    syncFields();
    redraw();
  }
  form.addEventListener("input", e => {
    const el = e.target;
    if (!el.name || !(el.name in state)) return;
    let v = CHECKS.has(el.name) ? el.checked : el.value;
    if (NUMERIC.has(el.name)) v = parseFloat(v);
    if (el.name === "journey" || el.name === "activity") v = parseInt(v, 10);
    // La rotation tourne autour du centre actuel : on garde le cadrage choisi
    if (el.name === "rotation") { state.rotation = v; redraw(); return; }
    set({ [el.name]: v }, RESHAPE.has(el.name));
  });
  document.querySelectorAll("[data-source]").forEach(b => b.addEventListener("click", () => set({ source: b.dataset.source })));
  document.querySelectorAll("[data-family].chip").forEach(chip => chip.addEventListener("click", () => {
    const hidden = new Set(state.hidden);
    hidden.has(chip.dataset.family) ? hidden.delete(chip.dataset.family) : hidden.add(chip.dataset.family);
    set({ hidden: [...hidden] }, true);
  }));
  document.querySelectorAll("[data-preset]").forEach(b => b.addEventListener("click", () => {
    const p = { ...PRESETS[b.dataset.preset] };
    p.borderColor = p.cityColor = p.ink;
    if (p.base && !LAYERS.some(l => l.key === p.base)) p.base = "";
    set(p);
  }));
  document.getElementById("reset-btn").addEventListener("click", () => {
    state = { ...DEFAULTS, source: state.source, journey: state.journey, activity: state.activity };
    syncFields();
    redraw();
  });

  // Liste des périples à mettre en évidence : ceux de la sélection, cochés sauf exclusion
  const tripList = document.getElementById("hl-trips");
  let tripKey = "";
  function refreshTrips(sel) {
    const hlBox = form.querySelector("[data-for-hl]");
    if (!hlBox) return;
    hlBox.hidden = !state.hl;
    const trips = tripsInSelection(sel);
    const key = trips.map(t => t.id).join() + "|" + state.hlOff.join();
    if (key === tripKey) return;
    tripKey = key;
    const off = new Set(state.hlOff);
    tripList.innerHTML = trips.length ? trips.map(t => `
      <li><label class="check"><input type="checkbox" data-hl-trip="${t.id}" ${off.has(t.id) ? "" : "checked"}>
        <span>${escapeHtml(t.title)} <span class="muted">${escapeHtml(t.dates)}</span></span></label></li>`).join("")
      : `<li class="muted">Aucun périple dans cette sélection.</li>`;
  }
  if (tripList) {
    tripList.addEventListener("change", e => {
      const id = Number(e.target.dataset.hlTrip);
      const off = new Set(state.hlOff);
      e.target.checked ? off.delete(id) : off.add(id);
      set({ hlOff: [...off] });
    });
    document.querySelectorAll("[data-hl-all]").forEach(b => b.addEventListener("click", () => {
      set({ hlOff: b.dataset.hlAll === "1" ? [] : TRIPS.map(t => t.id) });
    }));
  }

  // Conseil d'orientation : un tracé large remplit mal une affiche en hauteur (et inversement)
  const orientHint = document.getElementById("orient-hint");
  function refreshOrientHint(sel, auto) {
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const r of sel.routes) for (let i = 0; i < r.pts.length; i += 2) {
      const x = r.pts[i], y = r.pts[i + 1];
      if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
    }
    const shape = (x1 - x0) / Math.max(1e-9, y1 - y0);
    // Part de la carte remplie par le tracé, dans chaque sens (bandeau de texte compris)
    const texts = { title: text("title", auto), subtitle: text("subtitle", auto), stats: text("stats", auto) };
    const fill = orientation => {
      const saved = state.orientation;
      state.orientation = orientation;
      const m = layout(texts, sel).map;
      state.orientation = saved;
      const ratio = m.w / m.h;
      return Math.min(shape / ratio, ratio / shape);
    };
    const other = state.orientation === "portrait" ? "landscape" : "portrait";
    const better = isFinite(shape) && sel.routes.length && state.rotation === 0 && fill(other) > fill(state.orientation) * 1.25;
    orientHint.hidden = !better;
    if (!better) return;
    orientHint.querySelector("span").textContent = "Ce tracé remplirait mieux l'affiche";
    const btn = document.getElementById("orient-btn");
    btn.textContent = other === "landscape" ? "en paysage" : "en portrait";
    btn.dataset.want = other;
  }
  document.getElementById("orient-btn").addEventListener("click", e => set({ orientation: e.target.dataset.want }, true));

  function refreshPanel({ sel, auto }) {
    document.querySelectorAll("[data-source]").forEach(b => b.setAttribute("aria-pressed", b.dataset.source === state.source));
    const selBtn = document.querySelector('[data-source="selection"]');
    if (selBtn) selBtn.hidden = !state.selection.length;
    document.querySelectorAll("[data-for-source]").forEach(el => { el.hidden = el.dataset.forSource !== state.source; });
    document.querySelectorAll("[data-for-mode]").forEach(el => { el.hidden = !el.dataset.forMode.split(" ").includes(state.mode); });
    document.querySelectorAll("[data-for-frame]").forEach(el => { el.hidden = !el.dataset.forFrame.split(" ").includes(state.frame); });
    document.querySelectorAll("[data-for-text]").forEach(el => { el.hidden = state.textPos === "none"; });
    document.getElementById("relief-wrap").hidden = !state.base || !BASEMAP.hillshade;
    field("endpoints").closest("label").hidden = state.source === "all";
    field("stageDots").closest("label").hidden = state.source === "all";
    field("profile").closest("label").hidden = state.source === "all";
    document.getElementById("mute-wrap").hidden = !state.base;
    refreshOrientHint(sel, auto);
    document.querySelectorAll("[data-family].chip").forEach(c => c.setAttribute("aria-pressed", !state.hidden.includes(c.dataset.family)));
    yearBox.querySelectorAll("[data-year]").forEach(b => {
      const y = b.dataset.year;
      b.setAttribute("aria-pressed", y ? state.from === `${y}-01-01` && state.to === `${y}-12-31` : !state.from && !state.to);
    });
    for (const key of ["title", "subtitle", "stats"]) field(key).placeholder = auto[key] || "";
    const out = (name, txt) => { form.querySelector(`[data-out=${name}]`).textContent = txt; };
    out("width", `${nf1.format(state.width)} mm`);
    out("opacity", `${Math.round(state.opacity * 100)} %`);
    out("margin", `${state.margin} mm`);
    out("rotation", `${state.rotation}°`);
    out("mute", `${state.mute} %`);
    out("air", `${state.air} %`);
    out("borderWidth", `${nf1.format(state.borderWidth)} mm`);
    out("borderOpacity", `${Math.round(state.borderOpacity * 100)} %`);
    out("cityCount", state.cityCount);
    out("citySize", `${nf1.format(state.citySize)} mm`);
    out("hlWidth", `× ${nf1.format(state.hlWidth)}`);
    form.querySelector("[data-for-borders]").hidden = !(state.countries || state.regions || state.coast);
    form.querySelector("[data-for-borders] [data-dash=countries]").hidden = !state.countries;
    form.querySelector("[data-for-borders] [data-dash=regions]").hidden = !state.regions;
    form.querySelector("[data-for-cities]").hidden = !state.cities;
    form.querySelector("[data-for-labels]").hidden = !(state.cities || (state.hl && state.hlLabels && state.source === "all"));
    refreshTrips(sel);
    const km = sel.routes.reduce((a, r) => a + r.km, 0);
    document.getElementById("poster-summary").textContent = sel.routes.length
      ? `${nf.format(sel.routes.length)} tracé${sel.routes.length > 1 ? "s" : ""}, ${nf.format(km)} km`
      : "Aucun tracé : élargis les filtres.";
    const [w, h] = exportSize();
    document.getElementById("pixel-size").textContent =
      `${nf.format(w)} × ${nf.format(h)} pixels (${nf1.format(w * h / 1e6)} mégapixels)` +
      (w * h > 16.7e6 ? ". Sur iPhone ou iPad, choisis une résolution plus basse." : "");
  }

  // --- Export ---------------------------------------------------------------
  function exportSize() {
    const [W, H] = paper(), dpi = +state.dpi;
    return [Math.round(W / 25.4 * dpi), Math.round(H / 25.4 * dpi)];
  }
  const exportBtn = document.getElementById("export-btn");
  exportBtn.addEventListener("click", async () => {
    const [w, h] = exportSize();
    exportBtn.disabled = true;
    status.textContent = "Préparation de l'image…";
    try {
      await fonts;
      await Promise.all(wantedGeo().map(k => { geoData(k); return geo[k].promise; }));
      const out = document.createElement("canvas");
      out.width = w; out.height = h;
      const ctx = out.getContext("2d");
      if (!ctx) throw new Error("canvas");
      // Toutes les tuiles nécessaires à cette résolution, avant de dessiner
      const { lay } = compose();
      const ppm = w / lay.W, mapPx = { w: lay.map.w * ppm, h: lay.map.h * ppm };
      const plan = layerDefs().flatMap(({ def }) => tilePlan(def, state.view, mapPx, 900));
      for (const t of plan) if (tiles.get(t.url)?.failed) tiles.delete(t.url);  // nouvel essai
      let done = 0;
      await Promise.all(plan.map(t => tile(t.url).promise.then(() => {
        done++;
        status.textContent = `Fond de carte : ${done} / ${plan.length} tuiles…`;
      })));
      const failedTiles = plan.filter(t => tiles.get(t.url).failed).length;
      status.textContent = "Dessin de l'affiche…";
      await new Promise(r => setTimeout(r, 30));
      render(ctx, null, 900);
      const blob = await new Promise(resolve => out.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("toBlob");
      const file = await withDpi(blob, +state.dpi);
      const a = document.createElement("a");
      a.href = URL.createObjectURL(file);
      a.download = `affiche-${slug(last ? text("title", last.auto) : "") || "off-the-line"}-${state.format}.png`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      const [W, H] = paper();
      status.textContent = `Image enregistrée : ${nf.format(w)} × ${nf.format(h)} px, à imprimer en ${nf1.format(W / 10)} × ${nf1.format(H / 10)} cm.` +
        (failedTiles ? ` Attention : ${failedTiles} morceau${failedTiles > 1 ? "x" : ""} du fond de carte n'${failedTiles > 1 ? "ont" : "a"} pas pu être chargé${failedTiles > 1 ? "s" : ""} : réessaie, ou choisis un autre fond de carte.` : "");
    } catch (err) {
      console.error(err);
      status.textContent = err && err.name === "SecurityError"
        ? "Ce fond de carte n'autorise pas l'export d'image : choisis-en un autre, ou « Uni »."
        : "L'image est trop grande pour ce navigateur : choisis une résolution ou un format plus petit.";
    } finally {
      exportBtn.disabled = false;
    }
  });

  // Résolution inscrite dans le PNG (bloc pHYs) : l'imprimeur retrouve la bonne taille
  const CRC = (() => {
    const t = new Uint32Array(256);
    for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; }
    return t;
  })();
  function crc32(bytes) {
    let c = 0xffffffff;
    for (const b of bytes) c = CRC[(c ^ b) & 255] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }
  async function withDpi(blob, dpi) {
    const src = new Uint8Array(await blob.arrayBuffer());
    const view = new DataView(src.buffer);
    // Déjà présent ? (on parcourt les blocs jusqu'aux données d'image)
    for (let p = 8; p < src.length - 8;) {
      const len = view.getUint32(p), type = String.fromCharCode(...src.subarray(p + 4, p + 8));
      if (type === "pHYs") return blob;
      if (type === "IDAT") break;
      p += 12 + len;
    }
    const chunk = new Uint8Array(21), d = new DataView(chunk.buffer);
    const ppmeter = Math.round(dpi / 0.0254);
    d.setUint32(0, 9);
    chunk.set([0x70, 0x48, 0x59, 0x73], 4);  // « pHYs »
    d.setUint32(8, ppmeter); d.setUint32(12, ppmeter); chunk[16] = 1;
    d.setUint32(17, crc32(chunk.subarray(4, 17)));
    const at = 8 + 12 + view.getUint32(8);  // juste après IHDR
    const outBytes = new Uint8Array(src.length + 21);
    outBytes.set(src.subarray(0, at)); outBytes.set(chunk, at); outBytes.set(src.subarray(at), at + 21);
    return new Blob([outBytes], { type: "image/png" });
  }

  function slug(s) {
    return s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 50);
  }
  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  const fonts = Promise.all([
    document.fonts.load(`700 40px "Barlow Condensed"`), document.fonts.load(`600 40px "Barlow Condensed"`),
    document.fonts.load(`500 40px "Barlow"`), document.fonts.load(`400 40px "Barlow"`),
  ]).catch(() => null);
  fillActivities();
  syncFields();
  fonts.then(redraw);
  redraw();
})();
