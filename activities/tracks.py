"""Lecture des fichiers d'activité déposés par l'utilisateur : GPX, FIT, TCX, et archives ZIP ou .gz.

Ces fichiers viennent de l'utilisateur lui-même (export Garmin Connect, Komoot, Coros, Suunto,
Wahoo, ou l'export de ses données Strava) : ce ne sont pas des données obtenues par l'API Strava.

`read_files` renvoie, pour chaque activité trouvée, un dictionnaire prêt à créer une `Activity` :
nom, sport (au format Strava), dates, distance, D+, temps, tracé encodé et profil d'altitude.
"""

import gzip
import io
import math
import re
import zipfile
from zoneinfo import ZoneInfo
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import PurePosixPath
from xml.etree import ElementTree

MAX_TRACK_POINTS = 600     # points gardés pour le tracé (comme un « summary polyline » Strava)
PROFILE_POINTS = 120       # points du profil d'altitude
ELEVATION_STEP = 3.0       # mètres : en dessous, une variation est du bruit de mesure
MOVING_SPEED = 0.5         # m/s : en dessous, on considère l'arrêt
MAX_ARCHIVE_FILES = 10000  # un export Garmin complet contient des milliers de fichiers FIT

# Sports des fichiers (GPX <type>, TCX Sport, FIT sport/sub_sport) → types Strava utilisés par le site
SPORTS = {
    "running": "Run", "run": "Run", "trail_running": "TrailRun", "trail": "TrailRun", "trailrun": "TrailRun",
    "cycling": "Ride", "biking": "Ride", "ride": "Ride", "road": "Ride", "bike": "Ride",
    "mountain": "MountainBikeRide", "mountain_biking": "MountainBikeRide", "mountainbikeride": "MountainBikeRide",
    "mtb": "MountainBikeRide", "gravel_cycling": "GravelRide", "gravel": "GravelRide", "gravelride": "GravelRide",
    "e_biking": "EBikeRide", "ebikeride": "EBikeRide",
    "walking": "Walk", "walk": "Walk", "hiking": "Hike", "hike": "Hike",
    "swimming": "Swim", "swim": "Swim", "open_water": "Swim",
    "cross_country_skiing": "NordicSki", "alpine_skiing": "AlpineSki", "backcountry": "BackcountrySki",
    "snowboarding": "Snowboard", "snowshoeing": "Snowshoe",
    "rowing": "Rowing", "kayaking": "Kayaking", "paddling": "StandUpPaddling", "stand_up_paddleboarding": "StandUpPaddling",
}
SPORT_NAMES = {"Run": "Course", "TrailRun": "Trail", "Ride": "Sortie vélo", "MountainBikeRide": "Sortie VTT",
               "GravelRide": "Sortie gravel", "EBikeRide": "Sortie vélo", "Walk": "Marche", "Hike": "Randonnée",
               "Swim": "Natation", "NordicSki": "Ski de fond", "AlpineSki": "Ski", "Kayaking": "Kayak"}


class TrackError(Exception):
    """Fichier illisible ou sans tracé GPS."""


# ---------- Lecture des formats ----------

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _children(el, name):
    return [c for c in el if _local(c.tag) == name]


def _first_text(el, *names):
    for c in el.iter():
        if _local(c.tag) in names and c.text and c.text.strip():
            return c.text.strip()
    return ""


def _parse_time(text):
    if not text:
        return None
    try:
        t = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt_timezone.utc)


