"""Détection automatique des « périples » : des activités qui s'enchaînent.

Un périple, c'est un voyage en plusieurs étapes (itinérance à vélo, trek,
traversée à ski…). On le reconnaît à trois signes :

1. **Enchaînement dans le temps** : l'étape suivante démarre peu après la fin
   de la précédente (une nuit, éventuellement une journée de repos).
2. **Continuité dans l'espace** : elle démarre là où la précédente s'est arrêtée.
3. **Progression** : on ne repart pas sans cesse du même endroit. C'est ce qui
   distingue un périple d'une série de sorties en boucle depuis chez soi, où
   les points 1 et 2 sont aussi vrais (on finit chez soi, on repart de chez soi).

Certains voyages sont discontinus : on prend un train, un ferry, un bus entre
deux étapes. L'étape suivante repart alors loin de l'arrivée précédente. Un tel
« transfert » est accepté si le saut est physiquement plausible (vitesse
moyenne compatible avec un transport), s'il ne part ni n'arrive à un endroit
d'où l'on part souvent (chez soi : sinon chaque week-end en voiture deviendrait
un périple), s'il relie des étapes d'un point à un autre, et si le périple
compte au moins autant d'enchaînements « à pied/vélo » que de transferts.

L'algorithme relie chaque activité à sa meilleure suivante selon ces règles,
suit les chaînes ainsi formées, puis écarte les faux positifs :
boucles autour de la maison au début ou à la fin, allers-retours
domicile-travail, chaînes qui ne vont nulle part ou tiennent en une journée.
Tout est calculé à la volée, sans rien stocker : c'est rapide
(tri + parcours linéaire) et toujours à jour après une synchronisation.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from . import geo, polyline, sports

MAX_GAP = timedelta(hours=42)      # une nuit, ou une journée de repos entre deux étapes
OVERLAP = timedelta(minutes=10)    # tolérance sur les horloges des appareils
JOIN_KM = 5.0                      # l'étape suivante part à moins de 5 km de l'arrivée
SAME_PLACE_KM = 2.0                # en dessous, deux points sont « le même endroit »
MIN_SPREAD_KM = 10.0               # un périple doit emmener quelque part
MIN_DAYS = 2
TRANSFER_MAX_KM = 2000.0         # au-delà, ce n'est plus le même voyage
TRANSFER_MAX_SPEED = 900.0       # km/h, avion compris
HOME_MIN_STARTS = 5              # un lieu d'où partent 5 activités ou plus est « chez soi »

TRANSPORT_MODES = {
    "train": "Train", "ferry": "Ferry", "bus": "Bus", "car": "Voiture", "plane": "Avion", "other": "Transfert",
}


@dataclass(eq=False)
class Leg:
    activity: object
    start: tuple
    end: tuple
    start_at: object
    end_at: object
    start_name: str = None  # renseignés par places.name_journeys
    end_name: str = None
    custom_label: str = ""  # nom choisi par l'utilisateur

    @property
    def auto_label(self):
        """« Faverges → Albertville », « Autour de Moûtiers », ou None si lieux inconnus."""
        if self.start_name and self.end_name:
            if self.start_name == self.end_name:
                return f"Autour de {self.start_name}"
            return f"{self.start_name} → {self.end_name}"
        return None

    @property
    def label(self):
        return self.custom_label or self.auto_label

    @property
    def is_loop(self):
        return geo.distance_km(self.start, self.end) < SAME_PLACE_KM

    @property
    def day(self):
        return (self.activity.start_date_local or self.activity.start_date).date()


@dataclass
class Journey:
    legs: list
    nights: list = field(default_factory=list)  # index des étapes suivies d'une nuit
    transfers: list = field(default_factory=list)  # index des étapes suivies d'un transfert
    transfer_modes: dict = field(default_factory=dict)  # {index: "train"…} choisis par l'utilisateur
    start_name: str = None
    end_name: str = None
    custom_title: str = ""
    story: str = ""
    key: int = None       # identifiant stable : 1re activité de la chaîne détectée (avant corrections)
    hidden: bool = False  # l'utilisateur a indiqué que ce n'est pas un périple
    removed: list = field(default_factory=list)  # étapes détectées que l'utilisateur a retirées
    corrected: bool = False

    def transfer_after(self, i):
        """Le transfert qui suit l'étape i (ou None) : distance à vol d'oiseau, mode, lieux."""
        if i not in self.transfers:
            return None
        a, b = self.legs[i], self.legs[i + 1]
        mode = self.transfer_modes.get(i, "other")
        route = f"{a.end_name} → {b.start_name}" if a.end_name and b.start_name else None
        return {"index": i, "km": round(geo.distance_km(a.end, b.start)), "mode": mode,
                "mode_label": TRANSPORT_MODES.get(mode, "Transfert"), "route": route,
                "from": list(a.end), "to": list(b.start)}

    @property
    def transfer_list(self):
        return [self.transfer_after(i) for i in self.transfers]

    @property
    def place_name(self):
        start, end = self.start_name, self.end_name
        if start and end:
            back_home = start == end or geo.distance_km(self.legs[0].start, self.legs[-1].end) < JOIN_KM
            return f"Boucle depuis {start}" if back_home else f"{start} → {end}"
        if start:
            return f"Au départ de {start}"
        if end:
            return f"Jusqu'à {end}"
        return None

    @property
    def name(self):
        """Nom affiché : celui choisi par l'utilisateur, sinon les lieux, sinon le type de voyage."""
        return self.custom_title or self.place_name or self.title[:1].upper() + self.title[1:]

    @property
    def activities(self):
        return [leg.activity for leg in self.legs]

    @property
    def id(self):
        return self.key if self.key is not None else self.legs[0].activity.strava_id

    @property
    def first_day(self):
        return self.legs[0].day

    @property
    def last_day(self):
        return self.legs[-1].day

    @property
    def days(self):
        return (self.last_day - self.first_day).days + 1

    @property
    def distance_km(self):
        return round(sum(a.distance for a in self.activities) / 1000, 1)

    @property
    def elevation(self):
        return round(sum(a.total_elevation_gain for a in self.activities))

    @property
    def moving_time(self):
        return sum(a.moving_time for a in self.activities)

    @property
    def moving_display(self):
        h, rem = divmod(self.moving_time, 3600)
        return f"{h} h {rem // 60:02d}"

    @property
    def family(self):
        families = {sports.family(a.sport_type) for a in self.activities}
        return families.pop() if len(families) == 1 else "mixed"

    @property
    def title(self):
        how = {"ride": "à vélo", "run": "à pied", "walk": "à pied", "snow": "à ski",
               "water": "sur l'eau"}.get(self.family, "en itinérance")
        return f"{self.days} jours {how}"

    @property
    def color(self):
        return sports.FAMILIES[self.family][1] if self.family in sports.FAMILIES else "#fc4c02"


