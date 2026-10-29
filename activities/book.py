"""Livre de voyage : préparation des données et mise en forme des périples.

Le livre réutilise les périples détectés (journeys.py) et les complète, pour
les seules étapes concernées, avec ce que la liste d'activités Strava ne donne
pas : la description saisie sur Strava et le profil d'altitude. Ces données
sont mises en cache sur l'activité : un livre régénéré ne coûte plus d'appel.
"""

from django.utils import formats, timezone

from . import polyline, strava

PROFILE_POINTS = 160

# Mise en page des photos, en mm (zone utile d'une page A4 : 178 mm de large)
CONTENT_W = 178.0
GAP = 3.0
CAPTION_H = 5.0
STAGE_BOX_MAX_H = 105.0     # hauteur maximale réservée aux photos sur une page d'étape
STAGE_BOX_MIN_H = 45.0
STAGE_MAX_PHOTOS = 3
STAGE_MIN_SHARE = 0.20      # chaque photo garde au moins 20 % de la zone
PAGE_BOX_H = 240.0          # zone photos d'une page de photos
PAGE_MAX_PHOTOS = 6
PAGE_MIN_SHARE = 0.08
PAGE_COST = 0.1             # à surface vide égale, on préfère moins de pages
DEFAULT_RATIO = 1.5         # photo dont on ignore les dimensions (corrigé à l'affichage)


