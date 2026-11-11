"""Le livre : pages, personnalisation, photos."""

import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import formats, timezone
from django.views.decorators.http import require_POST

from .. import book, journeys, places
from ..models import Book, BookPhoto, BookStage, JourneyNote
from .common import _account_or_none, _signed, _json_body, _date_range, _stage_colors, _stops, _complete


def _clean_extra_places(rows):
    """Lieux ajoutés au livre : [[lat, lng, nom]] valides, sans doublon de nom, 100 au plus."""
    out, seen = [], set()
    for row in rows:
        try:
            lat, lng, name = float(row[0]), float(row[1]), str(row[2]).strip()[:80]
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if name and -85 <= lat <= 85 and -180 <= lng <= 180 and name not in seen:
            seen.add(name)
            out.append([round(lat, 4), round(lng, 4), name])
    return out[:100]


def _leg_places(leg):
    """Départ et arrivée nommés d'une étape : [lat, lng, nom] (un seul point pour une boucle)."""
    places = []
    for (lat, lng), name in ((leg.start, leg.start_name), (leg.end, leg.end_name)):
        if name and all(p[2] != name for p in places):
            places.append([round(lat, 4), round(lng, 4), name])
    return places


def _book_or_404(request, book_id):
    account = _account_or_none(request)
    if account is None:
        raise Http404
    return get_object_or_404(Book, pk=book_id, account=account), account


def _book_journeys(account, book_obj):
    if book_obj.activity_ids:
        j = journeys.from_activities(account, book_obj.activity_ids,
                                     book_obj.selection_title, book_obj.selection_story)
        return [j] if j else []
    found = {j.id: j for j in journeys.for_account(account)}
    return [found[i] for i in book_obj.journey_ids if i in found]


@login_required
def book_detail(request, book_id):
    """Le livre : pages A4 à l'écran comme à l'impression, avec un mode édition."""
    book_obj, account = _book_or_404(request, book_id)
    chosen = _book_journeys(account, book_obj)
    if not chosen:
        messages.error(request, "Les voyages de ce livre n'existent plus (activités supprimées ?).")
        return redirect("activities:creations")
    # Photos Strava et noms de lieux manquants : récupérés ensuite par la page (book_fetch_photos)
    photos_pending = (sum(1 for j in chosen for a in j.activities if not a.photos_synced_at)
                      + len(places.missing_points(chosen)))
    stage_notes = {s.activity_id: s for s in book_obj.stages.all()}
    pool = book.photo_pool(chosen, book_obj.uploads.all())
    chapters = [book.build(j, _stage_colors(len(j.legs)), _date_range(j), book_obj, stage_notes, pool)
                for j in chosen]

    first, last = chosen[0].first_day, chosen[-1].last_day
    period = (formats.date_format(first, "F Y") if (first.year, first.month) == (last.year, last.month)
              else f"{formats.date_format(first, 'F Y')} – {formats.date_format(last, 'F Y')}")
    totals = {
        "km": round(sum(j.distance_km for j in chosen)), "elevation": sum(j.elevation for j in chosen),
        "days": sum(j.days for j in chosen), "stages": sum(len(j.legs) for j in chosen),
    }
    cover_photo = pool.get(book_obj.cover_photo)
    if book_obj.cover == Book.COVER_PHOTO and cover_photo is None and pool:
        cover_photo = next(iter(pool.values()))

    maps = [{"id": f"j{c['journey'].id}", "stages": [{"coords": s["coords"], "color": s["color"]} for s in c["stages"]],
             "transfers": [[t["from"], t["to"]] for t in c["journey"].transfer_list],
             "places": _stops(c["journey"])}
            for c in chapters]
    maps += [{"id": f"s{s['id']}", "stages": [{"coords": s["coords"], "color": s["color"]}], "places": _leg_places(leg)}
             for c in chapters for s, leg in zip(c["stages"], c["journey"].legs)]
    # Couverture : les villes-étapes d'un seul périple, sinon le départ et l'arrivée de chacun
    cover_places = (maps[0]["places"] if len(chapters) == 1
                    else [p for m in maps[:len(chapters)] for p in (m["places"][:1] + m["places"][-1:])])
    maps.append({"id": "cover", "stages": [st for m in maps[:len(chapters)] for st in m["stages"]],
                 "places": cover_places})
    # Pour l'édition : toutes les photos du livre, et la sélection de chaque étape
    stages_info = [{"id": s["id"], "n": s["n"], "label": s["label"], "chapter": ci, "photos": s["photo_keys"]}
                   for ci, c in enumerate(chapters) for s in c["stages"]]
    return render(request, "activities/book.html", {
        "photos_pending": photos_pending,
        "book": book_obj, "title": book_obj.title or "Mes périples", "chapters": chapters, "totals": totals,
        "maps": maps, "period": period, "cover_photo": cover_photo,
        "subtitle": book_obj.subtitle or f"{period[:1].upper()}{period[1:]}"
                    + (f", {account.firstname} {account.lastname}".rstrip() if account.firstname else ""),
        "opt": {k: book_obj.option(k) for k in Book.DEFAULT_OPTIONS},
        "pool": list(pool.values()), "stages_info": stages_info, "has_photos": bool(pool),
        "edit": request.GET.get("edition") == "1",
    })


