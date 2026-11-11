/* Livre, mode « Personnaliser » : textes modifiables sur la page, photos (bac, glisser-déposer, légendes),
   panneau par étapes. Chargé seulement en édition, après book.js (BOOK, saveBook). */
(() => {
  // Textes modifiables directement sur la page
  function bindTexts(root) {
  for (const el of root.querySelectorAll("[data-edit]")) {
    if (el.dataset.bound) continue;
    el.dataset.bound = "1";
    try { el.contentEditable = "plaintext-only"; } catch (e) { el.contentEditable = "true"; }
    if (el.contentEditable !== "plaintext-only") el.contentEditable = "true";
    el.spellcheck = true;
    let before = el.innerText.trim();
    el.addEventListener("keydown", e => {
      if (e.key === "Enter" && !el.hasAttribute("data-multiline")) { e.preventDefault(); el.blur(); }
      if (e.key === "Escape") { el.innerText = before; el.blur(); }
    });
    el.addEventListener("blur", async () => {
      const value = el.innerText.trim();
      if (value === before) return;
      const ok = await saveBook({ field: el.dataset.edit, value, journey: el.dataset.journey, activity: el.dataset.activity });
      if (ok) before = value;
      if (!value) el.innerText = "";  // affiche le texte d'aide
    });
  }
  }

  bindTexts(document);
  window.bindTexts = bindTexts;
  const esc = t => t.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------- Étapes de la personnalisation ----------
  // Fond de carte, noms, couverture, pages, photos. L'étape en cours est gardée pendant les
  // rechargements (changer de fond ou de pages recharge le livre).
  const STEPS = ["fond", "noms", "couverture", "pages", "photos"];
  const STEP_KEY = `otl-book-step-${BOOK.id}`;
  // Page à montrer dans l'aperçu pour chaque étape
  const SHOW = { fond: ".page.cover", noms: ".page.opener, .stage-page", couverture: ".page.cover",
                 pages: ".page.roadbook, .page.contents, .page.opener", photos: ".stage-page" };
  const studio = document.getElementById("studio");
  const prevBtn = document.getElementById("step-prev"), nextBtn = document.getElementById("step-next");
  const doneBtn = document.getElementById("step-done");
  function goStep(step, scroll = true) {
    if (!STEPS.includes(step)) step = STEPS[0];
    const k = STEPS.indexOf(step);
    document.body.dataset.step = step;
    studio.querySelectorAll("[data-panel]").forEach(p => { p.hidden = p.dataset.panel !== step; });
    studio.querySelectorAll("[data-step]").forEach((b, i) => {
      b.toggleAttribute("aria-current", i === k);
      if (i === k) b.setAttribute("aria-current", "step");
      b.classList.toggle("done", i < k);
    });
    prevBtn.disabled = k === 0;
    nextBtn.hidden = k === STEPS.length - 1;
    doneBtn.hidden = k !== STEPS.length - 1;
    try { sessionStorage.setItem(STEP_KEY, step); } catch (e) {}
    document.getElementById("studio-body").scrollTop = 0;
    // Couverture : on montre le bas de la page, là où sont le titre et le sous-titre
    if (scroll) document.querySelector(SHOW[step])?.scrollIntoView({ block: step === "couverture" ? "end" : "start", behavior: "smooth" });
    if (step !== "noms") stopPlacing();
    window.fitPages && window.fitPages();
  }
  studio.querySelectorAll("[data-step]").forEach(b => b.addEventListener("click", () => goStep(b.dataset.step)));
  prevBtn.addEventListener("click", () => goStep(STEPS[STEPS.indexOf(document.body.dataset.step) - 1]));
  nextBtn.addEventListener("click", () => goStep(STEPS[STEPS.indexOf(document.body.dataset.step) + 1]));
  // Téléphone : le panneau se réduit pour voir l'aperçu en grand
  const minBtn = document.getElementById("studio-min");
  minBtn.addEventListener("click", () => {
    const min = studio.classList.toggle("is-min");
    minBtn.textContent = min ? "Ouvrir" : "Réduire";
    minBtn.setAttribute("aria-expanded", String(!min));
    window.fitPages && window.fitPages();
  });

  // Fond de carte, pages affichées, couverture : enregistrés, puis le livre est rechargé
  async function choose(btn, payload) {
    btn.closest("[role=group]")?.querySelectorAll("[aria-pressed]").forEach(b => b.setAttribute("aria-pressed", String(b === btn)));
    if (await saveBook(payload)) reloadAt();
  }
  studio.querySelectorAll("[data-imagery]").forEach(b => b.addEventListener("click", () => {
    if (b.getAttribute("aria-pressed") === "true") return;
    choose(b, { field: "options", value: { imagery: b.dataset.imagery } });
  }));
  studio.querySelectorAll("[data-cover]").forEach(b => b.addEventListener("click", () => {
    if (b.getAttribute("aria-pressed") === "true") return;
    choose(b, { field: "cover", value: b.dataset.cover });
  }));
  studio.querySelectorAll(".toggle[data-option]").forEach(b => b.addEventListener("click", async () => {
    const on = b.getAttribute("aria-pressed") !== "true";
    b.setAttribute("aria-pressed", String(on));
    if (await saveBook({ field: "options", value: { [b.dataset.option]: on } })) reloadAt();
    else b.setAttribute("aria-pressed", String(!on));
  }));
  // Titre et sous-titre : aussi modifiables sur la couverture
  studio.querySelectorAll("[data-text]").forEach(input => {
    const target = document.querySelector(`.cover [data-edit="${input.dataset.text}"]`);
    let before = input.value.trim();
    const save = async () => {
      const value = input.value.trim();
      if (value === before) return;
      if (await saveBook({ field: input.dataset.text, value })) {
        before = value;
        if (target) target.innerText = value || input.placeholder;
      }
    };
    input.addEventListener("input", () => { if (target) target.innerText = input.value.trim() || input.placeholder; });
    input.addEventListener("change", save);
    input.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); input.blur(); } });
  });

  // ---------- Villes-étapes : renommer, masquer ou réafficher un nom ----------
  // Le choix vaut pour tout le livre (même ville sur la carte du périple et sur celle de l'étape)
  // Applique tout de suite sur les cartes, enregistre, et revient en arrière si l'enregistrement échoue
  async function saveOpts(patch) {
    const prev = Object.fromEntries(Object.keys(patch).map(k => [k, BOOK.options[k]]));
    Object.assign(BOOK.options, patch);
    (window.bookStops || []).forEach(f => f());
    renderTags();
    if (!await saveBook({ field: "options", value: patch })) {
      Object.assign(BOOK.options, prev);
      (window.bookStops || []).forEach(f => f());
      renderTags();
    }
  }
  const saveNames = (hidden, names) => saveOpts({ cityHidden: [...hidden], cityNames: names });

  // Liste des noms dans le panneau (plus simple que de viser un petit nom sur la carte au téléphone)
  const allNames = [];
  for (const m of JSON.parse(document.getElementById("maps-data").textContent)) {
    for (const [, , name] of (m.places || [])) if (!allNames.includes(name)) allNames.push(name);
  }
  const tags = document.getElementById("name-tags");
  function renderTags() {
    const hidden = new Set(BOOK.options.cityHidden || []), names = BOOK.options.cityNames || {};
    const stops = BOOK.options.stops === false ? [] : allNames;
    const items = stops.map(n => {
      const off = hidden.has(n);
      return `<li class="name-tag${off ? " off" : ""}"><button type="button" class="tag-label" data-name="${esc(n)}" title="${off ? "Masqué" : "Renommer"}">${esc(names[n] || n)}</button>`
        + `<button type="button" class="tag-x" data-name="${esc(n)}" aria-label="${off ? "Réafficher" : "Masquer"} ${esc(names[n] || n)}">${off ? "+" : "✕"}</button></li>`;
    }).concat((BOOK.options.cityExtra || []).map(([, , n]) =>
      `<li class="name-tag extra"><button type="button" class="tag-label" data-extra="${esc(n)}" title="Renommer">${esc(n)}</button>`
      + `<button type="button" class="tag-x" data-extra="${esc(n)}" aria-label="Retirer ${esc(n)}">✕</button></li>`));
    tags.innerHTML = items.join("") || `<li class="muted">${BOOK.options.stops === false ? "Villes-étapes masquées." : "Aucun nom pour l'instant."}</li>`;
  }
  function rename(name, value, extra) {
    if (extra) {
      const rows = BOOK.options.cityExtra || [];
      saveOpts({ cityExtra: value ? rows.map(r => (r[2] === name ? [r[0], r[1], value] : r)) : rows.filter(r => r[2] !== name) });
      return;
    }
    const hidden = new Set(BOOK.options.cityHidden || []), names = { ...(BOOK.options.cityNames || {}) };
    if (!value) { hidden.add(name); delete names[name]; }
    else if (value === name) delete names[name];
    else names[name] = value;
    saveNames(hidden, names);
  }
  tags.addEventListener("click", e => {
    const b = e.target.closest("button");
    if (!b) return;
    const extra = "extra" in b.dataset, name = extra ? b.dataset.extra : b.dataset.name;
    const hidden = new Set(BOOK.options.cityHidden || []);
    if (b.classList.contains("tag-x")) {
      if (extra) return rename(name, "", true);
      hidden.has(name) ? hidden.delete(name) : hidden.add(name);
      return saveNames(hidden, BOOK.options.cityNames || {});
    }
    if (!extra && hidden.has(name)) { hidden.delete(name); return saveNames(hidden, BOOK.options.cityNames || {}); }
    // Renommer : le nom devient un champ, Entrée pour valider, Échap pour annuler
    const input = document.createElement("input");
    input.className = "tag-input";
    input.value = b.textContent;
    input.setAttribute("aria-label", `Nouveau nom pour ${name}`);
    b.replaceWith(input);
    input.focus();
    input.select();
    let cancelled = false;
    input.addEventListener("keydown", ev => {
      if (ev.key === "Enter") { ev.preventDefault(); input.blur(); }
      if (ev.key === "Escape") { cancelled = true; input.blur(); }
    });
    input.addEventListener("blur", () => {
      const value = input.value.trim();
      if (cancelled || value === b.textContent) return renderTags();
      rename(name, value, extra);
    }, { once: true });
  });
  renderTags();

  // ---------- Ajouter un lieu (col, bivouac, village…) : recherche, ou placement en touchant une carte ----------
  const fInput = document.getElementById("place-q"), fList = document.getElementById("place-results");
  let fTimer = 0, fSeq = 0, placing = null;
  // La carte la plus visible dans l'aperçu : la recherche cherche autour d'elle
  function visibleMap() {
    let best = null, most = -1;
    for (const inst of Object.values(window.bookMaps || {})) {
      const r = inst.map.getContainer().getBoundingClientRect();
      const seen = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
      if (seen > most) { most = seen; best = inst.map; }
    }
    return best;
  }
  function stopPlacing() {
    placing = null;
    document.body.classList.remove("is-placing");
  }
  function addPlace(lat, lng, name) {
    const rows = (BOOK.options.cityExtra || []).filter(r => r[2] !== name);
    saveOpts({ cityExtra: [...rows, [+lat.toFixed(4), +lng.toFixed(4), name]] });
    fInput.value = "";
    fList.innerHTML = "";
    stopPlacing();
  }
  for (const inst of Object.values(window.bookMaps || {})) {
    inst.map.on("click", e => {
      if (placing && document.body.dataset.step === "noms") addPlace(e.latlng.lat, e.latlng.lng, placing);
    });
  }
  fInput.addEventListener("input", () => {
    clearTimeout(fTimer);
    const typed = fInput.value.trim();
    placing = typed || null;
    document.body.classList.toggle("is-placing", !!placing);
    if (typed.length < 2) { fList.innerHTML = ""; return; }
    fTimer = setTimeout(async () => {
      const seq = ++fSeq, map = visibleMap(), c = map ? map.getCenter() : { lat: 46.6, lng: 2.4 };
      let found = [];
      try {
        const r = await fetch(`${BOOK.towns}?q=${encodeURIComponent(typed)}&lat=${c.lat.toFixed(3)}&lng=${c.lng.toFixed(3)}`);
        if (r.ok) found = (await r.json()).results || [];
      } catch (err) { /* hors ligne : placement en touchant la carte seulement */ }
      if (seq !== fSeq) return;
      fList.innerHTML = found.slice(0, 6).map(([name, la, ln, , km]) =>
        `<li><button type="button" data-lat="${la}" data-lng="${ln}" data-name="${esc(name)}">${esc(name)}<span class="muted">${km ?? ""} km</span></button></li>`).join("");
    }, 200);
  });
  fInput.addEventListener("keydown", e => {
    if (e.key === "Escape") { fInput.value = ""; fList.innerHTML = ""; stopPlacing(); }
    if (e.key === "Enter") { e.preventDefault(); fList.querySelector("button")?.click(); }
  });
  fList.addEventListener("click", e => {
    const b = e.target.closest("button");
    if (b) addPlace(+b.dataset.lat, +b.dataset.lng, b.dataset.name);
  });

  let startStep = null;
  try { startStep = sessionStorage.getItem(STEP_KEY); } catch (e) {}
  goStep(startStep || STEPS[0], false);

  document.addEventListener("click", e => {
    const el = e.target.closest(".stop-name");
    if (!el || el.isContentEditable) return;
    e.preventDefault();
    e.stopPropagation();
    const name = el.dataset.name, extra = el.hasAttribute("data-extra");
    const hidden = new Set(BOOK.options.cityHidden || []);
    if (hidden.has(name)) { hidden.delete(name); saveNames(hidden, BOOK.options.cityNames || {}); return; }
    const before = el.textContent;
    try { el.contentEditable = "plaintext-only"; } catch (err) { el.contentEditable = "true"; }
    if (el.contentEditable !== "plaintext-only") el.contentEditable = "true";
    el.focus();
    getSelection().selectAllChildren(el);
    const done = cancel => {
      el.removeEventListener("keydown", keys);
      el.contentEditable = "false";
      const value = el.textContent.trim();
      if (cancel || value === before) { el.textContent = before; return; }
      if (extra) {  // lieu ajouté : on change son nom, ou on le retire
        const rows = (BOOK.options.cityExtra || []).filter(r => r[2] !== value || r[2] === name);
        saveOpts({ cityExtra: value ? rows.map(r => (r[2] === name ? [r[0], r[1], value] : r)) : rows.filter(r => r[2] !== name) });
        return;
      }
      const names = { ...(BOOK.options.cityNames || {}) };
      if (!value) { hidden.add(name); delete names[name]; }
      else if (value === name) delete names[name];
      else names[name] = value;
      saveNames(hidden, names);
    };
    const keys = ev => {
      if (ev.key === "Enter") { ev.preventDefault(); el.blur(); }
      if (ev.key === "Escape") { ev.preventDefault(); el.removeEventListener("blur", blur); done(true); }
    };
    const blur = () => done(false);
    el.addEventListener("keydown", keys);
    el.addEventListener("blur", blur, { once: true });
  }, true);

  // ---------- Photos : manipulation directe sur les pages ----------
  const MIME = "application/x-otl-photo";
  let pool = {}, stages = {};
  function loadData(root) {
    pool = Object.fromEntries(JSON.parse(root.getElementById("pool-data").textContent).map(p => [p.key, p]));
    stages = Object.fromEntries(JSON.parse(root.getElementById("stages-data").textContent).map(s => [String(s.id), s]));
  }
  loadData(document);

  const toast = document.getElementById("toast");
  let toastTimer;
  function say(text) {
    toast.textContent = text; toast.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { toast.hidden = true; }, 2600);
  }

  // Étape active : celle sur laquelle on a cliqué (cible du « + », de l'import et du collage)
  let activeStage = null;
  function setActive(id) {
    activeStage = id;
    document.querySelectorAll(".page.is-target").forEach(p => p.classList.remove("is-target"));
    if (id) document.querySelectorAll(`.page[data-stage="${id}"]`).forEach(p => p.classList.add("is-target"));
    const st = stages[id];
    document.getElementById("tray-target").textContent = st
      ? `Étape ${st.n} : ${st.label}` : "Clique sur une page d'étape pour la choisir.";
    renderTray();
  }
  document.addEventListener("click", e => {
    const page = e.target.closest(".page[data-stage]");
    if (page) setActive(page.dataset.stage);
  });

  // Bac : toutes les photos du livre ; « À placer » = utilisées nulle part
  let filter = "free";
  function usage() {
    const used = {};
    for (const st of Object.values(stages)) for (const p of st.photos) (used[p.key] = used[p.key] || []).push(st.n);
    return used;
  }
  function renderTray() {
    const used = usage();
    const items = Object.values(pool).filter(p => filter === "all" || !used[p.key]);
    const grid = document.getElementById("tray-grid");
    if (!items.length) {
      grid.innerHTML = `<li class="tray-empty">${filter === "free" ? "Toutes les photos sont placées. Retire une photo d'une page pour la retrouver ici." : "Aucune photo pour l'instant."}</li>`;
      return;
    }
    grid.innerHTML = items.map(p => `
      <li class="tray-item" draggable="true" data-key="${p.key}">
        <img src="${p.url}" alt="" loading="lazy" draggable="false">
        <span class="tray-badge">${used[p.key] ? "Étape " + [...new Set(used[p.key])].join(", ") : "Non placée"}</span>
        <span class="tray-actions">
          ${activeStage ? `<button type="button" data-act="add" title="Ajouter à l'étape ${stages[activeStage].n}">+ Étape ${stages[activeStage].n}</button>` : ""}
          ${p.source === "upload" ? `<button type="button" data-act="delete" title="Supprimer définitivement">Supprimer</button>` : ""}
        </span>
      </li>`).join("");
  }
  document.querySelectorAll("[data-filter]").forEach(b => b.addEventListener("click", () => {
    filter = b.dataset.filter;
    document.querySelectorAll("[data-filter]").forEach(x => x.setAttribute("aria-pressed", x === b));
    renderTray();
  }));
  document.getElementById("tray-grid").addEventListener("click", async e => {
    const btn = e.target.closest("button[data-act]");
    if (!btn) return;
    const key = btn.closest("[data-key]").dataset.key;
    if (btn.dataset.act === "add") {
      await setPhotos(activeStage, [...stages[activeStage].photos, { key, caption: pool[key].caption || "" }]);
      say(`Photo ajoutée à l'étape ${stages[activeStage].n}.`);
    } else if (btn.dataset.act === "delete") {
      if (!confirm("Supprimer définitivement cette photo (de toutes les étapes) ?")) return;
      const touched = Object.values(stages).filter(st => st.photos.some(p => p.key === key)).map(st => String(st.id));
      if (await saveBook({ field: "upload.delete", upload: key.slice(2) })) { await refresh(touched); say("Photo supprimée."); }
    }
  });

  // Modifier la sélection d'une étape, puis ne rafraîchir que ses pages
  async function setPhotos(stageId, list, alsoRefresh = []) {
    stages[stageId].photos = list;
    if (await saveBook({ field: "stage.photos", activity: stageId, value: list })) {
      await refresh([String(stageId), ...alsoRefresh.map(String)]);
    }
  }

  // Outils sur chaque photo placée
  function bindPhotos(root) {
    root.querySelectorAll(".page[data-stage] figure[data-key]").forEach(fig => {
      if (fig.querySelector(".ph-tools")) return;
      const stageId = fig.closest(".page").dataset.stage;
      const index = Number(fig.dataset.index);
      const count = stages[stageId].photos.length;
      const tools = document.createElement("div");
      tools.className = "ph-tools";
      tools.innerHTML = `
        <button type="button" data-act="left" title="Avant" ${index === 0 ? "disabled" : ""}>‹</button>
        <button type="button" data-act="right" title="Après" ${index === count - 1 ? "disabled" : ""}>›</button>
        <button type="button" data-act="caption" title="Légende">Légende</button>
        <button type="button" data-act="cover" title="Mettre en couverture">Couverture</button>
        <button type="button" data-act="remove" class="ph-remove" title="Retirer de la page" aria-label="Retirer">✕</button>`;
      fig.appendChild(tools);
      fig.draggable = true;
      fig.querySelector("img").draggable = false;
      fig.addEventListener("dragstart", e => {
        e.dataTransfer.setData(MIME, JSON.stringify({ key: fig.dataset.key, from: stageId, index }));
        e.dataTransfer.effectAllowed = "copyMove";
        document.body.classList.add("dragging-photo");
      });
      fig.addEventListener("dragend", () => document.body.classList.remove("dragging-photo"));
      tools.addEventListener("click", async e => {
        const act = e.target.closest("button")?.dataset.act;
        if (!act) return;
        e.stopPropagation();
        const list = [...stages[stageId].photos];
        if (act === "remove") {
          list.splice(index, 1);
          await setPhotos(stageId, list);
          say("Photo retirée : elle reste disponible dans le bac.");
        } else if (act === "left" || act === "right") {
          const j = act === "left" ? index - 1 : index + 1;
          [list[index], list[j]] = [list[j], list[index]];
          await setPhotos(stageId, list);
        } else if (act === "cover") {
          if (await saveBook({ field: "cover", value: "photo", photo: fig.dataset.key })) say("Photo choisie pour la couverture.");
        } else if (act === "caption") {
          editCaption(fig, stageId, index);
        }
      });
      const cap = fig.querySelector("figcaption");
      if (cap) cap.addEventListener("click", e => { e.stopPropagation(); editCaption(fig, stageId, index); });
    });
  }

  function editCaption(fig, stageId, index) {
    let cap = fig.querySelector("figcaption");
    if (!cap) { cap = document.createElement("figcaption"); cap.className = "ph-new-caption"; fig.appendChild(cap); }
    cap.contentEditable = "true";
    cap.dataset.placeholder = "Légende";
    cap.focus();
    document.getSelection().selectAllChildren(cap);
    const before = cap.innerText.trim();
    const finish = async (save) => {
      cap.contentEditable = "false";
      const value = cap.innerText.trim();
      if (!save || value === before) { if (!before) cap.remove(); else cap.innerText = before; return; }
      const list = [...stages[stageId].photos];
      list[index] = { ...list[index], caption: value };
      await setPhotos(stageId, list);
    };
    cap.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); cap.blur(); }
      if (e.key === "Escape") { e.preventDefault(); cap.dataset.cancel = "1"; cap.blur(); }
    });
    cap.addEventListener("blur", () => finish(!cap.dataset.cancel), { once: true });
  }

  // Glisser-déposer : du bac vers une page, d'une page à l'autre, de l'ordinateur, ou vers le bac
  document.getElementById("tray-grid").addEventListener("dragstart", e => {
    const item = e.target.closest("[data-key]");
    if (!item) return;
    e.dataTransfer.setData(MIME, JSON.stringify({ key: item.dataset.key, from: null }));
    e.dataTransfer.effectAllowed = "copy";
  });
  const accepts = e => [...e.dataTransfer.types].some(t => t === MIME || t === "Files");
  document.addEventListener("dragover", e => {
    const page = e.target.closest(".page[data-stage]");
    const tray = e.target.closest("#tray");
    if (!(page || tray) || !accepts(e)) return;
    e.preventDefault();
    // L'effet doit être permis par la source : le bac et les fichiers ne proposent que la copie
    const allowed = e.dataTransfer.effectAllowed;
    e.dataTransfer.dropEffect = allowed === "copy" || allowed === "none" || e.altKey || !page ? "copy" : "move";
    document.querySelectorAll(".drop-target").forEach(x => x !== (page || tray) && x.classList.remove("drop-target"));
    (page || tray).classList.add("drop-target");
  });
  document.addEventListener("dragleave", e => {
    if (!e.relatedTarget || !e.relatedTarget.closest?.(".drop-target")) {
      document.querySelectorAll(".drop-target").forEach(x => x.classList.remove("drop-target"));
    }
  });
  document.addEventListener("drop", async e => {
    const page = e.target.closest(".page[data-stage]");
    const tray = e.target.closest("#tray");
    document.querySelectorAll(".drop-target").forEach(x => x.classList.remove("drop-target"));
    if (!(page || tray) || !accepts(e)) return;
    e.preventDefault();
    if (e.dataTransfer.files.length) {
      await upload([...e.dataTransfer.files], page ? page.dataset.stage : null);
      return;
    }
    const data = JSON.parse(e.dataTransfer.getData(MIME) || "null");
    if (!data) return;
    if (tray) {  // retour au bac : on retire la photo de son étape
      if (data.from) {
        const list = [...stages[data.from].photos];
        list.splice(data.index, 1);
        await setPhotos(data.from, list);
        say("Photo retirée : elle est de retour dans le bac.");
      }
      return;
    }
    const to = page.dataset.stage;
    const target = list => {  // déposée sur une photo : elle prend sa place, sinon à la fin
      const fig = e.target.closest("figure[data-key]");
      return fig && fig.closest(".page").dataset.stage === to ? Number(fig.dataset.index) : list.length;
    };
    const entry = { key: data.key, caption: pool[data.key]?.caption || "" };
    if (data.from === to) {
      const list = [...stages[to].photos];
      const [moved] = list.splice(data.index, 1);
      list.splice(Math.min(target(list), list.length), 0, moved);
      await setPhotos(to, list);
    } else if (data.from && !e.altKey) {
      const source = [...stages[data.from].photos];
      const [moved] = source.splice(data.index, 1);
      stages[data.from].photos = source;
      if (!(await saveBook({ field: "stage.photos", activity: data.from, value: source }))) return;
      const list = [...stages[to].photos];
      list.splice(target(list), 0, moved);
      await setPhotos(to, list, [data.from]);
      say(`Photo déplacée vers l'étape ${stages[to].n}.`);
    } else {
      const list = [...stages[to].photos];
      list.splice(target(list), 0, entry);
      await setPhotos(to, list);
      say(`Photo ${data.from ? "copiée" : "ajoutée"} sur l'étape ${stages[to].n}.`);
    }
  });

  // Import : bouton/zone du bac, dépôt de fichiers, collage
  document.getElementById("tray-files").addEventListener("change", e => {
    upload([...e.target.files], activeStage);
    e.target.value = "";
  });
  document.addEventListener("paste", e => {
    if (e.target.closest?.("[contenteditable=true], [contenteditable=plaintext-only], input, textarea")) return;
    const files = [...(e.clipboardData?.files || [])].filter(f => f.type.startsWith("image/"));
    if (!files.length) return;
    e.preventDefault();
    upload(files, activeStage);
  });
  async function upload(files, stageId) {
    files = files.filter(f => f.type.startsWith("image/") || /\.(jpe?g|png|webp|heic|gif|tiff?)$/i.test(f.name));
    if (!files.length) { say("Ce ne sont pas des images."); return; }
    say(`Envoi de ${files.length} photo${files.length > 1 ? "s" : ""}…`);
    const form = new FormData();
    if (stageId) form.append("activity", stageId);
    files.forEach((f, i) => form.append("photos", f, f.name || `photo-${i + 1}.png`));
    try {
      const r = await fetch(BOOK.upload, { method: "POST", headers: { "X-CSRFToken": BOOK.csrf }, body: form });
      const data = await r.json();
      if (!data.ok) throw new Error(data.error);
      if (stageId) {
        // Sélection personnalisée ou non : on s'assure que les nouvelles photos sont sur l'étape
        const list = [...stages[stageId].photos, ...data.keys.filter(k => !stages[stageId].photos.some(p => p.key === k)).map(k => ({ key: k, caption: "" }))];
        await saveBook({ field: "stage.photos", activity: stageId, value: list });
        await refresh([stageId]);
      } else {
        await refresh([]);
      }
      say(`${data.added} photo${data.added > 1 ? "s ajoutées" : " ajoutée"}${stageId ? ` sur l'étape ${stages[stageId].n}` : " au bac"}.`
          + (data.rejected.length ? ` Ignoré : ${data.rejected.join(", ")}.` : ""));
    } catch (err) {
      say(`Échec de l'envoi (${err.message}).`);
    }
  }

  // Rafraîchissement partiel : on recharge le HTML et on remplace seulement les
  // pages des étapes concernées, en gardant leur carte (déjà chargée) intacte.
  async function refresh(ids) {
    const html = await (await fetch(location.href, { headers: { "X-Requested-With": "fetch" } })).text();
    const doc = new DOMParser().parseFromString(html, "text/html");
    loadData(doc);
    for (const id of new Set(ids)) {
      const oldPage = document.querySelector(`.stage-page[data-stage="${id}"]`);
      const fresh = doc.querySelector(`.stage-page[data-stage="${id}"]`);
      if (!oldPage || !fresh) continue;
      const newPage = document.importNode(fresh, true);
      const mapEl = oldPage.querySelector(".book-map");
      newPage.querySelector(".book-map").replaceWith(mapEl);
      oldPage.replaceWith(newPage);
      document.querySelectorAll(`.photo-page[data-photos-of="${id}"]`).forEach(p => p.remove());
      let after = newPage;
      for (const pp of doc.querySelectorAll(`.photo-page[data-photos-of="${id}"]`)) {
        const node = document.importNode(pp, true);
        after.after(node);
        after = node;
      }
      const m = window.bookMaps["s" + id];
      if (m) { m.map.invalidateSize(); m.map.fitBounds(m.bounds, { padding: [28, 28] }); }
      [newPage, ...document.querySelectorAll(`.photo-page[data-photos-of="${id}"]`)].forEach(node => {
        window.layoutPhotos(node);
        bindPhotos(node);
        window.bindTexts(node);
      });
    }
    if (activeStage) setActive(activeStage); else renderTray();
  }

  bindPhotos(document);
  renderTray();
  let reopen = "";
  try { reopen = sessionStorage.getItem("otl-reopen") || ""; sessionStorage.removeItem("otl-reopen"); } catch (e) {}
  if (reopen && stages[reopen]) setActive(reopen);
})();
