/* Chargement en arrière-plan : photos Strava et noms de lieux.

   La page s'affiche sans attendre ; ce script appelle ensuite l'adresse donnée par
   #bg-task (data-url) en boucle, un petit lot à chaque fois. La réponse est
   {found, remaining, error, retry}. Un indicateur discret montre l'avancement.
   À la fin, si quelque chose de nouveau a été trouvé, la page se recharge à la même
   position ; si l'utilisateur est en train de saisir, un bouton le propose à la place. */
(function () {
  const task = document.getElementById("bg-task");
  if (!task) return;
  const url = task.dataset.url;
  const what = task.dataset.what || "données";
  const csrf = task.querySelector("[name=csrfmiddlewaretoken]").value;
  const RETRY_MS = 60000;

  const pill = document.createElement("div");
  pill.className = "bg-status";
  pill.setAttribute("role", "status");
  pill.setAttribute("aria-live", "polite");
  document.body.appendChild(pill);
  let found = 0;

  const plural = n => (n > 1 ? "s" : "");
  function show(text) {
    pill.innerHTML = `<span class="bg-spinner" aria-hidden="true"></span>${text}`;
    pill.hidden = false;
  }
  function busy() {
    // Saisie en cours ou formulaire de correction ouvert : ne pas recharger sous les doigts
    const el = document.activeElement;
    return (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) || document.querySelector("details[open] form");
  }
  function reload() {
    try { sessionStorage.setItem("otl-scroll", `${location.pathname}|${window.scrollY}`); } catch (e) {}
    location.reload();
  }
  function finish() {
    if (!found) { pill.hidden = true; return; }
    if (!busy()) return reload();
    pill.innerHTML = "";
    const b = document.createElement("button");
    b.type = "button";
    b.className = "link-button";
    b.textContent = `Nouvelles ${what} disponibles : afficher`;
    b.addEventListener("click", reload);
    pill.appendChild(b);
  }

  async function step() {
    let data;
    try {
      const r = await fetch(url, { method: "POST", headers: { "X-CSRFToken": csrf } });
      if (!r.ok) throw new Error(r.status);
      data = await r.json();
    } catch (e) {
      show(`Connexion perdue, nouvel essai dans 1 min…`);
      return setTimeout(step, RETRY_MS);
    }
    found += data.found;
    if (data.error && data.retry) {
      show(`Strava limite les requêtes : nouvel essai dans 1 min (${data.remaining} restant${plural(data.remaining)})`);
      pill.title = data.error;
      return setTimeout(step, RETRY_MS);
    }
    if (data.remaining && !data.error) {
      show(`Récupération des ${what} : ${data.remaining} restant${plural(data.remaining)}…`);
      return step();
    }
    finish();
  }

  show(`Récupération des ${what}…`);
  step();
})();

// Après un rechargement dû au chargement en arrière-plan, on revient où on en était
(function () {
  let saved = null;
  try { saved = sessionStorage.getItem("otl-scroll"); sessionStorage.removeItem("otl-scroll"); } catch (e) {}
  if (!saved) return;
  const [path, y] = saved.split("|");
  if (path === location.pathname) window.addEventListener("load", () => window.scrollTo(0, Number(y)));
})();