def _float(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _sport(*candidates):
    for c in candidates:
        key = (c or "").strip().lower().replace(" ", "_").replace("-", "_")
        if key in SPORTS:
            return SPORTS[key]
        for word, sport in SPORTS.items():
            if word and len(word) > 3 and word in key:
                return sport
    return ""


def _gpx(data):
    root = ElementTree.fromstring(data)
    out = []
    for trk in _children(root, "trk"):
        points = []
        for seg in _children(trk, "trkseg"):
            for pt in _children(seg, "trkpt"):
                lat, lon = _float(pt.get("lat")), _float(pt.get("lon"))
                if lat is None or lon is None:
                    continue
                ele = time = None
                for c in pt:
                    if _local(c.tag) == "ele":
                        ele = _float(c.text)
                    elif _local(c.tag) == "time":
                        time = _parse_time(c.text)
                points.append((lat, lon, ele, time))
        name = next((c.text.strip() for c in trk if _local(c.tag) == "name" and c.text), "")
        kind = next((c.text.strip() for c in trk if _local(c.tag) == "type" and c.text), "")
        out.append({"name": name, "sport": _sport(kind), "points": points})
    if not out:  # itinéraire (rte) plutôt que trace
        for rte in _children(root, "rte"):
            points = [(_float(p.get("lat")), _float(p.get("lon")), _float(_first_text(p, "ele")) if _first_text(p, "ele") else None, None)
                      for p in _children(rte, "rtept")]
            out.append({"name": _first_text(rte, "name"), "sport": "", "points": [p for p in points if p[0] is not None]})
    return out


def _tcx(data):
    root = ElementTree.fromstring(data)
    out = []
    for act in root.iter():
        if _local(act.tag) != "Activity":
            continue
        points = []
        for tp in act.iter():
            if _local(tp.tag) != "Trackpoint":
                continue
            lat = lon = ele = time = None
            for c in tp.iter():
                name = _local(c.tag)
                if name == "LatitudeDegrees":
                    lat = _float(c.text)
                elif name == "LongitudeDegrees":
                    lon = _float(c.text)
                elif name == "AltitudeMeters":
                    ele = _float(c.text)
                elif name == "Time":
                    time = _parse_time(c.text)
            if lat is not None and lon is not None:
                points.append((lat, lon, ele, time))
        out.append({"name": _first_text(act, "Notes"), "sport": _sport(act.get("Sport")), "points": points})
    return out


def _fit(data):
    import fitdecode  # dépendance légère, seulement pour les fichiers Garmin et compatibles

    points, sport, sub_sport = [], "", ""
    semicircles = 180 / 2 ** 31
    with fitdecode.FitReader(io.BytesIO(data)) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "record":
                lat = frame.get_value("position_lat", fallback=None)
                lon = frame.get_value("position_long", fallback=None)
                if lat is None or lon is None:
                    continue
                ele = frame.get_value("enhanced_altitude", fallback=None)
                if ele is None:
                    ele = frame.get_value("altitude", fallback=None)
                time = frame.get_value("timestamp", fallback=None)
                if time is not None and time.tzinfo is None:
                    time = time.replace(tzinfo=dt_timezone.utc)
                points.append((lat * semicircles, lon * semicircles, ele, time))
            elif frame.name in ("sport", "session") and not sport:
                sport = str(frame.get_value("sport", fallback="") or "")
                sub_sport = str(frame.get_value("sub_sport", fallback="") or "")
    return [{"name": "", "sport": _sport(sub_sport, sport), "points": points}]


READERS = {".gpx": _gpx, ".tcx": _tcx, ".fit": _fit}


def _expand(name, data, depth=0):
    """(nom, contenu) de chaque fichier lisible, en ouvrant les archives ZIP et .gz."""
    suffix = PurePosixPath(name.lower()).suffix
    if suffix == ".gz":
        try:
            yield from _expand(name[:-3], gzip.decompress(data), depth)
        except OSError:
            pass
        return
    if suffix == ".zip" and depth < 2:
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return
        for info in archive.infolist()[:MAX_ARCHIVE_FILES]:
            if not info.is_dir():
                yield from _expand(info.filename, archive.read(info), depth + 1)
        return
    if suffix in READERS:
        yield name, data


# ---------- Calculs ----------

def _distance_m(a, b):
    r = 6371000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def _encode(points):
    """Polyligne encodée (format Google/Strava, précision 1e-5)."""
    out, plat, plng = [], 0, 0
    for lat, lng in points:
        a, b = round(lat * 1e5), round(lng * 1e5)
        for v in (a - plat, b - plng):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plng = a, b
    return "".join(out)


def _sample(items, n):
    if len(items) <= n:
        return list(items)
    step = (len(items) - 1) / (n - 1)
    return [items[round(i * step)] for i in range(n)]


def _meaningful(stem):
    """Un nom de fichier qui dit quelque chose (« Col_de_la_Forclaz »), pas « 12345678_ACTIVITY »."""
    words = re.sub(r"[\d_\-.\s]+", " ", stem.lower()).split()
    return bool([w for w in words if w not in {"activity", "activite", "track", "export", "garmin", "strava", "course"}])


PARIS = ZoneInfo("Europe/Paris")


def _local_time(start, lat, lng):
    """Heure locale approchée : un fichier donne l'heure UTC, Strava l'heure locale.

    En Europe de l'Ouest et centrale, l'heure de Paris (été compris) ; ailleurs, d'après la longitude.
    """
    if 34 <= lat <= 62 and -10 <= lng <= 20:
        return start.astimezone(PARIS).replace(tzinfo=None)
    return (start + timedelta(hours=round(lng / 15))).replace(tzinfo=None)


def summarize(track, filename=""):
    """Dictionnaire de champs `Activity` pour une trace, ou TrackError si elle n'a pas de tracé."""
    points = [p for p in track["points"] if -90 <= p[0] <= 90 and -180 <= p[1] <= 180]
    if len(points) < 2:
        raise TrackError("pas de tracé GPS")
    cumul, distance, gain, moving = [0.0], 0.0, 0.0, 0
    ref_ele = next((p[2] for p in points if p[2] is not None), None)
    for a, b in zip(points, points[1:]):
        d = _distance_m(a, b)
        distance += d
        cumul.append(distance)
        if b[2] is not None and ref_ele is not None:
            if b[2] - ref_ele >= ELEVATION_STEP:
                gain += b[2] - ref_ele
                ref_ele = b[2]
            elif ref_ele - b[2] >= ELEVATION_STEP:
                ref_ele = b[2]
        if a[3] and b[3]:
            dt = (b[3] - a[3]).total_seconds()
            if 0 < dt < 3600 and d / dt >= MOVING_SPEED:
                moving += dt
    times = [p[3] for p in points if p[3]]
    start = times[0] if times else None
    elapsed = int((times[-1] - times[0]).total_seconds()) if len(times) > 1 else 0
    if start is None:
        raise TrackError("pas d'heure de départ (itinéraire préparé, pas une activité enregistrée)")
    local = _local_time(start, points[0][0], points[0][1])
    sport = track.get("sport") or "Ride"
    stem = PurePosixPath(filename).stem if filename else ""
    name = (track.get("name") or "").strip() or f"{SPORT_NAMES.get(sport, 'Sortie')} du {local:%d/%m/%Y}"
    if not track.get("name") and _meaningful(stem):
        name = stem.replace("_", " ")[:120]
    profile = None
    if any(p[2] is not None for p in points):
        pairs = [(c, p[2]) for c, p in zip(cumul, points) if p[2] is not None]
        profile = [[round(c / 1000, 3), round(e, 1)] for c, e in _sample(pairs, PROFILE_POINTS)]
    trace = _sample([(p[0], p[1]) for p in points], MAX_TRACK_POINTS)
    return {
        "name": name[:255],
        "sport_type": sport,
        "start_date": start,
        "start_date_local": local.replace(tzinfo=dt_timezone.utc),
        "distance": round(distance, 1),
        "moving_time": int(moving) or elapsed,
        "elapsed_time": elapsed,
        "total_elevation_gain": round(gain, 1),
        "average_speed": round(distance / moving, 3) if moving else 0,
        "summary_polyline": _encode(trace),
        "start_lat": points[0][0],
        "start_lng": points[0][1],
        "profile": profile,
        "raw": {"source": "file", "file": filename[:200],
                "start_latlng": [round(points[0][0], 6), round(points[0][1], 6)],
                "end_latlng": [round(points[-1][0], 6), round(points[-1][1], 6)]},
    }


def read_files(files):
    """files : [(nom, contenu)]. Renvoie (activités lues, [(nom, raison)] des fichiers ignorés)."""
    found, skipped = [], []
    for name, data in files:
        expanded = list(_expand(name, data))
        if not expanded:
            skipped.append((name, "format non reconnu (GPX, FIT, TCX ou ZIP)"))
            continue
        for inner, content in expanded:
            reader = READERS[PurePosixPath(inner.lower()).suffix]
            try:
                tracks = reader(content)
            except Exception:  # fichier abîmé ou d'un autre format malgré son extension
                skipped.append((inner, "fichier illisible"))
                continue
            if not tracks:
                skipped.append((inner, "pas de tracé GPS"))
            for track in tracks:
                try:
                    found.append(summarize(track, inner))
                except TrackError as exc:
                    skipped.append((inner, str(exc)))
    return found, skipped
