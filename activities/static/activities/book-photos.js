/* Livre : photos Strava manquantes, récupérées par lots en arrière-plan (BOOK.photos). */
// Photos Strava manquantes : récupérées par lots en arrière-plan, puis la page se recharge
// (en mode édition, un bouton le propose pour ne pas interrompre la saisie).
(() => {
  const el = document.getElementById("strava-photos");
  const url = BOOK.photos;
  let found = 0;
  window.stravaPhotosFetching = true;
  const plural = n => n > 1 ? "s" : "";
  function done() {
    window.stravaPhotosFetching = false;
    if (!found) { el.hidden = true; return; }
    if (!BOOK.edit) {
      try { sessionStorage.setItem("otl-scroll", String(window.scrollY)); } catch (e) {}
      location.reload();
      return;
    }
    el.innerHTML = "";
    const b = document.createElement("button");
    b.type = "button"; b.className = "btn ghost";
    b.textContent = "Afficher les nouvelles photos et lieux";
    b.addEventListener("click", () => location.reload());
    el.appendChild(b);
  }
  async function step() {
    let data;
    try {
      const r = await fetch(url, { method: "POST", headers: { "X-CSRFToken": BOOK.csrf } });
      data = await r.json();
    } catch (e) {
      el.textContent = "Photos Strava : connexion perdue, nouvel essai dans 1 min…";
      return setTimeout(step, 60000);
    }
    found += data.imported;
    const left = `${data.remaining} élément${plural(data.remaining)} à récupérer`;
    if (data.error && !data.rate_limited) return done();  // noms de lieux indisponibles : on garde ce qu'on a
    if (data.error) {
      const why = data.rate_limited ? "quota Strava atteint" : "Strava ne répond pas";
      el.textContent = `Photos : ${why}, nouvel essai dans 1 min (${left})`;
      el.title = data.error;
      return setTimeout(step, 60000);
    }
    if (data.remaining) {
      el.textContent = `Photos et lieux : ${left}…`;
      return step();
    }
    done();
  }
  step();
})();
