/* Fonds de carte vectoriels Off the line.

   Les données viennent de tuiles vectorielles au schéma OpenMapTiles (OpenFreeMap par défaut,
   ou un fichier .pmtiles hébergé chez nous) et du relief Terrarium ; le dessin est entièrement
   le nôtre : ce fichier fabrique un style MapLibre à partir de quelques couleurs.

   Chaque style (défini côté serveur, BASEMAP.vector.styles) donne sept couleurs « graines » :
   land (terre), water (eau), nature (forêts, prairies), urban (bâti), road (routes),
   label (textes) et shadow (ombre du relief), plus quelques dosages. Toutes les autres teintes
   en sont dérivées par mélange avec la couleur de la terre : changer une graine (personnalisation
   de l'affiche) garde donc une carte cohérente.

   OTLStyles.build(key, options) renvoie le style ; voir DEFAULTS pour les options. */
(() => {
  const data = document.getElementById("basemap-data");
  const CFG = ((typeof BASEMAP !== "undefined" && BASEMAP) || (data && JSON.parse(data.textContent)) || {}).vector || null;
  if (!CFG || !window.maplibregl) return;

  // --- Couleurs ---------------------------------------------------------------
  function rgb(hex) {
    let h = String(hex || "#000").replace("#", "");
    if (h.length === 3) h = h.split("").map(c => c + c).join("");
    const n = parseInt(h, 16) || 0;
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  const hex = c => "#" + c.map(v => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
  /** Mélange a → b (t = 0 : a, t = 1 : b). */
  const mix = (a, b, t) => { const A = rgb(a), B = rgb(b); return hex(A.map((v, i) => v + (B[i] - v) * t)); };
  const lum = c => { const [r, g, b] = rgb(c); return (0.299 * r + 0.587 * g + 0.114 * b) / 255; };
  const alpha = (c, a) => { const [r, g, b] = rgb(c); return `rgba(${r},${g},${b},${a})`; };

  /** Palette complète à partir des graines du style et des couleurs choisies par l'utilisateur. */
  function palette(def, colors) {
    const c = { ...def.colors, ...(colors || {}) };
    const k = { nature: 0.5, urban: 0.7, ...def, ...(colors && colors.nature ? { nature: Math.max(def.nature || 0, 0.45) } : {}) };
    const dark = lum(c.land) < 0.42;
    const land = c.land;
    const toward = dark ? "#ffffff" : "#000000";
    return {
      dark,
      land,
      water: c.water,
      waterLine: mix(c.water, toward, dark ? 0.08 : 0.06),
      waterOutline: mix(c.water, c.label, 0.55),
      waterLabel: mix(c.water, c.label, dark ? 0.55 : 0.6),
      wood: mix(land, c.nature, k.nature),
      grass: mix(land, c.nature, k.nature * 0.55),
      farm: mix(land, c.nature, k.nature * 0.22),
      park: mix(land, c.nature, k.nature * 0.4),
      parkLine: mix(land, c.nature, Math.min(1, k.nature * 1.3 + 0.15)),
      wetland: mix(land, c.water, 0.35),
      sand: mix(land, "#e6d3a3", dark ? 0.12 : 0.45),
      rock: mix(land, c.shadow, dark ? 0.12 : 0.08),
      ice: dark ? mix(land, "#ffffff", 0.12) : mix(land, "#ffffff", 0.75),
      iceLine: mix(c.water, land, 0.25),
      residential: mix(land, c.urban, k.urban * 0.32),
      industrial: mix(land, c.urban, k.urban * 0.48),
      building: mix(land, c.urban, k.urban),
      buildingLine: mix(land, c.urban, Math.min(1, k.urban * 1.25)),
      motorway: c.road,
      major: c.road,
      medium: mix(land, c.road, def.casing ? 0.62 : 0.72),
      minorFill: def.casing ? (dark ? mix(land, "#ffffff", 0.18) : "#ffffff") : mix(land, c.road, 0.45),
      casing: mix(c.road, c.label, 0.35),
      minorCasing: mix(land, c.road, 0.55),
      path: mix(land, c.road, def.casing ? 0.8 : 0.55),
      rail: mix(land, c.label, 0.4),
      boundary: mix(land, c.label, 0.5),
      label: c.label,
      halo: alpha(land, 0.85),
      shadow: c.shadow,
      highlight: dark ? mix(land, "#ffffff", 0.08) : mix(land, "#ffffff", 0.7),
      contour: mix(land, dark ? c.label : c.shadow, dark ? 0.16 : 0.22),
      contourIndex: mix(land, dark ? c.label : c.shadow, dark ? 0.28 : 0.38),
    };
  }

  // --- Sources ----------------------------------------------------------------
  let pmtilesReady = false;
  function vectorSource() {
    const url = CFG.tiles;
    if (url.startsWith("pmtiles://")) {
      if (!pmtilesReady && window.pmtiles) {
        maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);
        pmtilesReady = true;
      }
      return { type: "vector", url };
    }
    if (url.includes("{z}")) return { type: "vector", tiles: [url], maxzoom: 14 };
    return { type: "vector", url };
  }

  // Une seule source de relief pour toutes les cartes de la page (cache partagé),
  // qui sert à l'ombrage et, via maplibre-contour, aux courbes de niveau.
  let dem = null;
  function demSource() {
    if (dem || !CFG.dem) return dem;
    if (window.mlcontour) {
      const src = new mlcontour.DemSource({
        url: CFG.dem.url, encoding: CFG.dem.encoding || "terrarium", maxzoom: CFG.dem.maxzoom || 12, worker: true,
      });
      src.setupMaplibre(maplibregl);
      dem = { tiles: src.sharedDemProtocolUrl, contour: src };
    } else {
      dem = { tiles: CFG.dem.url, contour: null };
    }
    return dem;
  }

  // --- Expressions --------------------------------------------------------------
  const Z = ["zoom"];
  const cls = (...values) => ["match", ["get", "class"], values, true, false];
  /** Largeur selon le zoom (pixels CSS), multipliée par `scale`. */
  const width = (scale, ...stops) => {
    const out = ["interpolate", ["exponential", 1.55], Z];
    for (let i = 0; i < stops.length; i += 2) out.push(stops[i], +(stops[i + 1] * scale).toFixed(3));
    return out;
  };
  const step = (...stops) => ["interpolate", ["linear"], Z, ...stops];

  const FONT = { regular: ["Noto Sans Regular"], bold: ["Noto Sans Bold"], italic: ["Noto Sans Italic"] };

  const DEFAULTS = {
    colors: null,       // graines remplacées par l'utilisateur ({ land: "#…", … })
    labels: true,       // noms de lieux, d'eau, de sommets
    relief: null,       // intensité de l'ombrage 0–1 (null : celle du style)
    contours: null,     // courbes de niveau (null : selon le style)
    roads: "all",       // "all", "main" (sans rues ni chemins) ou "none"
    paths: true,        // sentiers et pistes
    buildings: true,
    nature: true,       // forêts, prairies, parcs
    boundaries: true,   // frontières et limites de régions
    roadScale: 1,       // épaisseur des routes
    lang: "fr",         // "fr" (nom français s'il existe) ou "local"
    print: false,       // rendu pour l'impression (affiche, livre) : sans texte, découpable en morceaux
  };

  function build(key, options) {
    const styles = CFG.styles;
    const def = styles.find(s => s.key === key) || styles[0];
    const o = { ...DEFAULTS, ...(options || {}) };
    const p = palette(def, o.colors);
    const relief = o.relief === null || o.relief === undefined ? (def.relief || 0) : +o.relief;
    const contours = o.contours === null || o.contours === undefined ? !!def.contours : !!o.contours;
    const labels = o.labels && !o.print;
    const name = o.lang === "local"
      ? ["coalesce", ["get", "name"], ["get", "name:latin"]]
      : ["coalesce", ["get", "name:fr"], ["get", "name:latin"], ["get", "name"]];
    const rs = (o.roadScale || 1) * (def.roadScale || 1);
    const casing = !!def.casing;

    const sources = { omt: vectorSource() };
    const d = (relief > 0 || contours) ? demSource() : null;
    if (d) {
      sources.dem = { type: "raster-dem", tiles: [d.tiles], encoding: CFG.dem.encoding || "terrarium", tileSize: 256,
                      maxzoom: CFG.dem.maxzoom || 12 };
      if (contours && d.contour) {
        // À l'écran, pas de courbes avant le zoom 11 : leur calcul ralentirait les zooms
        sources.contours = {
          type: "vector", maxzoom: 15, ...(o.print ? {} : { minzoom: 11 }),
          tiles: [d.contour.contourProtocolUrl({
            multiplier: 1, elevationKey: "ele", levelKey: "level", contourLayer: "contours",
            thresholds: { 9: [200, 1000], 10: [100, 500], 11: [100, 500], 12: [50, 250], 13: [20, 100], 14: [10, 50], 15: [10, 50] },
          })],
        };
      }
    }

    const L = [];
    const add = layer => L.push(layer);
    const omt = (id, layer, type, extra) => add({ id, type, source: "omt", "source-layer": layer, ...extra });

    add({ id: "land", type: "background", paint: { "background-color": p.land } });

    // Couverture du sol
    if (o.nature) {
      omt("farmland", "landcover", "fill", { filter: cls("farmland"), minzoom: 8, paint: { "fill-color": p.farm } });
      omt("grass", "landcover", "fill", { filter: cls("grass"), paint: { "fill-color": p.grass } });
      omt("wood", "landcover", "fill", { filter: cls("wood", "forest"), paint: { "fill-color": p.wood, "fill-antialias": false } });
      omt("wetland", "landcover", "fill", { filter: cls("wetland"), paint: { "fill-color": p.wetland } });
    }
    omt("sand", "landcover", "fill", { filter: cls("sand"), paint: { "fill-color": p.sand } });
    omt("rock", "landcover", "fill", { filter: cls("rock"), paint: { "fill-color": p.rock } });
    omt("ice", "landcover", "fill", { filter: cls("ice"), paint: { "fill-color": p.ice } });
    omt("ice-line", "landcover", "line", { filter: cls("ice"), minzoom: 10,
      paint: { "line-color": p.iceLine, "line-width": step(10, 0.3, 14, 0.8), "line-dasharray": [3, 2] } });
    if (o.buildings || o.roads === "all") {
      omt("residential", "landuse", "fill", { filter: cls("residential", "suburb", "quarter", "neighbourhood"), minzoom: 6,
        paint: { "fill-color": p.residential, "fill-opacity": step(6, 0.5, 12, 1, 15, 0.6) } });
      omt("industrial", "landuse", "fill", { filter: cls("industrial", "commercial", "retail", "railway", "quarry"), minzoom: 9,
        paint: { "fill-color": p.industrial, "fill-opacity": step(9, 0.6, 13, 1) } });
    }
    if (o.nature) {
      omt("park", "park", "fill", { paint: { "fill-color": p.park, "fill-opacity": 0.6 } });
      omt("park-line", "park", "line", { minzoom: 8,
        paint: { "line-color": p.parkLine, "line-width": step(8, 0.4, 14, 1.2), "line-opacity": 0.55, "line-dasharray": [4, 2] } });
    }

    // Relief : ombrage multidirectionnel, sous l'eau et les routes
    if (d && relief > 0) {
      add({ id: "hillshade", type: "hillshade", source: "dem", paint: {
        "hillshade-method": "igor",
        "hillshade-exaggeration": Math.min(1, 0.15 + relief * 0.85),
        "hillshade-shadow-color": alpha(p.shadow, Math.min(1, 0.35 + relief * 0.55)),
        "hillshade-highlight-color": alpha(p.highlight, p.dark ? 0.25 * relief : 0.55 * relief),
        "hillshade-accent-color": alpha(p.shadow, 0.4 * relief),
        "hillshade-illumination-direction": 315,
      } });
    }
    if (sources.contours) {
      add({ id: "contours", type: "line", source: "contours", "source-layer": "contours", filter: ["<", ["get", "level"], 1],
        paint: { "line-color": p.contour, "line-width": step(11, 0.35, 15, 0.7) } });
      add({ id: "contours-index", type: "line", source: "contours", "source-layer": "contours", filter: [">=", ["get", "level"], 1],
        paint: { "line-color": p.contourIndex, "line-width": step(9, 0.5, 15, 1.2) } });
    }

    // Eau
    omt("water", "water", "fill", { filter: ["!", cls("swimming_pool")],
      paint: { "fill-color": p.water, "fill-antialias": true, "fill-outline-color": def.waterOutline ? p.waterOutline : p.water } });
    if (def.waterOutline) {
      omt("water-outline", "water", "line", { filter: ["!", cls("swimming_pool")], minzoom: 6,
        paint: { "line-color": p.waterOutline, "line-width": step(6, 0.4, 14, 1) } });
    }
    omt("river", "waterway", "line", { filter: cls("river"), minzoom: 7,
      paint: { "line-color": p.waterLine, "line-width": width(1, 8, 0.5, 12, 1.4, 16, 5), "line-opacity": step(7, 0.6, 10, 1) },
      layout: { "line-cap": "round" } });
    omt("canal", "waterway", "line", { filter: cls("canal"), minzoom: 10,
      paint: { "line-color": p.waterLine, "line-width": width(1, 10, 0.5, 16, 3) } });
    omt("stream", "waterway", "line", { filter: cls("stream", "drain", "ditch"), minzoom: 12,
      paint: { "line-color": p.waterLine, "line-width": width(1, 12, 0.4, 16, 1.6), "line-opacity": 0.85 } });

    // Limites
    if (o.boundaries) {
      omt("boundary-region", "boundary", "line", { minzoom: 5,
        filter: ["all", ["match", ["get", "admin_level"], [3, 4], true, false], ["!=", ["get", "maritime"], 1]],
        paint: { "line-color": p.boundary, "line-width": step(5, 0.4, 12, 1.1), "line-dasharray": [3, 2, 1, 2], "line-opacity": 0.55 } });
      omt("boundary-country", "boundary", "line", {
        filter: ["all", ["==", ["get", "admin_level"], 2], ["!=", ["get", "maritime"], 1], ["!=", ["get", "disputed"], 1]],
        paint: { "line-color": p.boundary, "line-width": step(2, 0.6, 6, 1.1, 12, 2), "line-opacity": 0.75 },
        layout: { "line-join": "round" } });
    }

    // Routes : de la plus petite à la plus grande, avec bordure pour les styles qui en ont
    omt("runway", "aeroway", "line", { filter: cls("runway", "taxiway"), minzoom: 11,
      paint: { "line-color": p.industrial, "line-width": width(1, 11, 1, 16, 14) } });
    const ROADS = [
      { id: "path", filter: cls("path", "track", "bridleway"), min: 12, color: p.path, w: [12, 0.35, 15, 0.9, 18, 1.8], dash: [2, 1.5],
        when: o.roads === "all" && o.paths },
      { id: "minor", filter: cls("minor", "service", "living_street", "pedestrian"), min: 11, color: p.minorFill, case: p.minorCasing, caseMin: 13,
        w: [11, 0.25, 13, 0.7, 15, 2.6, 18, 12], when: o.roads === "all" },
      { id: "tertiary", filter: cls("tertiary"), min: 9, color: p.medium, case: p.casing,
        w: [9, 0.25, 11, 0.6, 14, 2.2, 18, 12], when: o.roads === "all" },
      { id: "medium", filter: cls("secondary"), min: 8, color: p.medium, case: p.casing,
        w: [8, 0.3, 11, 0.8, 14, 2.6, 18, 14], when: o.roads !== "none" },
      { id: "major", filter: cls("primary", "trunk"), min: 6, color: p.major, case: p.casing,
        w: [6, 0.35, 10, 1.1, 14, 3.4, 18, 18], when: o.roads !== "none" },
      { id: "motorway", filter: cls("motorway"), min: 5, color: p.motorway, case: p.casing,
        w: [5, 0.45, 10, 1.5, 14, 4, 18, 20], when: o.roads !== "none" },
    ];
    const visible = ROADS.filter(r => r.when);
    if (casing) {
      for (const r of visible) {
        if (!r.case) continue;
        const w = r.w.map((v, i) => (i % 2 ? v + (v < 1 ? 0.6 : 1.4) : v));
        const from = r.caseMin || 10;
        omt(`${r.id}-casing`, "transportation", "line", { filter: r.filter, minzoom: Math.max(r.min, from),
          paint: { "line-color": r.case, "line-width": width(rs, ...w), "line-opacity": step(from, 0, from + 1, 1) },
          layout: { "line-cap": "round", "line-join": "round" } });
      }
    }
    for (const r of visible) {
      omt(r.id, "transportation", "line", { filter: r.filter, minzoom: r.min,
        paint: { "line-color": r.id === "minor" && casing ? ["step", Z, p.minorCasing, 13, r.color] : r.color,
                 "line-width": width(rs, ...r.w), ...(r.dash ? { "line-dasharray": r.dash } : {}),
                 "line-opacity": r.id === "minor" && !casing ? step(11, 0.4, 13, 1) : 1 },
        layout: { "line-cap": r.dash ? "butt" : "round", "line-join": "round" } });
    }
    if (o.roads !== "none") {
      omt("rail", "transportation", "line", { filter: cls("rail"), minzoom: 9,
        paint: { "line-color": p.rail, "line-width": width(rs, 9, 0.4, 14, 1.1, 18, 2.4) } });
      omt("rail-ties", "transportation", "line", { filter: cls("rail"), minzoom: 13,
        paint: { "line-color": p.rail, "line-width": width(rs, 13, 2.5, 18, 7), "line-dasharray": [0.15, 3] } });
      omt("ferry", "transportation", "line", { filter: cls("ferry"), minzoom: 9,
        paint: { "line-color": p.waterOutline, "line-width": step(9, 0.5, 14, 1), "line-dasharray": [3, 3], "line-opacity": 0.4 } });
    }

    if (o.buildings) {
      omt("building", "building", "fill", { minzoom: 13,
        paint: { "fill-color": p.building, "fill-outline-color": p.buildingLine, "fill-opacity": step(13, 0, 14, 1) } });
    }

    // Textes (cartes à l'écran seulement)
    if (labels) {
      const text = (size, font, extra) => ({ "text-field": name, "text-font": font, "text-size": size, ...extra });
      const paint = (color, halo = 1.4) => ({ "text-color": color, "text-halo-color": p.halo, "text-halo-width": halo, "text-halo-blur": 0.4 });
      if (sources.contours) {
        add({ id: "contour-label", type: "symbol", source: "contours", "source-layer": "contours", minzoom: 13,
          filter: [">=", ["get", "level"], 1],
          layout: { "symbol-placement": "line", "text-field": ["concat", ["to-string", ["get", "ele"]], " m"],
                    "text-font": FONT.regular, "text-size": 9.5, "text-max-angle": 25, "text-padding": 60 },
          paint: paint(p.contourIndex, 1.2) });
      }
      omt("water-name-line", "water_name", "symbol", { filter: ["==", ["geometry-type"], "LineString"],
        layout: { ...text(step(8, 10, 14, 14), FONT.italic), "symbol-placement": "line", "text-letter-spacing": 0.15 },
        paint: paint(p.waterLabel) });
      omt("water-name", "water_name", "symbol", { filter: ["!=", ["geometry-type"], "LineString"], minzoom: 5,
        layout: { ...text(step(6, 10, 12, 14), FONT.italic), "text-letter-spacing": 0.12, "text-max-width": 8 },
        paint: paint(p.waterLabel) });
      omt("waterway-name", "waterway", "symbol", { filter: cls("river", "canal"), minzoom: 12,
        layout: { ...text(11, FONT.italic), "symbol-placement": "line", "text-letter-spacing": 0.1, "symbol-spacing": 350 },
        paint: paint(p.waterLabel) });
      omt("road-name", "transportation_name", "symbol", { minzoom: 13, filter: ["!", cls("ferry", "rail")],
        layout: { ...text(step(13, 9.5, 17, 12), FONT.regular), "symbol-placement": "line", "text-max-angle": 30 },
        paint: paint(mix(p.label, p.land, 0.2)) });
      omt("peak-dot", "mountain_peak", "symbol", { minzoom: 11, filter: ["has", "name"],
        layout: { "text-field": ["concat", "▲\n", ["coalesce", ["get", "name:fr"], ["get", "name"]],
                                  ["case", ["has", "ele"], ["concat", "\n", ["to-string", ["get", "ele"]], " m"], ""]],
                  "text-font": FONT.regular, "text-size": 10, "text-line-height": 1.15, "text-anchor": "top", "text-offset": [0, -0.6],
                  "symbol-sort-key": ["coalesce", ["get", "rank"], 10] },
        paint: paint(mix(p.label, p.shadow, 0.3)) });
      const upper = def.upper ? { "text-transform": "uppercase", "text-letter-spacing": 0.14 } : { "text-letter-spacing": 0.03 };
      omt("place-village", "place", "symbol", { filter: cls("village", "hamlet", "suburb"), minzoom: 11,
        layout: { ...text(step(11, 10, 15, 13), FONT.regular), "text-max-width": 7,
                  "symbol-sort-key": ["coalesce", ["get", "rank"], 20] },
        paint: paint(mix(p.label, p.land, 0.15)) });
      omt("place-town", "place", "symbol", { filter: cls("town"), minzoom: 8,
        layout: { ...text(step(8, 10.5, 14, 15), FONT.regular), ...upper, "text-max-width": 7,
                  "symbol-sort-key": ["coalesce", ["get", "rank"], 15] },
        paint: paint(p.label) });
      omt("place-city", "place", "symbol", { filter: cls("city"), minzoom: 4,
        layout: { ...text(step(4, 10, 8, 13, 14, 19), FONT.bold), ...upper, "text-max-width": 8,
                  "symbol-sort-key": ["coalesce", ["get", "rank"], 10] },
        paint: paint(p.label, 1.6) });
      omt("place-country", "place", "symbol", { filter: cls("country"), maxzoom: 8,
        layout: { ...text(step(2, 10, 6, 14), FONT.bold), "text-transform": "uppercase", "text-letter-spacing": 0.25, "text-max-width": 6 },
        paint: paint(mix(p.label, p.land, 0.3)) });
    }

    return {
      version: 8,
      name: `Off the line – ${def.label}`,
      glyphs: CFG.glyphs,
      sources,
      layers: L,
    };
  }

  /** Attribution complète d'un style (données + relief si utilisé). */
  function attribution(key, options) {
    const def = CFG.styles.find(s => s.key === key) || CFG.styles[0];
    const o = { ...DEFAULTS, ...(options || {}) };
    const relief = o.relief === null || o.relief === undefined ? (def.relief || 0) : +o.relief;
    const contours = o.contours === null || o.contours === undefined ? !!def.contours : !!o.contours;
    return [CFG.attribution, (relief > 0 || contours) && CFG.dem ? CFG.dem.attribution : ""].filter(Boolean).join(" · ");
  }

  window.OTLStyles = { build, palette, attribution, mix, lum, DEFAULTS, styles: CFG.styles };
})();