def _downsample(distance, altitude):
    pairs = [(d, a) for d, a in zip(distance, altitude) if d is not None and a is not None]
    if len(pairs) < 2:
        return None
    step = max(1, len(pairs) // PROFILE_POINTS)
    sample = pairs[::step]
    if sample[-1] != pairs[-1]:
        sample.append(pairs[-1])
    return [[round(d / 1000, 3), round(a, 1)] for d, a in sample]


def prepare(account, journeys, max_calls=60):
    """Récupère descriptions et profils manquants. Renvoie (étapes complétées, erreur éventuelle).

    Deux appels par étape ; on s'arrête au plafond ou à la première erreur
    (limite de requêtes) : le livre se génère quand même avec ce qu'on a.
    """
    done, calls = 0, 0
    for j in journeys:
        for activity in j.activities:
            if activity.details_fetched_at:
                continue
            if calls + 2 > max_calls:
                return done, "Plafond d'appels atteint : certaines étapes n'ont pas encore leur profil. Régénère le livre dans quelques minutes."
            try:
                detail = strava.activity_detail(account, activity.strava_id)
                calls += 1
                streams = strava.activity_streams(account, activity.strava_id)
                calls += 1
            except strava.StravaError as exc:
                return done, f"Données Strava incomplètes ({exc}). Le livre est généré avec ce qui est disponible."
            activity.description = (detail.get("description") or "").strip()
            activity.profile = _downsample((streams.get("distance") or {}).get("data") or [],
                                           (streams.get("altitude") or {}).get("data") or [])
            activity.details_fetched_at = timezone.now()
            activity.save(update_fields=["description", "profile", "details_fetched_at"])
            done += 1
    return done, None


def profile_svg(points, width=1000, height=160, pad=4):
    """Profil d'altitude en SVG : chemin de la courbe et de l'aire, altitudes min/max."""
    if not points or len(points) < 2:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    max_x = xs[-1] or 1e-9
    lo, hi = min(ys), max(ys)
    span = max(hi - lo, 50)  # évite d'exagérer le relief d'une étape plate
    def xy(x, y):
        return f"{x / max_x * width:.1f},{height - pad - (y - lo) / span * (height - 2 * pad):.1f}"
    line = "M" + "L".join(xy(x, y) for x, y in zip(xs, ys))
    return {"line": line, "area": f"{line}L{width},{height}L0,{height}Z",
            "min": round(lo), "max": round(hi), "km": round(max_x, 1), "width": width, "height": height}


def best_layout(photos, width, height, gap=GAP, caption_h=CAPTION_H):
    """Meilleure disposition sans recadrage de photos dans une zone width×height.

    Les photos gardent leur ordre et leurs proportions ; on essaie toutes les
    façons de les répartir en rangées (au plus 2^(n-1), n ≤ 6). Dans une rangée,
    toutes les photos ont la même hauteur et la rangée occupe toute la largeur ;
    si l'ensemble est trop haut, tout est réduit d'un même facteur. On garde la
    disposition qui couvre la plus grande surface. Le même calcul est refait
    dans le navigateur avec les dimensions réelles (book.html).
    Renvoie {"rows", "scale", "natural_h", "min_share"} ou None.
    """
    n = len(photos)
    if not n:
        return None
    best = None
    for mask in range(1 << (n - 1)):
        rows, row = [], [photos[0]]
        for i in range(1, n):
            if mask >> (i - 1) & 1:
                rows.append(row)
                row = []
            row.append(photos[i])
        rows.append(row)
        heights = [(width - gap * (len(r) - 1)) / sum(p["ratio"] for p in r) for r in rows]
        fixed = gap * (len(rows) - 1) + sum(caption_h for r in rows if any(p.get("caption") for p in r))
        natural = sum(heights) + fixed
        scale = min(1.0, (height - fixed) / sum(heights)) if height > fixed else 0
        areas = [(h * scale) ** 2 * p["ratio"] for r, h in zip(rows, heights) for p in r]
        # Note : somme des racines des surfaces. Elle récompense la place occupée
        # mais aussi l'équilibre : une grande photo et trois vignettes minuscules
        # valent moins que quatre photos de taille comparable.
        candidate = {"rows": [len(r) for r in rows], "scale": scale, "natural_h": natural,
                     "area": sum(areas), "min_share": min(areas) / (width * height),
                     "score": sum(a ** 0.5 for a in areas)}
        if best is None or candidate["score"] > best["score"] + 1e-6:
            best = candidate
    return best


def paginate_photos(photos):
    """Répartit les photos : quelques-unes sur la page de l'étape, le reste sur des pages dédiées.

    Le nombre de photos par page dépend de leurs proportions : on ajoute des
    photos tant que chacune reste assez grande une fois la page composée.
    """
    lead = []
    for p in photos[:STAGE_MAX_PHOTOS]:
        layout = best_layout(lead + [p], CONTENT_W, STAGE_BOX_MAX_H)
        if lead and layout["min_share"] < STAGE_MIN_SHARE:
            break
        lead.append(p)
    # Une seule photo restante : plutôt que de lui consacrer une page presque vide,
    # on la garde sur la page de l'étape si elle y reste lisible.
    if len(photos) == len(lead) + 1 and len(lead) < STAGE_MAX_PHOTOS + 1:
        if best_layout(photos, CONTENT_W, STAGE_BOX_MAX_H)["min_share"] >= STAGE_MIN_SHARE * 0.6:
            lead = list(photos)
    pages = _photo_pages(photos[len(lead):])
    box_h = None
    if lead:
        natural = best_layout(lead, CONTENT_W, STAGE_BOX_MAX_H)["natural_h"]
        box_h = round(max(STAGE_BOX_MIN_H, min(STAGE_BOX_MAX_H, natural)), 1)
    return lead, pages, box_h


def _photo_pages(photos):
    """Découpe en pages qui maximise la surface couverte (programmation dynamique).

    On minimise la surface laissée vide (plus un petit coût par page) : deux
    paysages empilés remplissent une page, un portrait seul aussi, alors qu'un
    paysage isolé laisse la moitié de la page vide. Les photos trop petites
    sont interdites, ce qui empêche de tout entasser sur une seule page.
    """
    n = len(photos)
    if not n:
        return []
    box = CONTENT_W * PAGE_BOX_H
    best = [0.0] + [float("-inf")] * n
    cut = [0] * (n + 1)
    for i in range(1, n + 1):
        for j in range(max(0, i - PAGE_MAX_PHOTOS), i):
            layout = best_layout(photos[j:i], CONTENT_W, PAGE_BOX_H)
            if i - j > 1 and layout["min_share"] < PAGE_MIN_SHARE:
                continue
            score = best[j] - (1 - layout["area"] / box) - PAGE_COST
            if score > best[i]:
                best[i], cut[i] = score, j
    pages, i = [], n
    while i:
        pages.append(photos[cut[i]:i])
        i = cut[i]
    return pages[::-1]


def _ratio(width, height):
    return round(width / height, 4) if width and height else DEFAULT_RATIO


def stage_photos(activity, stage_note, uploads):
    """Photos d'une étape pour le livre, et la liste complète pour l'éditeur.

    Renvoie (sélection ordonnée, toutes les photos disponibles avec leur état).
    Sans personnalisation : toutes les photos Strava puis les photos ajoutées.
    """
    available = {}
    for p in activity.photos.all():
        available[f"s:{p.pk}"] = {"key": f"s:{p.pk}", "url": p.url, "caption": p.caption, "source": "strava",
                                  "ratio": _ratio(p.width, p.height)}
    for u in uploads:
        available[u.key] = {"key": u.key, "url": u.image.url, "caption": u.caption, "source": "upload",
                            "ratio": _ratio(u.width, u.height)}
    if stage_note is not None and stage_note.photos is not None:
        chosen = []
        for entry in stage_note.photos:
            photo = available.get(entry.get("key"))
            if photo:
                chosen.append({**photo, "caption": entry.get("caption", photo["caption"])})
    else:
        chosen = list(available.values())
    picked = {p["key"] for p in chosen}
    everything = chosen + [{**p, "excluded": True} for k, p in available.items() if k not in picked]
    return chosen, everything


def build(journey, colors, date_range, book_obj=None, stage_notes=None, uploads=None):
    """Contenu d'un périple pour le livre : synthèse, puis une page par étape.

    `stage_notes` ({activity_id: BookStage}) et `uploads` ({activity_id: [BookPhoto]})
    appliquent la personnalisation du livre.
    """
    stage_notes, uploads = stage_notes or {}, uploads or {}
    stages, combined, offset = [], [], 0.0
    for i, (leg, color) in enumerate(zip(journey.legs, colors)):
        a = leg.activity
        note = stage_notes.get(a.strava_id)
        photos, all_photos = stage_photos(a, note, uploads.get(a.strava_id, []))
        lead, photo_pages, photo_box_h = paginate_photos(photos)
        if a.profile:
            combined += [[offset + km, alt] for km, alt in a.profile]
            offset += a.profile[-1][0]
        else:
            offset += a.distance / 1000
        default_label = leg.label or a.name
        stages.append({
            "n": i + 1, "id": a.strava_id, "color": color, "activity_name": a.name,
            "label": (note.title if note and note.title else default_label), "default_label": default_label,
            "date": formats.date_format(a.start_date_local or a.start_date, "l j F Y"),
            "km": a.distance_km, "elevation": round(a.total_elevation_gain), "duration": a.duration_display,
            "speed": a.speed_display,
            "description": note.text if note and note.text_edited else a.description,
            "strava_description": a.description,
            "coords": polyline.decode(a.summary_polyline), "profile": profile_svg(a.profile),
            "lead_photos": lead, "photo_pages": photo_pages, "photo_box_h": photo_box_h,
            "all_photos": all_photos,
            "night_after": i in journey.nights,
        })
    return {
        "journey": journey, "dates": date_range, "stages": stages,
        "profile": profile_svg(combined), "nights": len(journey.nights),
        "photo_count": sum(len(s["lead_photos"]) + sum(len(p) for p in s["photo_pages"]) for s in stages),
        "highest": max((s["profile"]["max"] for s in stages if s["profile"]), default=None),
    }