def _endpoints(activity):
    """Points de départ et d'arrivée, depuis les champs Strava ou à défaut le tracé."""
    raw = activity.raw or {}
    start = raw.get("start_latlng") or ([activity.start_lat, activity.start_lng]
                                        if activity.start_lat is not None else None)
    end = raw.get("end_latlng")
    if (not start or not end) and activity.summary_polyline:
        coords = polyline.decode(activity.summary_polyline)
        if coords:
            start, end = start or coords[0], end or coords[-1]
    if not start or not end or len(start) != 2 or len(end) != 2:
        return None
    return tuple(start), tuple(end)


def _legs(activities):
    legs = []
    for a in activities:
        raw = a.raw or {}
        if raw.get("commute") or raw.get("trainer") or a.sport_type.startswith("Virtual"):
            continue
        points = _endpoints(a)
        if not points:
            continue
        end_at = a.start_date + timedelta(seconds=a.elapsed_time or a.moving_time)
        legs.append(Leg(a, points[0], points[1], a.start_date, end_at))
    legs.sort(key=lambda leg: leg.start_at)
    return legs


def _home_places(legs):
    """Lieux d'où partent beaucoup d'activités : chez soi, le travail, le club…"""
    places = []  # [point, nombre de départs]
    for leg in legs:
        for entry in places:
            if geo.distance_km(entry[0], leg.start) < SAME_PLACE_KM:
                entry[1] += 1
                break
        else:
            places.append([leg.start, 1])
    return [p for p, n in places if n >= HOME_MIN_STARTS]