def _book_photo_keys(account, book_obj):
    return set(book.photo_pool(_book_journeys(account, book_obj), book_obj.uploads.all()))


def _book_activity_ids(account, book_obj):
    return {a.strava_id for j in _book_journeys(account, book_obj) for a in j.activities}


@require_POST
@login_required
def book_update(request, book_id):
    """Enregistre une modification faite dans le mode édition du livre (JSON)."""
    book_obj, account = _book_or_404(request, book_id)
    data = _json_body(request)
    field, value = data.get("field"), data.get("value")
    text = (value or "").strip() if isinstance(value, str) else ""

    if field in ("book.title", "book.subtitle"):
        setattr(book_obj, field.split(".")[1], text[:300])
        book_obj.save()
    elif field in ("journey.title", "journey.story"):
        journey_id = int(data.get("journey", -1))
        if book_obj.activity_ids and journey_id == journeys.SELECTION_KEY:
            if field == "journey.title":
                book_obj.selection_title = text[:200]
            else:
                book_obj.selection_story = text
            book_obj.save()
        elif journey_id not in book_obj.journey_ids:
            return JsonResponse({"ok": False, "error": "Périple inconnu"}, status=400)
        else:
            note, _ = JourneyNote.objects.get_or_create(account=account, journey_id=journey_id)
            setattr(note, field.split(".")[1], text[:200] if field == "journey.title" else text)
            note.save()
    elif field in ("stage.title", "stage.text", "stage.reset_text", "stage.photos"):
        activity_id = int(data.get("activity", 0))
        if activity_id not in _book_activity_ids(account, book_obj):
            return JsonResponse({"ok": False, "error": "Étape inconnue"}, status=400)
        stage, _ = BookStage.objects.get_or_create(book=book_obj, activity_id=activity_id)
        if field == "stage.title":
            stage.title = text[:200]
        elif field == "stage.text":
            stage.text, stage.text_edited = text, True
        elif field == "stage.reset_text":
            stage.text, stage.text_edited = "", False
        else:
            if not isinstance(value, list):
                return JsonResponse({"ok": False, "error": "Liste de photos attendue"}, status=400)
            allowed = _book_photo_keys(account, book_obj)
            stage.photos = [{"key": str(p.get("key")), "caption": str(p.get("caption", ""))[:300]}
                            for p in value if isinstance(p, dict) and p.get("key") in allowed]
        stage.save()
    elif field == "cover":
        if value in (Book.COVER_MAP, Book.COVER_PHOTO):
            book_obj.cover = value
        book_obj.cover_photo = str(data.get("photo", book_obj.cover_photo))[:30]
        book_obj.save()
    elif field == "options":
        if not isinstance(value, dict):
            return JsonResponse({"ok": False, "error": "Options attendues"}, status=400)
        options = dict(book_obj.options)
        for key, default in Book.DEFAULT_OPTIONS.items():
            if key in value and isinstance(value[key], type(default)):
                if key == "imagery" and value[key] not in Book.IMAGERY:
                    continue
                if key == "cityHidden":
                    value[key] = [str(n)[:80] for n in value[key] if isinstance(n, str) and n][:200]
                if key == "cityExtra":
                    value[key] = _clean_extra_places(value[key])
                if key == "cityNames":
                    value[key] = {str(k)[:80]: v.strip()[:80] for k, v in list(value[key].items())[:200]
                                  if isinstance(v, str) and v.strip() and v.strip() != k}
                options[key] = value[key]
        book_obj.options = options
        book_obj.save()
    elif field == "upload.delete":
        upload = get_object_or_404(BookPhoto, pk=int(data.get("upload", 0)), book=book_obj)
        for stage in book_obj.stages.exclude(photos=None):
            kept = [p for p in stage.photos if p.get("key") != upload.key]
            if len(kept) != len(stage.photos):
                stage.photos = kept
                stage.save(update_fields=["photos"])
        upload.image.delete(save=False)
        upload.delete()
    else:
        return JsonResponse({"ok": False, "error": "Champ inconnu"}, status=400)
    Book.objects.filter(pk=book_obj.pk).update(updated_at=timezone.now())
    return JsonResponse({"ok": True})


