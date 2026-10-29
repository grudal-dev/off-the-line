"""Familles de sports : libellé français et couleur de tracé."""

FAMILIES = {
    "run": ("Course", "#fc4c02"),
    "ride": ("Vélo", "#2f9bd6"),
    "walk": ("Marche et rando", "#5fae4e"),
    "snow": ("Glisse", "#9b7fd4"),
    "water": ("Eau", "#14a39b"),
    "other": ("Autres", "#8c9396"),
}

_FAMILY_OF = {
    "run": {"Run", "TrailRun", "VirtualRun"},
    "ride": {"Ride", "MountainBikeRide", "GravelRide", "EBikeRide", "EMountainBikeRide",
             "VirtualRide", "Velomobile", "Handcycle"},
    "walk": {"Walk", "Hike"},
    "snow": {"AlpineSki", "BackcountrySki", "NordicSki", "Snowboard", "Snowshoe", "IceSkate"},
    "water": {"Swim", "Rowing", "Kayaking", "Canoeing", "StandUpPaddling", "Surfing",
              "Kitesurf", "Windsurf", "Sail"},
}

LABELS = {
    "Run": "Course", "TrailRun": "Trail", "VirtualRun": "Course virtuelle",
    "Ride": "Vélo", "MountainBikeRide": "VTT", "GravelRide": "Gravel", "EBikeRide": "Vélo électrique",
    "EMountainBikeRide": "VTT électrique", "VirtualRide": "Vélo virtuel",
    "Walk": "Marche", "Hike": "Randonnée", "Swim": "Natation",
    "AlpineSki": "Ski alpin", "BackcountrySki": "Ski de rando", "NordicSki": "Ski de fond",
    "Snowboard": "Snowboard", "Snowshoe": "Raquettes", "Rowing": "Aviron", "Kayaking": "Kayak",
    "Canoeing": "Canoë", "StandUpPaddling": "Paddle", "WeightTraining": "Musculation",
    "Workout": "Entraînement", "Yoga": "Yoga",
}


def family(sport_type):
    for key, types in _FAMILY_OF.items():
        if sport_type in types:
            return key
    return "other"


def color(sport_type):
    return FAMILIES[family(sport_type)][1]


def label(sport_type):
    return LABELS.get(sport_type, sport_type or "Activité")


def uses_pace(sport_type):
    """Allure (min/km) pour la course et la marche, vitesse (km/h) sinon."""
    return family(sport_type) in ("run", "walk")