def _link(legs):
    """Associe à chaque étape sa suivante (au plus une), en un passage chronologique.

    Renvoie (suivantes, étapes ayant une précédente, étapes suivies d'un transfert).
    Un enchaînement continu est toujours préféré à un transfert.
    """
    homes = _home_places(legs)

    def at_home(point):
        return any(geo.distance_km(point, h) < SAME_PLACE_KM for h in homes)

    succ, has_pred, transfer = {}, set(), set()
    for i, a in enumerate(legs):
        jump = None
        for b in legs[i + 1:]:
            gap = b.start_at - a.end_at
            if gap > MAX_GAP:
                break  # triées par départ : les suivantes sont encore plus loin
            if gap < -OVERLAP or b in has_pred:
                continue
            distance = geo.distance_km(a.end, b.start)
            if distance <= JOIN_KM:
                # Progression : on repart d'ailleurs que du départ précédent,
                # sauf en cours de route (boucle depuis un gîte entre deux étapes).
                moved = geo.distance_km(a.start, b.start) >= SAME_PLACE_KM
                if moved or a in has_pred:
                    jump = None
                    succ[a] = b
                    break
                continue
            if jump is None and _plausible_transfer(a, b, distance, gap, a in has_pred, at_home):
                jump = b
        if a in succ:
            has_pred.add(succ[a])
        elif jump is not None:
            succ[a] = jump
            has_pred.add(jump)
            transfer.add(a)
    return succ, has_pred, transfer


def _plausible_transfer(a, b, distance, gap, a_in_chain, at_home):
    if distance > TRANSFER_MAX_KM:
        return False
    hours = max(gap.total_seconds() / 3600, 0.5)
    if distance / hours > TRANSFER_MAX_SPEED:
        return False
    if at_home(a.end) or at_home(b.start):
        return False
    if b.is_loop:
        return False
    return not a.is_loop or a_in_chain


def _places(points):
    """Regroupe des points proches en « lieux » distincts."""
    places = []
    for p in points:
        if not any(geo.distance_km(p, q) < SAME_PLACE_KM for q in places):
            places.append(p)
    return places


def _is_journey(chain, transfer):
    if len(chain) < 2:
        return False
    jumps = sum(1 for leg in chain[:-1] if leg in transfer)
    if jumps > (len(chain) - 1) - jumps:
        return False  # plus de transferts que d'enchaînements : une suite de sorties en déplacement
    if (chain[-1].day - chain[0].day).days + 1 < MIN_DAYS:
        return False
    origin = chain[0].start
    if max(geo.distance_km(origin, leg.end) for leg in chain) < MIN_SPREAD_KM:
        return False
    # Allers-retours entre deux lieux (trajets domicile-travail non marqués comme tels)
    moves = [leg for leg in chain if not leg.is_loop]
    places = _places([p for leg in moves for p in (leg.start, leg.end)])
    if len(places) <= 2 and len(moves) >= 3:
        return False
    return True


def detect(activities):
    """Renvoie les périples trouvés parmi `activities`, du plus récent au plus ancien."""
    legs = _legs(activities)
    succ, has_pred, transfer = _link(legs)
    journeys = []
    for head in legs:
        if head in has_pred:
            continue
        chain = [head]
        while chain[-1] in succ:
            chain.append(succ[chain[-1]])
        # Les boucles en début ou fin de chaîne sont des sorties autour de chez soi
        # (ou du dernier gîte) qui se sont accrochées au voyage : on les retire.
        while chain and chain[0].is_loop:
            chain.pop(0)
        while chain and chain[-1].is_loop:
            chain.pop()
        if _is_journey(chain, transfer):
            nights = [i for i in range(len(chain) - 1) if chain[i + 1].day != chain[i].day]
            jumps = [i for i in range(len(chain) - 1) if chain[i] in transfer]
            journeys.append(Journey(chain, nights, jumps, key=chain[0].activity.strava_id))
    journeys.sort(key=lambda j: j.legs[0].start_at, reverse=True)
    return journeys