MAX_UPLOAD_SIDE = 2400


BOOK_PHOTO_BATCH = 8  # étapes vérifiées par requête du navigateur : la page reste réactive


@require_POST
@login_required
def book_fetch_photos(request, book_id):
    """Récupère sur Strava les photos manquantes des étapes du livre, par petits lots.

    Appelée en boucle par la page du livre tant qu'il reste des étapes à vérifier.
    """
    book_obj, account = _book_or_404(request, book_id)
    chosen = _book_journeys(account, book_obj)
    result = _complete(account, [a for j in chosen for a in j.activities], chosen, photo_batch=BOOK_PHOTO_BATCH)
    return JsonResponse({"imported": result["found"], "remaining": result["remaining"], "error": result["error"],
                         "rate_limited": result["retry"]})


@require_POST
@login_required
def book_upload(request, book_id):
    """Ajoute des photos personnelles à une étape du livre (redimensionnées, orientation corrigée)."""
    from io import BytesIO

    from django.core.files.base import ContentFile
    from PIL import Image, ImageOps, UnidentifiedImageError

    book_obj, account = _book_or_404(request, book_id)
    raw = request.POST.get("activity", "")
    activity_id = _signed(raw)  # sans étape : la photo va dans le bac
    if activity_id is not None and activity_id not in _book_activity_ids(account, book_obj):
        return JsonResponse({"ok": False, "error": "Étape inconnue"}, status=400)
    added, rejected = [], []
    for f in request.FILES.getlist("photos")[:30]:
        try:
            img = ImageOps.exif_transpose(Image.open(f))
            img = img.convert("RGB")
            img.thumbnail((MAX_UPLOAD_SIDE, MAX_UPLOAD_SIDE))
            out = BytesIO()
            img.save(out, "JPEG", quality=85, optimize=True)
        except (UnidentifiedImageError, OSError):
            rejected.append(f.name)
            continue
        upload = BookPhoto(book=book_obj, activity_id=activity_id)
        upload.image.save(f"{secrets.token_hex(8)}.jpg", ContentFile(out.getvalue()), save=True)
        added.append(upload.key)
    stage = BookStage.objects.filter(book=book_obj, activity_id=activity_id).first() if activity_id else None
    if stage and stage.photos is not None:  # sélection personnalisée : on y ajoute les nouvelles photos
        stage.photos += [{"key": k, "caption": ""} for k in added]
        stage.save()
    Book.objects.filter(pk=book_obj.pk).update(updated_at=timezone.now())
    return JsonResponse({"ok": True, "added": len(added), "keys": added, "rejected": rejected})


@require_POST
@login_required
def book_delete(request, book_id):
    book_obj, _ = _book_or_404(request, book_id)
    for upload in book_obj.uploads.all():
        upload.image.delete(save=False)
    book_obj.delete()
    messages.info(request, "Livre supprimé.")
    return redirect("activities:creations")