def _breaks(legs):
    """Nuits et transferts d'une suite d'étapes (après corrections de l'utilisateur)."""
    nights = [i for i in range(len(legs) - 1) if legs[i + 1].day != legs[i].day]
    jumps = [i for i in range(len(legs) - 1) if geo.distance_km(legs[i].end, legs[i + 1].start) > JOIN_KM]
    return nights, jumps


def transfer_key(leg):
    """Clé d'un mode de transport en base : l'activité qui précède le transfert."""
    return f"a{leg.activity.strava_id}"


def _saved_modes(note, detected_legs):
    """{id activité: mode}, depuis le nouveau format (« a123 ») ou l'ancien (index dans la chaîne détectée)."""
    modes = {}
    for k, mode in (note.transfers or {}).items():
        k = str(k)
        if mode not in TRANSPORT_MODES:
            continue
        if k.startswith("a") and k[1:].isdigit():
            modes[int(k[1:])] = mode
        elif k.isdigit() and int(k) < len(detected_legs):
            modes.setdefault(detected_legs[int(k)].activity.strava_id, mode)
    return modes


def apply_note(journey, note, activities_by_id):
    """Applique titre, récit et corrections (étapes retirées, ajoutées, renommées) d'un JourneyNote."""
    journey.custom_title, journey.story, journey.hidden = note.title, note.story, note.hidden
    modes = _saved_modes(note, journey.legs)

    excluded = {int(x) for x in note.excluded or []}
    added = [int(x) for x in note.added or [] if int(x) in activities_by_id]
    if excluded or added:
        kept = [leg for leg in journey.legs if leg.activity.strava_id not in excluded]
        present = {leg.activity.strava_id for leg in kept}
        extra = _legs([activities_by_id[x] for x in dict.fromkeys(added) if x not in present and x not in excluded])
        legs = sorted(kept + extra, key=lambda leg: leg.start_at)
        if legs:  # un périple garde toujours au moins une étape
            journey.removed = [leg for leg in journey.legs if leg.activity.strava_id in excluded]
            journey.legs = legs
            journey.nights, journey.transfers = _breaks(legs)
            journey.corrected = True

    names = note.stage_names or {}
    for leg in journey.legs + journey.removed:
        leg.custom_label = names.get(str(leg.activity.strava_id), "")
    journey.corrected = journey.corrected or bool(names)
    journey.transfer_modes = {i: modes[journey.legs[i].activity.strava_id] for i in journey.transfers
                              if journey.legs[i].activity.strava_id in modes}
    return journey


def for_account(account, budget=None, include_hidden=True):
    """Périples d'un compte, avec titres/récits, corrections saisies et noms de lieux (voir places.py)."""
    from . import places
    from .models import JourneyNote

    activities = list(_queryset(account))
    found = detect(activities)
    by_id = {a.strava_id: a for a in activities}
    notes = {n.journey_id: n for n in JourneyNote.objects.filter(account=account)}
    for j in found:
        note = notes.get(j.id)
        if note:
            apply_note(j, note, by_id)
    if not include_hidden:
        found = [j for j in found if not j.hidden]
    places.name_journeys(found, budget)
    for j in found:  # les étapes retirées restent nommées (cache seulement) pour pouvoir les remettre
        places.name_journeys([Journey(j.removed)] if j.removed else [], None)
    return found


def _queryset(account):
    return account.activities.exclude(summary_polyline="").only(
        "strava_id", "name", "sport_type", "start_date", "start_date_local", "distance",
        "moving_time", "elapsed_time", "total_elevation_gain", "summary_polyline",
        "start_lat", "start_lng", "raw", "description", "profile", "details_fetched_at",
    ).prefetch_related("photos")
